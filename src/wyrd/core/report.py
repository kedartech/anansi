"""Read-only views over the log for ``wyrd cost``, ``wyrd why`` and ``wyrd leaks``.

Pure: every function takes events (and state where needed) and returns models or
strings. The CLI does the reading and printing.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence

from wyrd.core import events as ev
from wyrd.core._base import Model
from wyrd.core.canary import Leak, scan
from wyrd.core.events import Event
from wyrd.core.ids import DeltaId, EventId, IntentId
from wyrd.core.state import State

_SUMMARY_CAP = 200


class CostLine(Model):
    tick: int
    in_tick_usd: float
    out_of_tick_usd: float
    calls: int
    quiet: bool = False


class CostReport(Model):
    lines: tuple[CostLine, ...]
    by_class: dict[str, float]
    by_model: dict[str, float]
    total_usd: float
    input_tokens: int
    output_tokens: int
    ticks_closed: int
    per_tick_usd: float


class WhyLine(Model):
    seq: int
    tick: int
    kind: str
    summary: str


def cost_report(events: Iterable[Event]) -> CostReport:
    in_tick: defaultdict[int, float] = defaultdict(float)
    outside: defaultdict[int, float] = defaultdict(float)
    calls: defaultdict[int, int] = defaultdict(int)
    closed: set[int] = set()
    quiet: set[int] = set()
    by_class: defaultdict[str, float] = defaultdict(float)
    by_model: defaultdict[str, float] = defaultdict(float)
    total_usd = 0.0
    input_tokens = 0
    output_tokens = 0
    ticks_closed = 0

    for e in events:
        p = e.payload
        if isinstance(p, ev.TickClosed):
            ticks_closed += 1
            closed.add(e.tick)
            if p.quiet:
                quiet.add(e.tick)
            bucket = in_tick
        elif isinstance(p, ev.CostLogged):
            bucket = outside
        else:
            continue
        for call in p.costs:
            bucket[e.tick] += call.usd
            calls[e.tick] += 1
            total_usd += call.usd
            input_tokens += call.input_tokens
            output_tokens += call.output_tokens
            by_class[call.call_class] += call.usd
            by_model[call.model] += call.usd

    lines = tuple(
        CostLine(
            tick=tick,
            in_tick_usd=in_tick[tick],
            out_of_tick_usd=outside[tick],
            calls=calls[tick],
            quiet=tick in quiet,
        )
        for tick in sorted(closed | set(calls))
    )
    return CostReport(
        lines=lines,
        by_class=dict(by_class),
        by_model=dict(by_model),
        total_usd=total_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        ticks_closed=ticks_closed,
        per_tick_usd=total_usd / ticks_closed if ticks_closed else 0.0,
    )


def render_cost(report: CostReport) -> str:
    out = [f"{'tick':>4}  {'in-tick':>7}  {'outside':>7}  {'calls':>5}"]
    for line in report.lines:
        out.append(
            f"{line.tick:>4}  {line.in_tick_usd:>7.4f}  {line.out_of_tick_usd:>7.4f}  "
            f"{line.calls:>5}"
        )
    out.append("")
    out.append("by class")
    out.extend(f"  {name:<24} ${usd:.4f}" for name, usd in report.by_class.items())
    out.append("by model")
    out.extend(f"  {name:<24} ${usd:.4f}" for name, usd in report.by_model.items())
    out.append("")
    out.append(
        f"total ${report.total_usd:.4f} over {report.ticks_closed} ticks, "
        f"${report.per_tick_usd:.4f} per tick"
    )
    return "\n".join(out)


def why(events: Sequence[Event], target: str) -> list[WhyLine]:
    by_id = {e.event_id: e for e in events}
    named = by_id.get(EventId(target))
    if named is not None:
        intent_id = _intent_id(named.payload)
        if intent_id is None:
            return [_line(named)]
    else:
        intent_id = IntentId(target)
    return [_line(e) for e in sorted(_chain(events, intent_id), key=lambda e: e.seq)]


def render_why(lines: Sequence[WhyLine]) -> str:
    if not lines:
        return "nothing found"
    return "\n".join(f"#{w.seq} t{w.tick} {w.kind}: {w.summary}" for w in lines)


def leak_report(state: State, events: Iterable[Event]) -> list[Leak]:
    return scan(state, events)


def render_leaks(leaks: Sequence[Leak]) -> str:
    if not leaks:
        return "0 leaks"
    out = [f"LEAK seat={x.seat_id} fact={x.fact_id} event={x.event_id}" for x in leaks]
    out.append(f"{len(leaks)} leaks")
    return "\n".join(out)


_INTENT_KEYED = (
    ev.IntentRejected,
    ev.FeasibilityRated,
    ev.Rolled,
    ev.DeltaProposed,
    ev.DeltaApplied,
    ev.OperationOpened,
    ev.OperationResolved,
)


def _intent_id(payload: ev.Payload) -> IntentId | None:
    if isinstance(payload, ev.IntentFiled):
        return payload.intent.intent_id
    if isinstance(payload, _INTENT_KEYED):
        return payload.intent_id
    return None


def _chain(events: Sequence[Event], intent_id: IntentId) -> list[Event]:
    from_order: EventId | None = None
    proposed: set[DeltaId] = set()
    applied: set[DeltaId] = set()
    for e in events:
        p = e.payload
        if _intent_id(p) != intent_id:
            continue
        if isinstance(p, ev.IntentFiled):
            from_order = p.from_order
        elif isinstance(p, ev.DeltaProposed):
            proposed.update(d.delta_id for d in p.deltas)
        elif isinstance(p, ev.DeltaApplied):
            applied.add(p.delta.delta_id)

    chain: list[Event] = []
    for e in events:
        p = e.payload
        if (
            _intent_id(p) == intent_id
            or (from_order is not None and e.event_id == from_order)
            or (isinstance(p, ev.DeltaRejected) and p.delta_id in proposed)
            or (isinstance(p, ev.NarrationWritten) and applied.intersection(p.from_deltas))
        ):
            chain.append(e)
    return chain


def _line(e: Event) -> WhyLine:
    summary = _summary(e.payload).replace("\n", " ")[:_SUMMARY_CAP]
    return WhyLine(seq=e.seq, tick=e.tick, kind=e.kind, summary=summary)


def _summary(p: ev.Payload) -> str:
    match p:
        case ev.OrderReceived(seat_id=seat, text=text):
            return f"{seat} wrote: {text[:120]}"
        case ev.IntentFiled(intent=i):
            return f"{i.verb.value} by {i.actor} targets={len(i.targets)} means={len(i.means)}"
        case ev.IntentRejected(reason=reason, detail=detail):
            return f"{reason.value}: {detail}"
        case ev.FeasibilityRated(band=band, perceived=perceived):
            return f"true {band.value}, perceived {perceived.value}"
        case ev.Rolled(outcome=outcome, exposure_roll=roll, exposed=exposed):
            return f"{outcome.value}, exposure {roll} exposed={exposed}"
        case ev.DeltaProposed(deltas=deltas):
            return f"{len(deltas)} deltas"
        case ev.DeltaRejected(delta_id=delta_id, reason=reason, detail=detail):
            return f"{delta_id} {reason.value}: {detail}"
        case ev.DeltaApplied(delta=delta):
            return f"{delta.kind}: {delta.reason}"
        case ev.OperationOpened(clock_id=clock_id):
            return f"clock {clock_id}"
        case ev.OperationResolved(clock_id=clock_id, outcome=outcome):
            return f"clock {clock_id} {outcome.value}"
        case ev.NarrationWritten(seat_id=seat, text=text):
            return f"to {seat}: {text[:80]}"
        case _:
            return p.kind


__all__ = [
    "CostLine",
    "CostReport",
    "WhyLine",
    "cost_report",
    "leak_report",
    "render_cost",
    "render_leaks",
    "render_why",
    "why",
]
