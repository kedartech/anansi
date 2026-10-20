"""What a seat does between ticks: give an order, read a briefing, check status.

``wyrd order | briefing | status`` and the Discord bot (Layer 9a) both call
these, so the two paths cannot drift. Each returns text for the caller to
print or send; none prints. Transport errors propagate.
"""

from dataclasses import dataclass

from wyrd.core.canary import scan_deliveries
from wyrd.core.ids import CampaignId, SeatId
from wyrd.core.intent import Intent
from wyrd.core.project import project
from wyrd.core.state import State
from wyrd.engine import campaign as cmp
from wyrd.engine.agent import Agent, Briefing, StructureResult, receive_order
from wyrd.engine.llm import Transport
from wyrd.engine.store import EventStore, load_state


@dataclass(frozen=True)
class OrderOutcome:
    result: StructureResult
    line: str
    """``filed ...``, ``question: ...`` or ``refused: ...``."""

    @property
    def refused(self) -> bool:
        return self.result.rejection is not None


@dataclass(frozen=True)
class BriefingOutcome:
    briefing: Briefing
    withheld: bool
    """The briefing named a canary the seat cannot know; ``lines`` is then empty."""
    lines: tuple[str, ...]


WITHHELD = "briefing withheld: it named something your seat cannot know"


def object_name(state: State, object_id: str) -> str:
    for table in (state.entities, state.assets, state.clocks, state.meters, state.seats):
        found = table.get(object_id)  # type: ignore[call-overload]  # IDs are str NewTypes
        if found is not None:
            return str(found.name)
    return object_id


def give_order(
    store: EventStore, campaign_id: CampaignId, seat: SeatId, text: str, transport: Transport
) -> OrderOutcome:
    """Log the order, structure it, file the intent or ask a question, log the cost."""
    state = load_state(store, campaign_id)
    result = receive_order(store, state, seat, text, transport, versions=cmp.versions())
    if result.cost is not None:
        cmp.log_cost(store, campaign_id, "structure", [result.cost], seat)
    outcome = result.outcome
    if not isinstance(outcome, Intent):
        return OrderOutcome(result, f"question: {outcome.question}")
    if result.rejection is not None:
        reason = result.rejection
        return OrderOutcome(result, f"refused: {reason.reason.value}: {reason.detail}")
    targets = ", ".join(object_name(state, t) for t in outcome.targets) or "-"
    return OrderOutcome(result, f"filed {outcome.intent_id}: {outcome.verb.value} -> {targets}")


def get_briefing(
    store: EventStore, campaign_id: CampaignId, seat: SeatId, transport: Transport
) -> BriefingOutcome:
    """The seat's briefing, its cost logged, withheld if it names a hidden canary."""
    state = load_state(store, campaign_id)
    brief = Agent.for_seat(state, seat, transport).brief()
    if brief.cost is not None:
        cmp.log_cost(store, campaign_id, "briefing", [brief.cost], seat)
    moves = brief.moves or ()
    text = "\n".join([brief.text, brief.dilemma or "", *moves])
    if scan_deliveries(state, [(seat, text)]):
        return BriefingOutcome(brief, withheld=True, lines=())
    lines = [brief.text]
    if brief.dilemma:
        lines.append(f"\nDilemma: {brief.dilemma}")
    lines += [f"  {n}. {move}" for n, move in enumerate(moves, start=1)]
    return BriefingOutcome(brief, withheld=False, lines=tuple(lines))


def status_lines(state: State, seat: SeatId) -> list[str]:
    """Where the campaign stands, as ``seat`` sees it."""
    view = project(state, seat)
    lines = [f"tick {view.tick}  seat {view.seats[seat].name}  slots {view.seats[seat].slots}"]
    for intent in view.own_pending:
        targets = ", ".join(object_name(state, t) for t in intent.targets) or "-"
        lines.append(f"  pending {intent.intent_id} {intent.verb.value} -> {targets}")
    for clock in sorted(view.clocks.values(), key=lambda c: c.name):
        if clock.status.value == "open":
            lines.append(f"  clock {clock.name}: {clock.segments_filled}/{clock.segments_total}")
    for meter in sorted(view.meters.values(), key=lambda m: (m.owner, m.name)):
        lines.append(f"  {object_name(state, meter.owner)} {meter.name}: {meter.value}")
    return lines


__all__ = [
    "WITHHELD",
    "BriefingOutcome",
    "OrderOutcome",
    "get_briefing",
    "give_order",
    "object_name",
    "status_lines",
]
