"""Order results: one line per order of one seat, read through that seat's view."""

from revisionist.core import delta as d
from revisionist.core import events as ev
from revisionist.core.events import Event
from revisionist.core.ids import ClockId, DeltaId, IntentId, SeatId
from revisionist.core.intent import Intent, Verb
from revisionist.core.ontology import ClockStatus
from revisionist.core.orders import ORDERS_HEADER, OrderLine, OrderResult, order_results, render
from revisionist.core.resolution import OutcomeTier, RejectionReason
from revisionist.core.visibility import PUBLIC, Scope, private_to
from tests import fixtures as fx

SEAT = fx.NORTH
OTHER = fx.SOUTH
TICK = 3


def _ev(seq: int, payload: ev.Payload, *, scope: Scope = PUBLIC, tick: int = TICK) -> Event:
    return fx.envelope(seq, payload, tick=tick, scope=scope)


def _iid(n: int) -> IntentId:
    return IntentId(f"01ARZ3NDEKTSV4RRFFQ69I{n:05d}")


def _intent(n: int, *, actor: SeatId = SEAT, verb: Verb = Verb.LOBBY, duration: int = 1) -> Intent:
    return Intent(
        intent_id=_iid(n),
        actor=actor,
        verb=verb,
        duration_ticks=duration,
        scope=private_to(actor),
        filed_tick=TICK,
    )


def _opened(seq: int, *intents: Intent) -> Event:
    return _ev(seq, ev.TickOpened(intents_frozen=tuple(i.intent_id for i in intents)))


def _typed(seq: int, intent: Intent, text: str) -> list[Event]:
    """What the player wrote, then the intent the agent filed from it."""
    typed = _ev(
        seq, ev.OrderReceived(seat_id=intent.actor, text=text), scope=private_to(intent.actor)
    )
    filed = _ev(
        seq + 1,
        ev.IntentFiled(intent=intent, from_order=typed.event_id),
        scope=intent.scope,
    )
    return [typed, filed]


def _deputy(seq: int, intent: Intent) -> list[Event]:
    return [_ev(seq, ev.IntentFiled(intent=intent), scope=intent.scope)]


def _rolled(
    seq: int,
    intent: Intent,
    outcome: OutcomeTier = OutcomeTier.SUCCESS,
    *,
    exposed: bool = False,
    exposure_roll: int = 0,
    scope: Scope | None = None,
) -> Event:
    payload = ev.Rolled(
        intent_id=intent.intent_id,
        seed="00",
        outcome=outcome,
        exposure_roll=exposure_roll,
        exposed=exposed,
    )
    return _ev(seq, payload, scope=scope if scope is not None else intent.scope)


def _line(events: list[Event], seat: SeatId = SEAT) -> list[OrderLine]:
    return order_results(events, seat, TICK)


def test_outcome_tiers_map_to_results() -> None:
    intent = _intent(1)
    expected = {
        OutcomeTier.SUCCESS: OrderResult.WORKED,
        OutcomeTier.SUCCESS_OPPORTUNITY: OrderResult.OPENED,
        OutcomeTier.PARTIAL: OrderResult.PARTIAL,
        OutcomeTier.FAIL_COMPLICATION: OrderResult.FAILED,
    }
    for outcome, result in expected.items():
        events = [
            _opened(0, intent),
            *_typed(1, intent, "Lobby the council"),
            _rolled(3, intent, outcome),
        ]
        assert _line(events) == [OrderLine(ordered="Lobby the council", result=result)]


def test_rejected_order_is_refused_with_its_reason() -> None:
    intent = _intent(1)
    events = [
        _opened(0, intent),
        *_typed(1, intent, "Buy the mill"),
        _ev(
            3, ev.IntentRejected(intent_id=intent.intent_id, reason=RejectionReason.MEANS_NOT_HELD)
        ),
    ]
    assert _line(events) == [
        OrderLine(ordered="Buy the mill", result=OrderResult.REFUSED, detail="means not held")
    ]


def test_operation_opened_this_tick_is_under_way_for_its_duration() -> None:
    intent = _intent(1, verb=Verb.SABOTAGE, duration=3)
    clock = ClockId("01ARZ3NDEKTSV4RRFFQ69C00001")
    events = [
        _opened(0, intent),
        *_typed(1, intent, "Sabotage the dam"),
        _rolled(3, intent),
        _ev(4, ev.OperationOpened(intent_id=intent.intent_id, clock_id=clock, seat_id=SEAT)),
    ]
    assert _line(events) == [
        OrderLine(ordered="Sabotage the dam", result=OrderResult.UNDER_WAY, detail="3 ticks")
    ]


def test_operation_running_from_an_earlier_tick_is_under_way_without_detail() -> None:
    intent = _intent(1, verb=Verb.SABOTAGE, duration=3)
    clock = ClockId("01ARZ3NDEKTSV4RRFFQ69C00001")
    events = [
        _ev(
            0,
            ev.OperationOpened(intent_id=intent.intent_id, clock_id=clock, seat_id=SEAT),
            tick=TICK - 1,
            scope=intent.scope,
        ),
        _opened(1, intent),
        *_typed(2, intent, "Keep sabotaging the dam"),
        _rolled(4, intent),
    ]
    assert _line(events) == [
        OrderLine(ordered="Keep sabotaging the dam", result=OrderResult.UNDER_WAY)
    ]


