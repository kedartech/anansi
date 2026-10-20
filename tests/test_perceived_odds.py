"""Layer 9b: perceived odds (spec §7).

A player sees how their own agent rated an order once it is locked at the
cutoff, never the true band, never before, and not at all when blinded. The
fixture's South attack reads Likely to South and is Contested in truth,
because North's hill irregulars are covert.
"""

from collections.abc import Iterator

import pytest

from revisionist.core.ids import IntentId
from revisionist.core.intent import Intent, Verb
from revisionist.core.ontology import SeatStatus
from revisionist.core.resolution import TickStage
from revisionist.core.visibility import private_to
from revisionist.engine import play
from revisionist.engine.providers import RecordingDeliverer, ScriptedResolver, TemplateNarrator
from revisionist.engine.store import MemoryStore, load_state
from revisionist.engine.tick import Providers, file_intent, run_tick
from tests import fixtures as fx
from tests.test_cli import FakeClaude

ATTACK = Intent(
    intent_id=IntentId("01ARZ3NDEKTSV4RRFFQ69P0001"),
    actor=fx.SOUTH,
    verb=Verb.ATTACK,
    targets=(fx.E_NORTH,),
    means=(fx.A_SOUTH_GUARD,),
    duration_ticks=3,
    scope=private_to(fx.SOUTH),
    filed_tick=0,
)


@pytest.fixture
def store() -> Iterator[MemoryStore]:
    s = MemoryStore()
    s.append_all(fx.creation_events())
    assert file_intent(s, fx.CAMPAIGN, ATTACK) is None
    yield s


def _providers() -> Providers:
    return Providers(ScriptedResolver(), TemplateNarrator(), RecordingDeliverer())


def _status(store: MemoryStore) -> str:
    state = load_state(store, fx.CAMPAIGN)
    return "\n".join(play.status_lines(state, fx.SOUTH, store.read(fx.CAMPAIGN)))


def test_no_odds_before_the_order_locks(store: MemoryStore) -> None:
    status = _status(store)
    assert "pending" in status and "read as" not in status


def test_a_locked_order_shows_the_perceived_band_never_the_true_one(store: MemoryStore) -> None:
    run_tick(store, fx.CAMPAIGN, _providers(), stop_after=TickStage.FREEZE)
    status = _status(store)
    assert "read as likely at the tick 0 cutoff" in status
    assert "contested" not in status


def test_the_briefing_reports_the_last_cutoff(store: MemoryStore) -> None:
    run_tick(store, fx.CAMPAIGN, _providers())
    brief = play.get_briefing(store, fx.CAMPAIGN, fx.SOUTH, FakeClaude())
    text = "\n".join(brief.lines)
    assert "At the last cutoff your agent read your orders as:" in text
    assert "attack -> " in text and text.rstrip().endswith(": likely")
    assert "contested" not in text
    other = "\n".join(play.get_briefing(store, fx.CAMPAIGN, fx.NORTH, FakeClaude()).lines)
    assert "last cutoff" not in other  # North filed nothing, and never sees South's odds


def test_a_blinded_seat_reads_no_odds(store: MemoryStore) -> None:
    run_tick(store, fx.CAMPAIGN, _providers(), stop_after=TickStage.FREEZE)
    state = load_state(store, fx.CAMPAIGN)
    south = state.seats[fx.SOUTH].model_copy(update={"agent": None, "status": SeatStatus.BLINDED})
    blind = state.model_copy(update={"seats": {**state.seats, fx.SOUTH: south}})
    status = "\n".join(play.status_lines(blind, fx.SOUTH, store.read(fx.CAMPAIGN)))
    assert "pending" in status and "read as" not in status
