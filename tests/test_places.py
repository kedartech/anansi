"""Places on demand (#29, spec §5.2): a real place the world lacks is admitted before filing."""

from typing import Any

import pytest

from revisionist.core import events as ev
from revisionist.core import places
from revisionist.core.fold import replay
from revisionist.core.ids import EntityId, derived_id
from revisionist.core.intent import Intent
from revisionist.core.ontology import Entity, EntityKind
from revisionist.core.resolution import RejectionReason
from revisionist.core.state import State
from revisionist.engine.agent import Agent, receive_order
from revisionist.engine.llm import Reply, Request, TransportError
from revisionist.engine.store import MemoryStore, load_state
from tests import fixtures as fx
from tests.test_agent import FakeTransport

VELLMARA = "Vellmara"
REFUSAL = "I could not place Atlantis at this date."


def _location(entity_id: str, name: str) -> ev.EntityCreated:
    return ev.EntityCreated(
        entity=Entity(entity_id=EntityId(entity_id), kind=EntityKind.LOCATION, name=name)
    )


@pytest.fixture
def state() -> State:
    return replay(fx.creation_events())


def _world_with_location(name: str) -> State:
    base = fx.creation_events()
    extra = fx.envelope(
        len(base), _location(derived_id(fx.CAMPAIGN, "place", name.casefold()), name)
    )
    return replay([*base, extra])


def _locations_created(store: MemoryStore) -> list[Entity]:
    return [
        e.payload.entity
        for e in store.read(fx.CAMPAIGN)
        if isinstance(e.payload, ev.EntityCreated) and e.payload.entity.kind is EntityKind.LOCATION
    ]


# --- pure: missing and admit ---------------------------------------------------


def test_missing_keeps_order_and_drops_known_places_and_duplicates() -> None:
    state = _world_with_location("Vellmara")
    names = ("  vellmara ", "Ostrava", "", "ostrava", "Brenn")
    assert places.missing(state, names) == ("Ostrava", "Brenn")


def test_missing_counts_only_locations_as_places(state: State) -> None:
    assert places.missing(state, ("North",)) == ("North",)


def test_admit_builds_a_public_location_under_its_polity(state: State) -> None:
    entity = places.admit(state, VELLMARA, fx.E_NORTH)
    assert entity.kind is EntityKind.LOCATION
    assert entity.name == VELLMARA
    assert entity.parent == fx.E_NORTH
    assert entity.entity_id == derived_id(fx.CAMPAIGN, "place", "vellmara")


def test_admit_without_a_polity_leaves_the_parent_empty(state: State) -> None:
    assert places.admit(state, VELLMARA, None).parent is None


def test_admit_refuses_a_place_the_world_already_holds() -> None:
    state = _world_with_location("Vellmara")
    with pytest.raises(ValueError):
        places.admit(state, "vellmara", None)


def test_admit_refuses_an_id_already_in_use(state: State) -> None:
    taken = fx.envelope(
        len(fx.creation_events()),
        _location(derived_id(fx.CAMPAIGN, "place", "ostrava"), "Something Else"),
    )
    with pytest.raises(ValueError):
        places.admit(replay([*fx.creation_events(), taken]), "Ostrava", None)


@pytest.mark.parametrize("polity", [EntityId("01ARZ3NDEKTSV4RRFFQ69G5ZZ9"), fx.E_HERALD])
def test_admit_refuses_a_parent_that_is_not_a_polity_or_faction(
    state: State, polity: EntityId
) -> None:
    with pytest.raises(ValueError):
        places.admit(state, VELLMARA, polity)


# --- engine: receive_order admits a real place --------------------------------


def _order(destination: str) -> dict[str, Any]:
    return {"kind": "intent", "intent": {"verb": "declare", "destination": destination}}


def _real(name: str = VELLMARA, polity: str = "north") -> dict[str, Any]:
    return {"real": True, "name": name, "polity": polity}


