"""The opening dossier and the public world state name only what may be seen."""

from revisionist.core import delta as d
from revisionist.core.dossier import dossier
from revisionist.core.events import DeltaApplied
from revisionist.core.fold import replay
from revisionist.core.ids import DeltaId
from revisionist.core.ontology import EntityStatus
from revisionist.core.project import project
from revisionist.core.visibility import PUBLIC
from revisionist.core.world_state import world_state
from tests.fixtures import (
    A_NORTH_SECRET_ARMY,
    CANARY,
    E_SOUTH,
    F_NORTH_PLOT,
    M_NORTH_STABILITY,
    NORTH,
    creation_events,
    envelope,
)

_N = 0


def _id(kind: str) -> str:
    global _N
    _N += 1
    return f"01ARZ3NDEKTSV4RRFFQ69{_N:05d}" if kind == "e" else f"01ARZ3NDEKTSV4RRFFQ69D{_N:04d}"


def test_the_dossier_lists_holdings_and_never_the_canary() -> None:
    view = project(replay(creation_events()), NORTH)
    text = "\n".join(dossier(view))
    assert "Northern Levies" in text and "tier 2" in text
    assert "Hill Irregulars" in text  # the North's own covert force
    assert "Southern Treasury" not in text  # held by the South
    assert "Stability: 60 of " in text
    assert "South: cold (-1)" in text
    assert "North" in text and "River Guild" in text
    assert CANARY not in text  # the canary names the covert fact; the predicate does not


def test_the_dossier_names_a_public_fact_and_not_a_covert_one() -> None:
    view = project(replay(creation_events()), NORTH)
    text = "\n".join(dossier(view))
    assert "signed_truce_with" in text
    assert "arms" in text  # the North may see its own covert fact
    assert CANARY not in text


def test_a_private_change_never_reaches_the_public_world_state() -> None:
    state = replay(creation_events())
    events = [
        envelope(
            int(_id("e")[-3:]),
            DeltaApplied(
                intent_id=None,
                delta=d.FactAssert(
                    delta_id=DeltaId(_id("d")),
                    reason="test",
                    fact_id=F_NORTH_PLOT,
                    subject=state.facts[F_NORTH_PLOT].subject,
                    predicate="rearms",
                    obj=A_NORTH_SECRET_ARMY,
                    exposure=2,
                    scope=state.facts[F_NORTH_PLOT].scope,
                ),
            ),
            tick=1,
        ),
        envelope(
            2,
            DeltaApplied(
                intent_id=None,
                delta=d.EntityStatusSet(
                    delta_id=DeltaId(_id("d")),
                    reason="test",
                    entity_id=E_SOUTH,
                    status=EntityStatus.DEAD,
                ),
            ),
            tick=1,
        ),
    ]
    text = "\n".join(world_state(state, events, 1))
    assert CANARY not in text
    assert "South is dead" in text


def test_a_quiet_tick_says_so() -> None:
    state = replay(creation_events())
    assert world_state(state, [], 3) == [
        "The world after tick 3:",
        "No public change this tick.",
    ]


def test_a_restricted_meter_change_stays_out_of_the_public_state() -> None:
    state = replay(creation_events())
    events = [
        envelope(
            int(_id("e")[-3:]),
            DeltaApplied(
                intent_id=None,
                delta=d.MeterDelta(
                    delta_id=DeltaId(_id("d")), reason="test", meter_id=M_NORTH_STABILITY, change=-5
                ),
            ),
            tick=2,
            scope=PUBLIC,
        )
    ]
    text = "\n".join(world_state(state, events, 2))
    assert "Stability" not in text
    assert "No public change this tick." in text
