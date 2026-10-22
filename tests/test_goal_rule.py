"""Session Zero v3, Phase 2: the goal rule is a warning, never a block (spec §5.6)."""

from pathlib import Path
from typing import Any

import yaml

from revisionist.core import goals as g
from revisionist.core.fold import replay
from revisionist.core.ids import AssetId, EntityId
from revisionist.core.ontology import EntityStatus, ScheduleSource
from revisionist.core.state import State
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import compile_world
from tests import fixtures as fx

SALT_STRAIT = Path(__file__).resolve().parents[1] / "worlds" / "salt-strait.yaml"
WARNING = (
    "This goal ties to none of the world's contests or coming events. "
    "You can keep it, but it may sit outside the main action."
)
WAR: dict[str, Any] = {
    "key": "war",
    "kind": "rivalry",
    "name": "War",
    "prize": "the strait",
    "sides": ["varda", "ostrel"],
    "momentum": -2,
    "countdown_days": 30,
}
TREATY: dict[str, Any] = {
    "key": "treaty",
    "name": "Treaty of Harrow",
    "on_date": "1500-04-01",
    "condition": {"check": "entity_is", "entity": "abbess_ren", "status": "active"},
    "weight": 2,
}


def _world(contests: list[dict[str, Any]], schedule: list[dict[str, Any]]) -> State:
    raw: dict[str, Any] = yaml.safe_load(SALT_STRAIT.read_text())
    raw["start_date"] = "1500-03-01"
    raw["contests"] = contests
    raw["schedule"] = schedule
    return replay(compile_world(WorldFile.model_validate(raw), fx.CAMPAIGN, fx.VERSIONS))


def _entity(state: State, name: str) -> EntityId:
    return next(e.entity_id for e in state.entities.values() if e.name == name)


def _asset(state: State, name: str) -> AssetId:
    return next(a.asset_id for a in state.assets.values() if a.name == name)


def test_no_pressures_means_no_warning() -> None:
    state = _world([], [])
    goal = g.EntityIs(entity=_entity(state, "Admiral Quell"), status=EntityStatus.DEAD)
    assert g.pressure_warning(state, next(iter(state.seats)), goal) is None


def test_a_goal_on_a_contest_side_is_not_warned() -> None:
    state = _world([WAR], [])
    seat = next(iter(state.seats))
    varda = _entity(state, "Varda")
    guard = _asset(state, "Varda Harbour Guard")
    assert g.pressure_warning(state, seat, g.ControlsAsset(holder=varda, asset=guard)) is None
    aske = g.EntityIs(entity=_entity(state, "General Aske"), status=EntityStatus.DEAD)
    assert g.pressure_warning(state, seat, aske) is None


def test_a_goal_on_an_entity_a_history_event_names_is_not_warned() -> None:
    state = _world([WAR], [])
    goal = g.EntityIs(entity=_entity(state, "Abbess Ren"), status=EntityStatus.DEAD)
    seat = next(iter(state.seats))
    assert g.pressure_warning(state, seat, goal) == WARNING

    with_history = _world([WAR], [TREATY])
    seat = next(iter(with_history.seats))
    goal = g.EntityIs(entity=_entity(with_history, "Abbess Ren"), status=EntityStatus.DEAD)
    assert g.pressure_warning(with_history, seat, goal) is None


def test_an_unrelated_goal_gets_the_warning() -> None:
    state = _world([WAR], [TREATY])
    seat = next(iter(state.seats))
    harbours = _asset(state, "Free Harbours")
    lune = _entity(state, "Lune Isles")
    goal = g.ControlsAsset(holder=lune, asset=harbours)
    assert g.pressure_warning(state, seat, goal) == WARNING


def test_a_coming_history_event_alone_is_a_pressure() -> None:
    state = _world([], [TREATY])
    seat = next(iter(state.seats))
    goal = g.ControlsAsset(
        holder=_entity(state, "Lune Isles"), asset=_asset(state, "Free Harbours")
    )
    assert g.pressure_warning(state, seat, goal) == WARNING


def test_a_decided_contest_no_longer_counts() -> None:
    state = _world([WAR], [])
    seat = next(iter(state.seats))
    varda = _entity(state, "Varda")
    goal = g.ControlsAsset(holder=varda, asset=_asset(state, "Varda Harbour Guard"))
    assert g.pressure_warning(state, seat, goal) is None

    decided = state.model_copy(
        update={
            "contests": {
                k: c.model_copy(update={"decided": True, "winner": varda})
                for k, c in state.contests.items()
            }
        }
    )
    assert g.pressure_warning(decided, seat, goal) == WARNING


def test_a_fired_history_event_no_longer_counts() -> None:
    state = _world([], [TREATY])
    seat = next(iter(state.seats))
    goal = g.EntityIs(entity=_entity(state, "Abbess Ren"), status=EntityStatus.DEAD)
    assert g.pressure_warning(state, seat, goal) is None

    fired = state.model_copy(
        update={
            "scheduled": {
                k: ev.model_copy(update={"fired": True})
                if ev.source is ScheduleSource.HISTORY
                else ev
                for k, ev in state.scheduled.items()
            }
        }
    )
    assert g.pressure_warning(fired, seat, goal) == WARNING
