"""Layer 3: an order that bears on a contest rates by strength against strength on it."""

from pathlib import Path

import pytest
import yaml

from revisionist.core import contest
from revisionist.core.fold import replay
from revisionist.core.ids import AssetId, EntityId, IntentId, SeatId
from revisionist.core.intent import Intent, Verb
from revisionist.core.referee import rate
from revisionist.core.resolution import FeasibilityBand
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import compile_world, resolve_keys
from tests import fixtures as fx

SALT_STRAIT = Path(__file__).resolve().parents[1] / "worlds" / "salt-strait.yaml"


@pytest.fixture(scope="module")
def world() -> tuple[State, dict[str, str]]:
    """Salt Strait with one rivalry between Varda (strength 3) and Ostrel (strength 6)."""
    raw: dict[str, object] = yaml.safe_load(SALT_STRAIT.read_text())
    raw["start_date"] = "1500-03-01"
    raw["contests"] = [
        {
            "key": "war",
            "kind": "rivalry",
            "name": "War",
            "prize": "the strait",
            "sides": ["varda", "ostrel"],
            "momentum": 0,
            "countdown_days": 30,
        }
    ]
    wf = WorldFile.model_validate(raw)
    state = replay(compile_world(wf, fx.CAMPAIGN, fx.VERSIONS))
    return state, resolve_keys(wf, fx.CAMPAIGN)


def _bare(state: State) -> State:
    return state.model_copy(update={"contests": {}})


def _order(
    ids: dict[str, str],
    verb: Verb,
    seat: str,
    *,
    targets: tuple[str, ...] = (),
    means: tuple[str, ...] = (),
    destination: str | None = None,
) -> Intent:
    return Intent(
        intent_id=IntentId("01ARZ3NDEKTSV4RRFFQ69I00001"),
        actor=SeatId(ids[seat]),
        verb=verb,
        targets=tuple(EntityId(ids[t]) for t in targets),
        means=tuple(AssetId(ids[m]) for m in means),
        destination=destination,
        scope=PUBLIC,
        filed_tick=0,
    )


def test_the_edge_in_this_world_favours_ostrel(world: tuple[State, dict[str, str]]) -> None:
    state, ids = world
    assert (
        contest.edge(state, _order(ids, Verb.CONCEAL, "ostrel_seat", means=("ostrel_army",))) == 1
    )
    assert (
        contest.edge(state, _order(ids, Verb.DEFEND, "varda_seat", means=("varda_treasury",))) == -1
    )


def test_an_attack_by_the_stronger_side_rates_higher(world: tuple[State, dict[str, str]]) -> None:
    state, ids = world
    attack = _order(ids, Verb.ATTACK, "ostrel_seat", targets=("varda",), means=("ostrel_granary",))
    assert rate(_bare(state), attack) is FeasibilityBand.LONG_SHOT
    assert rate(state, attack) is FeasibilityBand.CONTESTED


def test_an_order_by_the_weaker_side_rates_lower(world: tuple[State, dict[str, str]]) -> None:
    state, ids = world
    defend = _order(ids, Verb.DEFEND, "varda_seat", targets=("varda",), means=("varda_treasury",))
    assert rate(_bare(state), defend) is FeasibilityBand.CONTESTED
    assert rate(state, defend) is FeasibilityBand.LONG_SHOT


def test_an_edge_never_exceeds_assured(world: tuple[State, dict[str, str]]) -> None:
    state, ids = world
    strong = _order(ids, Verb.ATTACK, "ostrel_seat", targets=("varda",), means=("ostrel_army",))
    assert rate(_bare(state), strong) is FeasibilityBand.ASSURED
    assert rate(state, strong) is FeasibilityBand.ASSURED


def test_an_edge_never_makes_an_impossible_order_possible(
    world: tuple[State, dict[str, str]],
) -> None:
    state, ids = world
    for seat in ("varda_seat", "ostrel_seat"):
        for verb in (Verb.ATTACK, Verb.DEFEND, Verb.CONCEAL, Verb.FUND, Verb.SABOTAGE):
            for means in ("ostrel_army", "varda_guard", "varda_treasury"):
                order = _order(ids, verb, seat, targets=("varda",), means=(means,))
                if rate(_bare(state), order) is FeasibilityBand.IMPOSSIBLE:
                    assert rate(state, order) is FeasibilityBand.IMPOSSIBLE


def test_an_order_with_a_destination_stays_a_long_shot(world: tuple[State, dict[str, str]]) -> None:
    state, ids = world
    march = _order(
        ids,
        Verb.ATTACK,
        "ostrel_seat",
        targets=("varda",),
        means=("ostrel_army",),
        destination="harrow",
    )
    assert rate(state, march) is FeasibilityBand.LONG_SHOT


def test_a_verb_outside_the_edge_verbs_is_unchanged(world: tuple[State, dict[str, str]]) -> None:
    state, ids = world
    lobby = _order(ids, Verb.LOBBY, "ostrel_seat", targets=("varda",), means=("ostrel_army",))
    assert contest.edge(state, lobby) == 0
    assert rate(state, lobby) == rate(_bare(state), lobby) == FeasibilityBand.CONTESTED
