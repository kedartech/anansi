"""A generated cast (realism, Batch 5): the cast compiles with a roster and no cards,
each card is built when its person is picked, and the rest are built afterwards.
Replayed from the hand-written 1962 recording; no model is called."""

import json
from typing import Any

import pytest

from revisionist.core import events as ev
from revisionist.core.fold import state_hash
from revisionist.core.ids import CampaignId, SeatId, derived_id
from revisionist.core.realism.quantity import RESOLVER_KEYS
from revisionist.core.report import leak_report
from revisionist.core.world import CardDecl, WorldFile
from revisionist.core.world_compile import WorldError, compile_figure, compile_world
from revisionist.engine import build, session_zero
from revisionist.engine import campaign as cmp
from revisionist.engine.cast import draft_cast
from revisionist.engine.llm import RecordedTransport, Reply, Request, TransportError
from revisionist.engine.pitch import read_pitch
from revisionist.engine.store import MemoryStore, StoreError, load_state
from tests.cast_scenario import (
    BUILD_BY_NAME,
    CAST_REPLY,
    KENNEDY_INTEREST,
    KENNEDY_VOICE,
    PITCH,
    opened,
    person,
    scenario,
)
from tests.handwritten import CAST


class Capturing:
    def __init__(self) -> None:
        self.inner = RecordedTransport(CAST)
        self.sent: list[Request] = []

    def call(self, request: Request) -> Reply:
        self.sent.append(request)
        return self.inner.call(request)


class Fixed:
    """Answers every request with ``output``: a reply the recording does not hold."""

    def __init__(self, output: Any) -> None:
        self.output = output
        self.sent: list[Request] = []

    def call(self, request: Request) -> Reply:
        self.sent.append(request)
        return Reply(
            output=self.output,
            model=request.model,
            input_tokens=10,
            output_tokens=10,
            cached_tokens=0,
            usd=0.0,
        )


def test_the_cast_compiles_with_a_roster_and_no_cards() -> None:
    store, transport = MemoryStore(), Capturing()
    cid = opened(store, transport)
    state = load_state(store, cid)
    assert state.ruleset == "realism" and state.phase.value == "session_zero"
    assert sorted(state.entities[c].name for c in state.roster) == [
        "Fidel Castro",
        "John F. Kennedy",
        "Nikita Khrushchev",
        "Robert McNamara",
    ]
    assert state.cards == {}
    cast = transport.sent[-1]
    assert (cast.call_class, cast.model) == ("cast", "claude-haiku-5-5")


def test_cast_refuses_a_short_roster_and_engine_words_then_gives_up() -> None:
    card, _ = read_pitch(PITCH, RecordedTransport(CAST))
    bad = {**CAST_REPLY, "roster": CAST_REPLY["roster"][:2]}
    bad["entities"] = [
        {**e, "name": "Fidel_Castro"} if e["key"] == "castro" else e for e in bad["entities"]
    ]
    transport = Fixed(bad)
    with pytest.raises(session_zero.SessionZeroError) as caught:
        draft_cast(card, card.start_points[0], transport)
    assert any("roster: 2 people" in p for p in caught.value.problems)
    assert any("castro" in p for p in caught.value.problems)
    assert len(transport.sent) == 2 and "was refused" in transport.sent[1].prompt


def test_claim_refuses_a_cast_member_whose_card_is_not_built() -> None:
    store = MemoryStore()
    cid = opened(store, RecordedTransport(CAST))
    with pytest.raises(session_zero.StepRefusedError, match="not ready"):
        session_zero.claim(store, cid, SeatId("01ARZ3NDEKTSV4RRFFQ69G5FAV"), "Fidel Castro")


def test_a_picked_figure_is_built_in_one_batch() -> None:
    store, transport = MemoryStore(), Capturing()
    cid = opened(store, transport)
    kennedy = person(store, cid, "John F. Kennedy")
    before = load_state(store, cid).last_seq
    assert build.build_figure(store, cid, kennedy, transport)
    req = transport.sent[-1]
    assert (req.call_class, req.model) == ("build", "claude-sonnet-5-5")
    state = load_state(store, cid)
    card = state.cards[kennedy]
    assert card.playable and 2 <= len(card.levers) <= 6
    assert all(lv.resolver in RESOLVER_KEYS for lv in card.levers)
    assert {state.entities[c.person].name for c in card.contacts} == {
        "Robert McNamara",
        "Dean Rusk",
        "Nikita Khrushchev",
    }
    log = list(store.read(cid))[before + 1 :]
    *built, cost = log
    assert isinstance(built[-1].payload, ev.CardsDeclared)
    assert [e.event_id for e in built] == [
        derived_id(cid, "figure", kennedy, n) for n in range(len(built))
    ]
    assert isinstance(cost.payload, ev.CostLogged) and cost.payload.purpose == "build"
    # Built already: nothing is asked and nothing written.
    calls = len(transport.sent)
    assert not build.build_figure(store, cid, kennedy, transport)
    assert len(transport.sent) == calls and load_state(store, cid).last_seq == state.last_seq


