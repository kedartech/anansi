"""Order results: what each of one seat's orders did in one tick (playtest #19).

Read through the seat's own view only, so a covert result the seat did not see
stays out of the lines. Exposure is never read: no player is told it was rolled.
"""

from collections.abc import Mapping, Sequence
from enum import StrEnum

from revisionist.core._base import Model
from revisionist.core.delta import ClockStatusSet
from revisionist.core.events import (
    DeltaApplied,
    Event,
    IntentFiled,
    IntentRejected,
    OperationOpened,
    OperationResolved,
    OrderReceived,
    Rolled,
    TickClosed,
    TickOpened,
)
from revisionist.core.ids import EventId, SeatId
from revisionist.core.intent import Intent
from revisionist.core.ontology import ClockStatus
from revisionist.core.resolution import OutcomeTier

ORDERS_HEADER = "Your orders:"
ORDER_TEXT_LIMIT = 100


class OrderResult(StrEnum):
    WORKED = "worked"
    OPENED = "worked, and opened a door"
    PARTIAL = "partly worked"
    FAILED = "failed, with a complication"
    REFUSED = "refused"
    UNDER_WAY = "under way"
    CARRIED = "carried to next tick"


class OrderLine(Model):
    ordered: str
    result: OrderResult
    detail: str = ""


_OUTCOMES: dict[OutcomeTier, OrderResult] = {
    OutcomeTier.SUCCESS: OrderResult.WORKED,
    OutcomeTier.SUCCESS_OPPORTUNITY: OrderResult.OPENED,
    OutcomeTier.PARTIAL: OrderResult.PARTIAL,
    OutcomeTier.FAIL_COMPLICATION: OrderResult.FAILED,
}


def order_results(events: Sequence[Event], seat: SeatId, tick: int) -> list[OrderLine]:
    """One line per order ``seat`` froze in ``tick``, in freeze order."""
    visible = [e for e in events if e.scope.visible_to(seat)]
    opened = next(
        (e.payload for e in visible if e.tick == tick and isinstance(e.payload, TickOpened)),
        None,
    )
    if opened is None:
        return []
    quiet = any(
        isinstance(e.payload, TickClosed) and e.tick == tick and e.payload.quiet for e in visible
    )
    filed = {
        e.payload.intent.intent_id: e.payload for e in visible if isinstance(e.payload, IntentFiled)
    }
    typed = {e.event_id: e.payload.text for e in visible if isinstance(e.payload, OrderReceived)}
    lines: list[OrderLine] = []
    for intent_id in opened.intents_frozen:
        f = filed.get(intent_id)
        if f is None or f.intent.actor != seat:
            continue
        result, detail = (OrderResult.CARRIED, "") if quiet else _result(visible, f.intent, tick)
        lines.append(OrderLine(ordered=_ordered(f, typed), result=result, detail=detail))
    return lines


def render(lines: Sequence[OrderLine]) -> list[str]:
    if not lines:
        return []
    return [ORDERS_HEADER, *(_line(line) for line in lines)]


def _ordered(filed: IntentFiled, typed: Mapping[EventId, str]) -> str:
    if filed.from_order is None:
        return f"{filed.intent.verb.value} (your deputy)"
    text = typed.get(filed.from_order, filed.intent.verb.value).strip()
    if len(text) > ORDER_TEXT_LIMIT:
        return text[:ORDER_TEXT_LIMIT] + "..."
    return text


def _result(visible: Sequence[Event], intent: Intent, tick: int) -> tuple[OrderResult, str]:
    """The first rule that matches wins; the order is the one in the module docs."""
    iid = intent.intent_id
    refused: IntentRejected | None = None
    resolved: OutcomeTier | None = None
    rolled: OutcomeTier | None = None
    failed = False
    settled = False
    opened_at: int | None = None
    for e in visible:
        p = e.payload
        match p:
            case IntentRejected(intent_id=i) if i == iid and e.tick == tick:
                refused = p
            case OperationResolved(intent_id=i) if i == iid and e.tick <= tick:
                settled = True
                if e.tick == tick:
                    resolved = p.outcome
            case DeltaApplied(intent_id=i, delta=ClockStatusSet(status=ClockStatus.FAILED)) if (
                i == iid and e.tick == tick
            ):
                failed = True
            case OperationOpened(intent_id=i) if i == iid and e.tick <= tick:
                opened_at = e.tick
            case Rolled(intent_id=i) if i == iid and e.tick == tick:
                rolled = p.outcome
    if refused is not None:
        return OrderResult.REFUSED, refused.reason.value.replace("_", " ")
    if resolved is not None:
        return _OUTCOMES[resolved], ""
    if failed:
        return OrderResult.FAILED, ""
    if opened_at is not None and not settled:
        detail = f"{intent.duration_ticks} ticks" if opened_at == tick else ""
        return OrderResult.UNDER_WAY, detail
    if rolled is not None:
        return _OUTCOMES[rolled], ""
    return OrderResult.CARRIED, ""


def _line(line: OrderLine) -> str:
    text = f"{line.ordered}: {line.result.value}"
    return f"{text} ({line.detail})" if line.detail else text