def test_operation_failed_on_its_reroll_is_failed_even_with_a_good_opening_roll() -> None:
    intent = _intent(1, verb=Verb.SABOTAGE, duration=3)
    clock = ClockId("01ARZ3NDEKTSV4RRFFQ69C00001")
    fail = ev.DeltaApplied(
        intent_id=intent.intent_id,
        delta=d.ClockStatusSet(
            delta_id=DeltaId("01ARZ3NDEKTSV4RRFFQ69D00001"),
            reason="operation failed its re-roll",
            clock_id=clock,
            status=ClockStatus.FAILED,
        ),
    )
    events = [
        _opened(0, intent),
        *_typed(1, intent, "Keep sabotaging the dam"),
        _rolled(3, intent, OutcomeTier.SUCCESS),
        _ev(4, fail, scope=intent.scope),
    ]
    assert _line(events) == [
        OrderLine(ordered="Keep sabotaging the dam", result=OrderResult.FAILED)
    ]


def test_order_text_is_stripped_and_cut_at_100_characters() -> None:
    long, exact = _intent(1), _intent(2)
    events = [
        _opened(0, long, exact),
        *_typed(1, long, "  " + "a" * 150),
        *_typed(3, exact, "b" * 100),
    ]
    assert _line(events) == [
        OrderLine(ordered="a" * 100 + "...", result=OrderResult.CARRIED),
        OrderLine(ordered="b" * 100, result=OrderResult.CARRIED),
    ]


def test_deputy_filed_order_is_labelled_with_the_verb() -> None:
    intent = _intent(1, verb=Verb.LOBBY)
    events = [_opened(0, intent), *_deputy(1, intent), _rolled(3, intent)]
    assert _line(events) == [OrderLine(ordered="lobby (your deputy)", result=OrderResult.WORKED)]


def test_other_seats_orders_never_appear() -> None:
    mine = _intent(1)
    theirs = _intent(2, actor=OTHER)
    public_theirs = Intent(
        intent_id=_iid(3), actor=OTHER, verb=Verb.LOBBY, scope=PUBLIC, filed_tick=TICK
    )
    events = [
        _opened(0, mine, theirs, public_theirs),
        *_typed(1, mine, "Lobby the council"),
        *_typed(3, theirs, "Buy the mill"),
        _ev(5, ev.IntentFiled(intent=public_theirs), scope=PUBLIC),
        _rolled(6, mine),
        _rolled(7, theirs),
    ]
    assert _line(events) == [
        OrderLine(ordered="Lobby the council", result=OrderResult.WORKED),
    ]
    assert _line(events, OTHER) == [
        OrderLine(ordered="Buy the mill", result=OrderResult.WORKED),
        OrderLine(ordered="lobby (your deputy)", result=OrderResult.CARRIED),
    ]


def test_result_the_seat_cannot_see_falls_through_to_carried() -> None:
    intent = _intent(1)
    hidden = _rolled(3, intent, OutcomeTier.SUCCESS, scope=private_to(OTHER, covert=True))
    assert _line([_opened(0, intent), *_typed(1, intent, "Forge the letter"), hidden]) == [
        OrderLine(ordered="Forge the letter", result=OrderResult.CARRIED)
    ]


def test_covert_result_the_seat_can_see_is_reported() -> None:
    intent = _intent(1)
    seen = _rolled(3, intent, OutcomeTier.PARTIAL, scope=private_to(SEAT, covert=True))
    assert _line([_opened(0, intent), *_typed(1, intent, "Forge the letter"), seen]) == [
        OrderLine(ordered="Forge the letter", result=OrderResult.PARTIAL)
    ]


def test_quiet_tick_carries_every_order() -> None:
    mine = _intent(1)
    other = _intent(2)
    events = [
        _opened(0, mine, other),
        *_typed(1, mine, "Lobby the council"),
        *_typed(3, other, "Buy the mill"),
        _rolled(5, mine),
        _ev(6, ev.TickClosed(quiet=True, quiet_reason="resolver down", state_hash="h")),
    ]
    assert _line(events) == [
        OrderLine(ordered="Lobby the council", result=OrderResult.CARRIED),
        OrderLine(ordered="Buy the mill", result=OrderResult.CARRIED),
    ]


def test_orders_follow_the_freeze_order() -> None:
    first = _intent(1)
    second = _intent(2)
    events = [
        _opened(0, second, first),
        *_typed(1, first, "First"),
        *_typed(3, second, "Second"),
        _rolled(5, first),
        _rolled(6, second),
    ]
    assert [line.ordered for line in _line(events)] == ["Second", "First"]


def test_exposure_never_changes_the_result() -> None:
    intent = _intent(1)
    base = [_opened(0, intent), *_typed(1, intent, "Forge the letter")]
    quiet = [*base, _rolled(3, intent, exposed=False, exposure_roll=0)]
    exposed = [*base, _rolled(3, intent, exposed=True, exposure_roll=99)]
    assert (
        _line(quiet)
        == _line(exposed)
        == [OrderLine(ordered="Forge the letter", result=OrderResult.WORKED)]
    )


def test_render_of_nothing_is_nothing() -> None:
    assert render([]) == []


def test_render_starts_with_the_header_and_adds_detail_in_brackets() -> None:
    lines = [
        OrderLine(ordered="Buy the mill", result=OrderResult.REFUSED, detail="means not held"),
        OrderLine(ordered="Lobby the council", result=OrderResult.WORKED),
    ]
    assert render(lines) == [
        ORDERS_HEADER,
        "Buy the mill: refused (means not held)",
        "Lobby the council: worked",
    ]