def test_a_second_card_for_one_person_is_refused() -> None:
    store = MemoryStore()
    cid = opened(store, RecordedTransport(CAST))
    kennedy = person(store, cid, "John F. Kennedy")
    state = load_state(store, cid)
    known = build.known_keys(state)
    part = WorldFile.model_validate(
        {"world": "figure", "name": "figure", **BUILD_BY_NAME["John F. Kennedy"]}
    )
    first = compile_figure(state, kennedy, part, known, cmp.versions())
    raced = compile_figure(state, kennedy, part, known, cmp.versions())
    assert [e.event_id for e in raced] == [e.event_id for e in first]
    store.append_all(first)
    with pytest.raises(StoreError):
        store.append_all(raced)
    with pytest.raises(WorldError, match="already in the world"):
        compile_figure(load_state(store, cid), kennedy, part, known, cmp.versions())


def test_a_card_may_not_redeclare_what_the_world_holds() -> None:
    store = MemoryStore()
    cid = opened(store, RecordedTransport(CAST))
    state = load_state(store, cid)
    kennedy = person(store, cid, "John F. Kennedy")
    raw = BUILD_BY_NAME["John F. Kennedy"]
    twin = {"key": "castro2", "kind": "person", "name": "Fidel Castro", "parent": "cuba"}
    again = {"key": "cuba", "kind": "polity", "name": "Cuba"}
    for extra, said in ((twin, "already in the world as fidel_castro"), (again, "duplicate key")):
        part = WorldFile.model_validate(
            {"world": "figure", "name": "figure", **raw, "entities": [*raw["entities"], extra]}
        )
        with pytest.raises(WorldError, match=said):
            compile_figure(state, kennedy, part, build.known_keys(state), cmp.versions())
    two = WorldFile.model_validate(
        {"world": "figure", "name": "figure", **raw, "cards": raw["cards"] * 2}
    )
    with pytest.raises(WorldError, match="exactly one card"):
        compile_figure(state, kennedy, two, build.known_keys(state), cmp.versions())


def test_a_card_that_never_fits_writes_nothing_but_its_cost() -> None:
    store = MemoryStore()
    cid = opened(store, RecordedTransport(CAST))
    raw = BUILD_BY_NAME["Fidel Castro"]
    card = {**raw["cards"][0], "levers": raw["cards"][0]["levers"][:1]}
    transport = Fixed({**raw, "cards": [card]})
    before = load_state(store, cid)
    with pytest.raises(build.BuildError, match="1 levers"):
        build.build_figure(store, cid, person(store, cid, "Fidel Castro"), transport)
    assert len(transport.sent) == 2 and "was refused" in transport.sent[1].prompt
    after = list(store.read(cid))[before.last_seq + 1 :]
    assert [e.payload.kind for e in after] == ["cost_logged"]


def test_the_rest_are_built_afterwards_and_a_failure_is_named_not_raised() -> None:
    store = MemoryStore()
    cid = scenario(store, RecordedTransport(CAST))
    state = load_state(store, cid)
    assert set(state.cards) == set(state.roster)
    assert all(c.playable for c in state.cards.values())

    class Down:
        def call(self, request: Request) -> Reply:
            raise TransportError("model unavailable")

    fresh = MemoryStore()
    cid = opened(fresh, RecordedTransport(CAST))
    assert build.build_rest(fresh, cid, Down()) == [
        "Fidel Castro",
        "John F. Kennedy",
        "Nikita Khrushchev",
        "Robert McNamara",
    ]
    assert load_state(fresh, cid).cards == {}


def test_a_build_never_reads_another_cards_policy() -> None:
    store, transport = MemoryStore(), Capturing()
    cid = scenario(store, transport)
    builds = [r for r in transport.sent if r.call_class == "build"]
    assert len(builds) == 4
    assert all(KENNEDY_VOICE not in r.prompt for r in transport.sent)
    later = builds[1:]
    for req in later:
        assert KENNEDY_INTEREST not in req.prompt and "loyalty" not in req.prompt
    for req in transport.sent:
        assert cid not in req.system + req.prompt + json.dumps(req.schema)
    state = load_state(store, cid)
    assert leak_report(state, store.read(cid)) == []


def test_a_listed_person_without_a_card_compiles_but_an_unplayable_card_does_not() -> None:
    world = WorldFile.model_validate(
        {
            "world": "t",
            "name": "T",
            "ruleset": "realism",
            "start_date": "1962-10-16",
            **{k: v for k, v in CAST_REPLY.items() if k != "name"},
        }
    )
    cid = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FAV")
    compile_world(world, cid, cmp.versions())
    bare = CardDecl(person="kennedy", office="President of the United States", horizon_days=7)
    with pytest.raises(WorldError, match="lists only playable cards"):
        compile_world(world.model_copy(update={"cards": (bare,)}), cid, cmp.versions())


def test_replaying_a_built_cast_gives_the_same_state() -> None:
    a, b = MemoryStore(), MemoryStore()
    ca = scenario(a, RecordedTransport(CAST))
    cb = scenario(b, RecordedTransport(CAST))
    sa, sb = load_state(a, ca), load_state(b, cb)
    assert len(sa.cards) == len(sb.cards) == 4
    assert state_hash(load_state(a, ca)) == state_hash(sa)