def test_order_to_a_real_missing_place_admits_it_and_files_against_it(state: State) -> None:
    store = MemoryStore()
    store.append_all(fx.creation_events())
    t = FakeTransport([_order(VELLMARA), _real()])

    result = receive_order(store, state, fx.NORTH, "declare at Vellmara", t)

    assert result.rejection is None
    assert [r.call_class for r in t.requests] == ["structure", "place_check"]
    (place,) = _locations_created(store)
    assert place.name == VELLMARA and place.parent == fx.E_NORTH
    assert isinstance(result.outcome, Intent)
    assert place.entity_id in result.outcome.targets
    assert result.outcome.destination is None
    filed = [e.payload for e in store.read(fx.CAMPAIGN) if isinstance(e.payload, ev.IntentFiled)]
    assert len(filed) == 1 and place.entity_id in filed[0].intent.targets
    assert filed[0].intent.destination is None


def test_admitted_place_is_visible_to_every_seat(state: State) -> None:
    store = MemoryStore()
    store.append_all(fx.creation_events())
    receive_order(
        store, state, fx.NORTH, "declare at Vellmara", FakeTransport([_order(VELLMARA), _real()])
    )
    (place,) = _locations_created(store)
    current = load_state(store, fx.CAMPAIGN)
    for seat in (fx.NORTH, fx.SOUTH):
        assert place.entity_id in Agent.for_seat(current, seat, FakeTransport([])).view.entities


def test_place_check_prompt_names_the_place_the_date_and_the_polities(state: State) -> None:
    store = MemoryStore()
    store.append_all(fx.creation_events())
    t = FakeTransport([_order(VELLMARA), _real()])
    receive_order(store, state, fx.NORTH, "declare at Vellmara", t)
    prompt = t.requests[1].prompt
    assert VELLMARA in prompt and "unknown" in prompt
    assert "North" in prompt and "South" in prompt


def test_unreal_place_is_refused_and_nothing_is_filed(state: State) -> None:
    store = MemoryStore()
    store.append_all(fx.creation_events())
    t = FakeTransport([_order("Atlantis"), {"real": False, "name": "", "polity": ""}])

    result = receive_order(store, state, fx.NORTH, "declare at Atlantis", t)

    assert result.rejection is not None
    assert result.rejection.reason is RejectionReason.TARGET_UNKNOWN
    assert result.rejection.detail == REFUSAL
    assert _locations_created(store) == []
    kinds = {e.kind for e in store.read(fx.CAMPAIGN)}
    assert "intent_filed" not in kinds and "intent_rejected" not in kinds


@pytest.mark.parametrize(
    "reply",
    [
        "yes",
        None,
        {"name": VELLMARA, "polity": "north"},
        {"real": "yes", "name": VELLMARA, "polity": "north"},
    ],
)
def test_malformed_place_check_reply_is_refused(state: State, reply: Any) -> None:
    store = MemoryStore()
    store.append_all(fx.creation_events())
    t = FakeTransport([_order(VELLMARA), reply])

    result = receive_order(store, state, fx.NORTH, "declare at Vellmara", t)

    assert (
        result.rejection is not None
        and result.rejection.detail == f"I could not place {VELLMARA} at this date."
    )
    assert _locations_created(store) == []
    assert "intent_filed" not in {e.kind for e in store.read(fx.CAMPAIGN)}


class _Unreachable(FakeTransport):
    def call(self, request: Request) -> Reply:
        if request.call_class == "place_check":
            raise TransportError("no route")
        return super().call(request)


def test_transport_error_on_place_check_refuses_the_order(state: State) -> None:
    store = MemoryStore()
    store.append_all(fx.creation_events())
    t = _Unreachable([_order(VELLMARA)])
    result = receive_order(store, state, fx.NORTH, "declare at Vellmara", t)
    assert (
        result.rejection is not None
        and result.rejection.detail == f"I could not place {VELLMARA} at this date."
    )
    assert _locations_created(store) == []


def test_place_already_in_the_world_makes_no_extra_call() -> None:
    state = _world_with_location(VELLMARA)
    store = MemoryStore()
    store.append_all(fx.creation_events())
    store.append(
        fx.envelope(
            len(fx.creation_events()),
            _location(derived_id(fx.CAMPAIGN, "place", "vellmara"), VELLMARA),
        )
    )
    t = FakeTransport([_order(VELLMARA)])

    result = receive_order(store, state, fx.NORTH, "declare at Vellmara", t)

    assert len(t.requests) == 1 and result.rejection is None
    assert len(_locations_created(store)) == 1
