"""Playtest #19: a player's briefing opens with what their own orders did this tick."""

from collections.abc import Sequence

import pytest

from revisionist.core import delta as d
from revisionist.core import events as ev
from revisionist.core.ids import SeatId
from revisionist.core.intent import Verb
from revisionist.core.orders import ORDERS_HEADER
from revisionist.core.resolution import LEGACY_STAGES, TickStage
from revisionist.core.state import View
from revisionist.engine.providers import (
    KeywordSafety,
    RecordingDeliverer,
    ScriptedResolver,
    TemplateNarrator,
)
from revisionist.engine.store import EventStore, MemoryStore
from revisionist.engine.tick import Narration, Providers, file_intent, run_tick
from tests import fixtures as fx
from tests.test_tick import _file_some, _intent, _script, store  # noqa: F401  (the fixture)

STAGES = list(LEGACY_STAGES)


class _Recording:
    """Template prose, with each seat's text kept so a test can compare the log to it."""

    def __init__(self) -> None:
        self.texts: dict[SeatId, str] = {}

    def narrate(self, view: View, deltas: Sequence[d.Delta]) -> Narration:
        n = TemplateNarrator().narrate(view, deltas)
        self.texts[view.viewer] = n.text
        return n


def _providers() -> tuple[Providers, RecordingDeliverer, _Recording]:
    deliverer = RecordingDeliverer()
    narrator = _Recording()
    p = Providers(
        resolver=ScriptedResolver(_script),
        narrator=narrator,
        deliverer=deliverer,
        safety=KeywordSafety(),
    )
    return p, deliverer, narrator


def _logged(store: EventStore) -> dict[SeatId, str]:  # noqa: F811
    return {
        e.payload.seat_id: e.payload.text
        for e in store.read(fx.CAMPAIGN)
        if isinstance(e.payload, ev.NarrationWritten)
    }


def _orders_block(text: str) -> list[str]:
    return text.split("\n\n", 1)[0].split("\n")


def test_human_briefing_opens_with_its_order(store: EventStore) -> None:  # noqa: F811
    _file_some(store, 0, 1)
    providers, deliverer, _ = _providers()
    assert not run_tick(store, fx.CAMPAIGN, providers).quiet
    text = _logged(store)[fx.NORTH]
    block = _orders_block(text)
    assert block[0] == ORDERS_HEADER
    assert len(block) == 2 and block[1].startswith("declare (your deputy): ")
    assert text.split("\n\n", 1)[1].startswith("Tick 0")
    delivered = next(t for s, t, _ in deliverer.sent if s == fx.NORTH)
    assert delivered.endswith(text)


def test_seat_with_no_order_gets_exactly_the_narrators_text(store: EventStore) -> None:  # noqa: F811
    assert (
        file_intent(
            store,
            fx.CAMPAIGN,
            _intent(
                Verb.SABOTAGE,
                fx.SOUTH,
                2,
                targets=(fx.A_NORTH_ARMY,),
                means=(fx.A_SOUTH_GUARD,),
                duration_ticks=2,
            ),
        )
        is None
    )
    providers, _, narrator = _providers()
    assert not run_tick(store, fx.CAMPAIGN, providers).quiet
    text = _logged(store)[fx.NORTH]
    assert ORDERS_HEADER not in text
    assert text == narrator.texts[fx.NORTH]


def test_outlet_narration_never_has_orders(store: EventStore) -> None:  # noqa: F811
    _file_some(store, 0, 1)
    providers, _, narrator = _providers()
    assert not run_tick(store, fx.CAMPAIGN, providers).quiet
    text = _logged(store)[fx.HERALD]
    assert ORDERS_HEADER not in text
    assert text == narrator.texts[fx.HERALD]


def test_another_seats_order_stays_out_of_this_section(store: EventStore) -> None:  # noqa: F811
    _file_some(store, 0, 1)
    providers, _, _ = _providers()
    assert not run_tick(store, fx.CAMPAIGN, providers).quiet
    logged = _logged(store)
    north, south = _orders_block(logged[fx.NORTH]), _orders_block(logged[fx.SOUTH])
    assert len(north) == 2 and "sabotage" not in " ".join(north)
    assert len(south) == 2 and south[1].startswith("sabotage (your deputy): ")
    assert "declare" not in " ".join(south)


@pytest.mark.parametrize("crash_after", STAGES)
def test_resumed_tick_narrates_the_same_orders(store: EventStore, crash_after: TickStage) -> None:  # noqa: F811
    _file_some(store, 0, 1)
    providers, _, _ = _providers()
    partial = run_tick(store, fx.CAMPAIGN, providers, stop_after=crash_after)
    assert partial.stopped_after is crash_after
    resumed = run_tick(store, fx.CAMPAIGN, providers)
    assert resumed.resumed_from is crash_after

    clean: EventStore = MemoryStore()
    clean.append_all(fx.creation_events())
    _file_some(clean, 0, 1)
    clean_providers, _, _ = _providers()
    straight = run_tick(clean, fx.CAMPAIGN, clean_providers)

    assert resumed.state_hash == straight.state_hash
    assert _logged(store) == _logged(clean)
    assert ORDERS_HEADER in _logged(store)[fx.NORTH]
