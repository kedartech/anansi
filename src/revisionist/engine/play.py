"""What a seat does between ticks: give an order, read a briefing, check status.

``revisionist order | briefing | status`` and the Discord bot (Layer 9a) both call
these, so the two paths cannot drift. Each returns text for the caller to
print or send; none prints. Transport errors propagate.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from revisionist.core import dashboard
from revisionist.core import events as ev
from revisionist.core.canary import scan_deliveries
from revisionist.core.events import Event
from revisionist.core.ids import CampaignId, IntentId, SeatId
from revisionist.core.intent import Intent
from revisionist.core.project import project
from revisionist.core.resolution import FeasibilityBand
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC, Scope, Visibility, private_to
from revisionist.engine import campaign as cmp
from revisionist.engine.agent import Agent, Briefing, StructureResult, receive_order
from revisionist.engine.llm import Transport
from revisionist.engine.store import EventStore, load_state


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


ORDER_VISIBILITIES = (Visibility.PRIVATE, Visibility.PUBLIC, Visibility.COVERT)
"""What a player may choose. Restricted waits for alliances (Layer 10)."""
SPIN_LIMIT = 280


def order_scope(seat: SeatId, visibility: Visibility) -> Scope:
    if visibility is Visibility.PUBLIC:
        return PUBLIC
    if visibility is Visibility.PRIVATE:
        return private_to(seat)
    if visibility is Visibility.COVERT:
        return private_to(seat, covert=True)
    raise ValueError(f"orders cannot be {visibility.value} yet")


def give_order(
    store: EventStore,
    campaign_id: CampaignId,
    seat: SeatId,
    text: str,
    transport: Transport,
    *,
    visibility: Visibility = Visibility.PRIVATE,
    spin: str | None = None,
) -> OrderOutcome:
    """Log the order, structure it, file the intent or ask a question, log the cost.

    ``visibility`` and ``spin`` are the player's: the agent never chooses them.
    Spin is the one public line on an order, published when the tick freezes.
    """
    spin = (spin or "").strip() or None
    if spin is not None and len(spin) > SPIN_LIMIT:
        raise ValueError(f"spin is at most {SPIN_LIMIT} characters")
    scope = order_scope(seat, visibility)
    state = load_state(store, campaign_id)
    result = receive_order(
        store, state, seat, text, transport, versions=cmp.versions(), scope=scope, spin=spin
    )
    if result.cost is not None:
        cmp.log_cost(store, campaign_id, "structure", [result.cost], seat)
    outcome = result.outcome
    if not isinstance(outcome, Intent):
        return OrderOutcome(result, f"question: {outcome.question}")
    if result.rejection is not None:
        reason = result.rejection
        return OrderOutcome(result, f"refused: {reason.reason.value}: {reason.detail}")
    targets = ", ".join(object_name(state, t) for t in outcome.targets) or "-"
    line = f"filed {outcome.intent_id}: {outcome.verb.value} -> {targets} ({visibility.value})"
    return OrderOutcome(result, line)


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
    lines = [*dashboard_lines(state, seat), "", brief.text]
    if brief.dilemma:
        lines.append(f"\nDilemma: {brief.dilemma}")
    lines += [f"  {n}. {move}" for n, move in enumerate(moves, start=1)]
    lines += _last_cutoff(state, seat, list(store.read(campaign_id)))
    return BriefingOutcome(brief, withheld=False, lines=tuple(lines))


def dashboard_lines(state: State, seat: SeatId) -> list[str]:
    """The seat's dashboard (spec §15), from its own view only. No model is asked."""
    return dashboard.render(dashboard.dashboard(project(state, seat)))


# --- perceived odds (spec §7) ----------------------------------------------------
#
# A player sees how their own agent rated an order only once it is locked: the
# perceived band logged in feasibility_rated when the tick froze. Never the true
# band, never before the cutoff (so odds cannot be shopped for by re-filing), and
# never for a blinded seat, which has no agent to read anything.


def _band(band: FeasibilityBand) -> str:
    return band.value.replace("_", " ")


def _perceived(
    events: Iterable[Event], seat: SeatId
) -> dict[IntentId, tuple[int, FeasibilityBand]]:
    """The latest perceived band per locked intent of ``seat``, with its tick."""
    rated: dict[IntentId, tuple[int, FeasibilityBand]] = {}
    for e in events:
        if isinstance(e.payload, ev.FeasibilityRated) and e.actor == seat:
            rated[e.payload.intent_id] = (e.tick, e.payload.perceived)
    return rated


def _last_cutoff(state: State, seat: SeatId, events: Sequence[Event]) -> list[str]:
    if state.seats[seat].agent is None or state.tick == 0:
        return []
    filed = {
        e.payload.intent.intent_id: e.payload.intent
        for e in events
        if isinstance(e.payload, ev.IntentFiled) and e.payload.intent.actor == seat
    }
    last = [
        (filed[i], band)
        for i, (tick, band) in _perceived(events, seat).items()
        if tick == state.tick - 1 and i in filed
    ]
    if not last:
        return []
    lines = ["", "At the last cutoff your agent read your orders as:"]
    for intent, band in last:
        targets = ", ".join(object_name(state, t) for t in intent.targets) or "-"
        lines.append(f"  {intent.verb.value} -> {targets}: {_band(band)}")
    return lines


def status_lines(state: State, seat: SeatId, events: Iterable[Event] = ()) -> list[str]:
    """Where the campaign stands, as ``seat`` sees it. ``events`` (the log) adds
    the perceived odds of locked orders."""
    view = project(state, seat)
    rated = _perceived(events, seat) if view.me.agent is not None else {}
    lines = [f"tick {view.tick}  seat {view.me.name}  slots {view.me.slots}"]
    for intent in view.own_pending:
        targets = ", ".join(object_name(state, t) for t in intent.targets) or "-"
        line = f"  pending {intent.intent_id} {intent.verb.value} -> {targets}"
        if intent.intent_id in rated:
            tick, band = rated[intent.intent_id]
            line += f"; read as {_band(band)} at the tick {tick} cutoff"
        lines.append(line)
    for clock in sorted(view.clocks.values(), key=lambda c: c.name):
        if clock.status.value == "open":
            lines.append(f"  clock {clock.name}: {clock.segments_filled}/{clock.segments_total}")
    for meter in sorted(view.meters.values(), key=lambda m: (m.owner, m.name)):
        lines.append(f"  {object_name(state, meter.owner)} {meter.name}: {meter.value}")
    return lines


__all__ = [
    "ORDER_VISIBILITIES",
    "SPIN_LIMIT",
    "WITHHELD",
    "BriefingOutcome",
    "OrderOutcome",
    "get_briefing",
    "give_order",
    "object_name",
    "order_scope",
    "status_lines",
]
