"""Decision D1 and D2 (playtest #25, #29): distance, scouting, bodyguards, unlisted places."""

import pytest
from pydantic import ValidationError

from revisionist.core.fold import replay
from revisionist.core.ids import EntityId, FactId, IntentId
from revisionist.core.intent import Intent, Verb
from revisionist.core.ontology import Entity, EntityKind, Fact
from revisionist.core.referee import rate
from revisionist.core.resolution import FeasibilityBand
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC, private_to
from tests import fixtures as fx

L_NORTH_FORD = EntityId("01ARZ3NDEKTSV4RRFFQ69L0001")
L_SOUTH_HILLS = EntityId("01ARZ3NDEKTSV4RRFFQ69L0002")


@pytest.fixture
def state() -> State:
    return replay(fx.creation_events())


def _attack(target: EntityId, **extra: object) -> Intent:
    return Intent(
        intent_id=IntentId("01ARZ3NDEKTSV4RRFFQ69I00001"),
        actor=fx.NORTH,
        verb=Verb.ATTACK,
        targets=(target,),
        means=(fx.A_NORTH_ARMY,),
        scope=PUBLIC,
        filed_tick=0,
        **extra,
    )


def _placed(state: State, army_at: EntityId, aide_at: EntityId) -> State:
    places = {
        L_NORTH_FORD: Entity(entity_id=L_NORTH_FORD, kind=EntityKind.LOCATION, name="Ford"),
        L_SOUTH_HILLS: Entity(entity_id=L_SOUTH_HILLS, kind=EntityKind.LOCATION, name="Hills"),
    }
    aide = state.entities[fx.E_SOUTH_AIDE].model_copy(update={"location": aide_at})
    army = state.assets[fx.A_NORTH_ARMY].model_copy(update={"location": army_at})
    return state.model_copy(
        update={
            "entities": {**state.entities, **places, fx.E_SOUTH_AIDE: aide},
            "assets": {**state.assets, fx.A_NORTH_ARMY: army},
        }
    )


def test_a_person_counts_as_guarded(state: State) -> None:
    """The same army against an unarmed org and an unarmed person: the person is harder."""
    assert rate(state, _attack(fx.E_HERALD)) is FeasibilityBand.ASSURED
    assert rate(state, _attack(fx.E_SOUTH_AIDE)) is FeasibilityBand.LIKELY


def test_troops_elsewhere_cost_a_band(state: State) -> None:
    here = _placed(state, army_at=L_SOUTH_HILLS, aide_at=L_SOUTH_HILLS)
    away = _placed(state, army_at=L_NORTH_FORD, aide_at=L_SOUTH_HILLS)
    assert rate(here, _attack(fx.E_SOUTH_AIDE)) is FeasibilityBand.LIKELY
    assert rate(away, _attack(fx.E_SOUTH_AIDE)) is FeasibilityBand.CONTESTED


def test_a_private_fact_about_the_target_is_scouting(state: State) -> None:
    scouted = Fact(
        fact_id=FactId("01ARZ3NDEKTSV4RRFFQ69F0099"),
        subject=fx.E_SOUTH_AIDE,
        predicate="sleeps_at",
        obj=fx.E_SOUTH,
        scope=private_to(fx.NORTH),
    )
    seen = state.model_copy(update={"facts": {**state.facts, scouted.fact_id: scouted}})
    assert rate(state, _attack(fx.E_SOUTH_AIDE)) is FeasibilityBand.LIKELY
    assert rate(seen, _attack(fx.E_SOUTH_AIDE)) is FeasibilityBand.ASSURED


def test_a_public_fact_is_not_scouting(state: State) -> None:
    known = Fact(
        fact_id=FactId("01ARZ3NDEKTSV4RRFFQ69F0098"),
        subject=fx.E_SOUTH_AIDE,
        predicate="sleeps_at",
        obj=fx.E_SOUTH,
        scope=PUBLIC,
    )
    seen = state.model_copy(update={"facts": {**state.facts, known.fact_id: known}})
    assert rate(seen, _attack(fx.E_SOUTH_AIDE)) is FeasibilityBand.LIKELY


def test_a_place_the_world_left_out_is_a_long_shot_at_best(state: State) -> None:
    easy = _attack(fx.E_HERALD)
    assert rate(state, easy) is FeasibilityBand.ASSURED
    far = _attack(fx.E_HERALD, destination="Madagascar")
    assert rate(state, far) is FeasibilityBand.LONG_SHOT


@pytest.mark.parametrize(
    "name", ["Madagascar", "Fort Wayne", "St. Mary's River", "Cape of Good Hope"]
)
def test_destination_accepts_a_place_name(name: str) -> None:
    assert _attack(fx.E_HERALD, destination=name).destination == name


@pytest.mark.parametrize(
    "bad",
    ["march on the capital and burn it", "Grid 42", " Leading space", "", "x" * 41],
)
def test_destination_rejects_what_is_not_a_short_name(bad: str) -> None:
    with pytest.raises(ValidationError):
        _attack(fx.E_HERALD, destination=bad)
