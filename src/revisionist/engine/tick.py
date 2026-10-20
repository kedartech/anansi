"""The tick orchestrator (README §13; build plan Layer 4).

The only module that both reads the store and calls providers. It drives the
pure core through six stages, each landed in the log as one atomic batch
ending in ``tick_stage_completed``:

    FREEZE    tick_opened; structural validation (intent_rejected); rate and roll
    PROPOSE   resolver sees truth; delta_proposed per intent (quiet tick on failure)
    VALIDATE  clamp per intent, then cap_meters across the tick; delta_rejected
    APPLY     delta_applied for everything kept; operations; scheduled events fire
    NARRATE   narration_written per human seat, from that seat's View only
    DELIVER   briefing_delivered after each send; the key makes retries idempotent
    close     tick_closed with the state hash and the summed costs

Because a stage either landed whole or not at all, resuming is reading the
tail of the log for the current tick and continuing from the first stage
not yet marked complete. Everything a later stage needs from an earlier one
is either in the log (proposals, narrations) or recomputable from state by a
pure function (rolls, clamps), so a resumed run reaches the same hash as an
uninterrupted one.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import Field

from revisionist import __version__
from revisionist.core import SCHEMA_VERSION, goals, intel, referee
from revisionist.core import delta as d
from revisionist.core import events as ev
from revisionist.core._base import Model
from revisionist.core.events import CallCost, Event, Versions
from revisionist.core.fold import replay, state_hash
from revisionist.core.ids import CampaignId, ClockId, DeltaId, EventId, IntentId, SeatId, derived_id
from revisionist.core.intent import Intent
from revisionist.core.ontology import ClockKind, ClockStatus, Controller, SeatKind
from revisionist.core.project import project
from revisionist.core.resolution import OutcomeTier, TickStage
from revisionist.core.state import State, View
from revisionist.core.visibility import PUBLIC, Scope, private_to
from revisionist.engine.ids import new_id
from revisionist.engine.store import EventStore, load_state

# --- provider contracts ------------------------------------------------------


class Proposal(Model):
    intent_id: IntentId | None
    deltas: tuple[d.Delta, ...]


class Resolution(Model):
    proposals: tuple[Proposal, ...]
    costs: tuple[CallCost, ...] = ()


class Narration(Model):
    text: str
    cost: CallCost | None = None


class Resolver(Protocol):
    """The one place truth meets a model (README §12). Layer 5 implements it with Claude."""

    def propose(
        self, state: State, intents: Sequence[Intent], rolls: Mapping[IntentId, referee.Roll]
    ) -> Resolution: ...


class Narrator(Protocol):
    """Takes a ``View``, never a ``State``. Passing truth here is a type error."""

    def narrate(self, view: View, deltas: Sequence[d.Delta]) -> Narration: ...


class Deliverer(Protocol):
    def deliver(self, seat_id: SeatId, text: str, idempotency_key: str) -> str:
        """Send and return the channel name. Must be idempotent on the key."""
        ...


@dataclass(frozen=True)
class Providers:
    resolver: Resolver
    narrator: Narrator
    deliverer: Deliverer
    safety: referee.SafetyCheck | None = None


class TickResult(Model):
    tick: int
    quiet: bool = False
    quiet_reason: str = ""
    resumed_from: TickStage | None = None
    rejected: tuple[IntentId, ...] = ()
    applied: int = 0
    cut: int = 0
    delivered: tuple[SeatId, ...] = ()
    state_hash: str = ""
    stopped_after: TickStage | None = Field(default=None)
    """Set when ``stop_after`` ended the run early (tests only)."""


class TickError(RuntimeError):
    pass


# --- log access --------------------------------------------------------------


class _Emitter:
    """Builds envelopes with dense sequence numbers and folds them as they land."""

    def __init__(self, store: EventStore, state: State, versions: Versions) -> None:
        self.store = store
        self.state = state
        self.versions = versions
        self.next_seq = state.last_seq + 1
        self.roll_for: referee.Roll | None = None

    def event(
        self, payload: ev.Payload, *, actor: SeatId | None = None, scope: Scope = PUBLIC
    ) -> Event:
        e = Event(
            event_id=EventId(new_id()),
            campaign_id=self.state.campaign_id,
            seq=self.next_seq,
            tick=self.state.tick,
            actor=actor,
            scope=scope,
            payload=payload,
            versions=self.versions,
        )
        self.next_seq += 1
        return e

    def commit(self, batch: Sequence[Event]) -> None:
        """Fold first so a bad batch never reaches the store; then append atomically."""
        folded = replay(batch, initial=self.state)
        self.store.append_all(batch)
        self.state = folded


class _Tail(Model):
    """What the log says about the tick in progress."""

    opened: bool = False
    completed: tuple[TickStage, ...] = ()
    proposals: tuple[Proposal, ...] = ()
    narrations: dict[SeatId, tuple[EventId, str]] = Field(default_factory=dict)
    delivered: frozenset[SeatId] = frozenset()
    costs: tuple[CallCost, ...] = ()
    intel: tuple[d.Delta, ...] = ()
    """Goal pieces and decoys the engine applied this tick, for the narrators."""


def _tail(events: Sequence[Event]) -> _Tail:
    start = 0
    for i, e in enumerate(events):
        if e.kind == "tick_closed":
            start = i + 1
    tail = _Tail()
    for e in events[start:]:
        p = e.payload
        match p:
            case ev.TickOpened():
                tail = tail.model_copy(update={"opened": True})
            case ev.TickStageCompleted():
                tail = tail.model_copy(
                    update={
                        "completed": (*tail.completed, p.stage),
                        "costs": (*tail.costs, *p.costs),
                    }
                )
            case ev.DeltaProposed():
                prop = Proposal(intent_id=p.intent_id, deltas=p.deltas)
                tail = tail.model_copy(update={"proposals": (*tail.proposals, prop)})
            case ev.DeltaApplied(delta=d.FactAssert(predicate=pred)) if pred in intel.PIECES:
                tail = tail.model_copy(update={"intel": (*tail.intel, p.delta)})
            case ev.NarrationWritten():
                narrations = dict(tail.narrations)
                narrations[p.seat_id] = (e.event_id, p.text)
                tail = tail.model_copy(update={"narrations": narrations})
            case ev.BriefingDelivered():
                tail = tail.model_copy(update={"delivered": tail.delivered | {p.seat_id}})
            case _:
                pass
    return tail


# --- filing ------------------------------------------------------------------


def file_intent(
    store: EventStore,
    campaign_id: CampaignId,
    intent: Intent,
    *,
    safety: referee.SafetyCheck | None = None,
    versions: Versions | None = None,
    from_order: EventId | None = None,
) -> referee.Rejection | None:
    state = load_state(store, campaign_id)
    em = _Emitter(store, state, versions or _VERSIONS)
    rejection = referee.validate(state, intent, safety)
    if rejection is None:
        payload: ev.Payload = ev.IntentFiled(intent=intent, from_order=from_order)
    else:
        payload = ev.IntentRejected(
            intent_id=intent.intent_id, reason=rejection.reason, detail=rejection.detail
        )
    em.commit([em.event(payload, actor=intent.actor, scope=intent.scope)])
    return rejection


_VERSIONS = Versions(engine=__version__, schema_version=SCHEMA_VERSION)


# --- the tick ----------------------------------------------------------------


def run_tick(
    store: EventStore,
    campaign_id: CampaignId,
    providers: Providers,
    *,
    versions: Versions | None = None,
    stop_after: TickStage | None = None,
    roll_for: referee.Roll | None = None,
) -> TickResult:
    """Run (or resume) one tick. Safe to call again after a crash at any point."""
    versions = versions or _VERSIONS
    state = load_state(store, campaign_id)
    events = list(store.read(campaign_id))
    tail = _tail(events)
    em = _Emitter(store, state, versions)
    em.roll_for = roll_for
    done = set(tail.completed)
    result = TickResult(
        tick=state.tick, resumed_from=tail.completed[-1] if tail.completed else None
    )

    def stop(stage: TickStage) -> bool:
        return stop_after is stage

    # FREEZE --------------------------------------------------------------
    if not tail.opened:
        frozen = tuple(sorted(em.state.pending))
        batch = [em.event(ev.TickOpened(intents_frozen=frozen))]
        em.commit(batch)
        tail = tail.model_copy(update={"opened": True})

    rejected: list[IntentId] = []
    if TickStage.FREEZE not in done:
        batch = []
        for intent_id in em.state.frozen:
            intent = em.state.pending[intent_id]
            rej = referee.validate(em.state, intent, providers.safety, filed=True)
            if rej is not None:
                rejected.append(intent_id)
                batch.append(
                    em.event(
                        ev.IntentRejected(
                            intent_id=intent_id, reason=rej.reason, detail=rej.detail
                        ),
                        actor=intent.actor,
                        scope=intent.scope,
                    )
                )
                continue
            band = referee.rate(em.state, intent)
            perceived = referee.rate(project(em.state, intent.actor), intent)
            r = _roll(em.state, intent)
            batch.append(
                em.event(
                    ev.FeasibilityRated(intent_id=intent_id, band=band, perceived=perceived),
                    actor=intent.actor,
                    scope=intent.scope,
                )
            )
            batch.append(
                em.event(
                    ev.Rolled(
                        intent_id=intent_id,
                        seed=r.seed,
                        outcome=r.outcome,
                        exposure_roll=r.exposure_roll,
                        exposed=r.exposed,
                    ),
                    actor=intent.actor,
                    scope=intent.scope,
                )
            )
        # Spin goes public once, when its order first freezes; an operation that
        # runs on is frozen again each tick but says nothing new.
        stated = {e.payload.intent_id for e in events if isinstance(e.payload, ev.StatementIssued)}
        for intent_id in em.state.frozen:
            intent = em.state.pending[intent_id]
            if intent.spin and intent_id not in rejected and intent_id not in stated:
                batch.append(
                    em.event(
                        ev.StatementIssued(
                            seat_id=intent.actor, intent_id=intent_id, text=intent.spin
                        ),
                        actor=intent.actor,
                    )
                )
        batch.append(em.event(ev.TickStageCompleted(stage=TickStage.FREEZE)))
        em.commit(batch)
        done.add(TickStage.FREEZE)
        result = result.model_copy(update={"rejected": tuple(rejected)})
        if stop(TickStage.FREEZE):
            return result.model_copy(update={"stopped_after": TickStage.FREEZE})

    intents = [em.state.pending[i] for i in em.state.frozen]
    rolls = {i.intent_id: _roll(em.state, i) for i in intents}

    # PROPOSE -------------------------------------------------------------
    if TickStage.PROPOSE not in done:
        resolution: Resolution | None = None
        reason = ""
        for _attempt in range(2):
            try:
                resolution = providers.resolver.propose(em.state, intents, rolls)
                break
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"[:200]
        if resolution is None:
            return _close(em, tail.costs, quiet=True, reason=reason, result=result)
        batch = []
        for prop in resolution.proposals:
            scope = PUBLIC
            actor = None
            if prop.intent_id is not None:
                src = em.state.pending[prop.intent_id]
                scope, actor = src.scope, src.actor
            batch.append(
                em.event(
                    ev.DeltaProposed(intent_id=prop.intent_id, deltas=prop.deltas),
                    actor=actor,
                    scope=scope,
                )
            )
        batch.append(
            em.event(ev.TickStageCompleted(stage=TickStage.PROPOSE, costs=resolution.costs))
        )
        em.commit(batch)
        done.add(TickStage.PROPOSE)
        tail = tail.model_copy(
            update={
                "proposals": resolution.proposals,
                "costs": (*tail.costs, *resolution.costs),
            }
        )
        if stop(TickStage.PROPOSE):
            return result.model_copy(update={"stopped_after": TickStage.PROPOSE})

    # VALIDATE ------------------------------------------------------------
    kept, cuts = _clamp_all(em.state, tail.proposals, rolls)
    if TickStage.VALIDATE not in done:
        batch = [
            em.event(
                ev.DeltaRejected(delta_id=c.delta_id, reason=c.reason, detail=c.detail),
            )
            for c in cuts
        ]
        batch.append(em.event(ev.TickStageCompleted(stage=TickStage.VALIDATE)))
        em.commit(batch)
        done.add(TickStage.VALIDATE)
        if stop(TickStage.VALIDATE):
            return result.model_copy(update={"cut": len(cuts), "stopped_after": TickStage.VALIDATE})
    result = result.model_copy(update={"cut": len(cuts)})

    # APPLY ---------------------------------------------------------------
    if TickStage.APPLY not in done:
        batch = []
        applied: list[d.Delta] = []
        for source, delta in kept:
            batch.append(em.event(ev.DeltaApplied(intent_id=source, delta=delta)))
            applied.append(delta)
        batch.extend(_operations(em, intents, rolls, providers.resolver))
        found, learned = _intel(em, intents, rolls)
        tail = tail.model_copy(update={"intel": (*tail.intel, *learned)})
        batch.extend(found)
        batch.extend(_scheduled(em))
        batch.extend(_goals(em))
        batch.append(em.event(ev.TickStageCompleted(stage=TickStage.APPLY)))
        em.commit(batch)
        done.add(TickStage.APPLY)
        if stop(TickStage.APPLY):
            return result.model_copy(
                update={"applied": len(kept), "stopped_after": TickStage.APPLY}
            )
    result = result.model_copy(update={"applied": len(kept)})

    # NARRATE -------------------------------------------------------------
    if TickStage.NARRATE not in done:
        batch = []
        costs: list[CallCost] = []
        applied_deltas = [delta for _, delta in kept] + list(tail.intel)
        for seat_id in _narration_seats(em.state):
            view = project(em.state, seat_id)
            visible = [x for x in applied_deltas if _in_view(view, x)]
            narration = providers.narrator.narrate(view, visible)
            if narration.cost is not None:
                costs.append(narration.cost)
            seat = em.state.seats[seat_id]
            scope = PUBLIC if seat.kind is SeatKind.OUTLET else private_to(seat_id)
            batch.append(
                em.event(
                    ev.NarrationWritten(
                        seat_id=seat_id,
                        text=narration.text,
                        from_deltas=tuple(x.delta_id for x in visible),
                    ),
                    actor=seat_id,
                    scope=scope,
                )
            )
        batch.append(em.event(ev.TickStageCompleted(stage=TickStage.NARRATE, costs=tuple(costs))))
        em.commit(batch)
        done.add(TickStage.NARRATE)
        tail = tail.model_copy(update={"costs": (*tail.costs, *costs)})
        if stop(TickStage.NARRATE):
            return result.model_copy(update={"stopped_after": TickStage.NARRATE})

    # DELIVER -------------------------------------------------------------
    narrations = _narrations(em, store, campaign_id, tail)
    delivered: list[SeatId] = []
    if TickStage.DELIVER not in done:
        already = set(_tail(list(store.read(campaign_id))).delivered)
        for seat_id, (narration_id, text) in sorted(narrations.items()):
            if seat_id in already:
                continue
            key = derived_id(campaign_id, em.state.tick, "deliver", seat_id)
            channel = providers.deliverer.deliver(seat_id, text, key)
            em.commit(
                [
                    em.event(
                        ev.BriefingDelivered(
                            seat_id=seat_id,
                            narration=narration_id,
                            channel=channel,
                            idempotency_key=key,
                        ),
                        actor=seat_id,
                        scope=private_to(seat_id),
                    )
                ]
            )
            delivered.append(seat_id)
        em.commit([em.event(ev.TickStageCompleted(stage=TickStage.DELIVER))])
        done.add(TickStage.DELIVER)
        result = result.model_copy(update={"delivered": tuple(delivered)})
        if stop(TickStage.DELIVER):
            return result.model_copy(update={"stopped_after": TickStage.DELIVER})

    return _close(em, tail.costs, quiet=False, reason="", result=result)


# --- stage helpers -----------------------------------------------------------


def _roll(state: State, intent: Intent) -> referee.Roll:
    seed = referee.seed_for(state.campaign_id, state.tick, intent.intent_id)
    exposure = 0
    for c in state.clocks.values():
        if c.owner == intent.actor and c.status is ClockStatus.OPEN:
            exposure = max(exposure, c.exposure)
    return referee.roll(seed, referee.rate(state, intent), exposure)


def _clamp_all(
    state: State, proposals: Sequence[Proposal], rolls: Mapping[IntentId, referee.Roll]
) -> tuple[list[tuple[IntentId | None, d.Delta]], list[referee.Cut]]:
    """Per-intent clamp, then the cross-intent meter cap. Pure; safe to recompute on resume."""
    kept: list[tuple[IntentId | None, d.Delta]] = []
    cuts: list[referee.Cut] = []
    for prop in proposals:
        intent = state.pending.get(prop.intent_id) if prop.intent_id is not None else None
        outcome = (
            rolls[prop.intent_id].outcome
            if prop.intent_id is not None and prop.intent_id in rolls
            else OutcomeTier.SUCCESS
        )
        clamped = referee.clamp(state, intent, prop.deltas, outcome)
        cuts.extend(clamped.cut)
        kept.extend((prop.intent_id, x) for x in clamped.kept)
    capped = referee.cap_meters(state, [x for _, x in kept])
    cuts.extend(capped.cut)
    by_id = {x.delta_id: x for x in capped.kept}
    kept = [(iid, by_id[x.delta_id]) for iid, x in kept if x.delta_id in by_id]
    folds = referee.admissible(state, [x for _, x in kept])
    cuts.extend(folds.cut)
    ok = {x.delta_id for x in folds.kept}
    kept = [(iid, x) for iid, x in kept if x.delta_id in ok]
    return kept, cuts


def _operations(
    em: _Emitter,
    intents: Sequence[Intent],
    rolls: Mapping[IntentId, referee.Roll],
    resolver: Resolver,
) -> list[Event]:
    """Open an operation clock for each surviving multi-tick slot-holding intent,
    then advance every open operation (old and new) by one segment.

    A running operation is re-rated and re-rolled each tick from its own seed
    (README: the world keeps moving). A failure stops it; a success that fills
    the clock logs ``operation_resolved`` with the outcome actually rolled.
    """
    out: list[Event] = []
    state = em.state
    tick = state.tick
    running: list[tuple[ClockId, IntentId | None, SeatId | None, Scope, bool]] = [
        (c.clock_id, c.intent_id, c.owner, c.scope, c.segments_filled + 1 >= c.segments_total)
        for c in sorted(state.clocks.values(), key=lambda c: c.clock_id)
        if c.kind is ClockKind.OPERATION and c.status is ClockStatus.OPEN
    ]
    for intent in intents:
        r = rolls[intent.intent_id]
        if (
            not intent.holds_slot
            or intent.duration_ticks <= 1
            or r.outcome is OutcomeTier.FAIL_COMPLICATION
        ):
            continue
        clock_id = ClockId(derived_id(state.campaign_id, tick, "op", intent.intent_id))
        already = any(c.intent_id == intent.intent_id for c in state.clocks.values())
        if clock_id in state.clocks or already:
            continue
        create = d.ClockCreate(
            delta_id=DeltaId(derived_id(state.campaign_id, tick, "opc", intent.intent_id)),
            reason="operation opened",
            clock_id=clock_id,
            clock_kind=ClockKind.OPERATION,
            name=f"{intent.verb.value} operation"[:80],
            owner=intent.actor,
            intent_id=intent.intent_id,
            segments_total=intent.duration_ticks,
            scope=intent.scope,
        )
        opened = ev.OperationOpened(
            intent_id=intent.intent_id, clock_id=clock_id, seat_id=intent.actor
        )
        for payload in (ev.DeltaApplied(intent_id=intent.intent_id, delta=create), opened):
            out.append(em.event(payload, actor=intent.actor, scope=intent.scope))
        running.append((clock_id, intent.intent_id, intent.actor, intent.scope, False))
    for clock_id, intent_id, owner, scope, done in running:
        outcome = _reroll(em, resolver, intent_id, rolls)
        if outcome is OutcomeTier.FAIL_COMPLICATION:
            _abandon(em, out, clock_id, intent_id, owner, scope)
            continue
        _advance(em, out, clock_id, intent_id, owner, scope, done=done, outcome=outcome)
    return out


def _reroll(
    em: _Emitter,
    resolver: Resolver,
    intent_id: IntentId | None,
    rolls: Mapping[IntentId, referee.Roll],
) -> OutcomeTier:
    """Re-rate and re-roll one running operation. Falls back to its opening roll."""
    if intent_id is None or intent_id not in em.state.pending:
        return OutcomeTier.SUCCESS
    intent = em.state.pending[intent_id]
    fresh = em.roll_for if em.roll_for is not None else _roll(em.state, intent)
    rolls_now = {intent_id: fresh}
    try:
        resolver.propose(em.state, [intent], rolls_now)
    except Exception:
        return rolls[intent_id].outcome if intent_id in rolls else OutcomeTier.SUCCESS
    return fresh.outcome


def _abandon(
    em: _Emitter,
    out: list[Event],
    clock_id: ClockId,
    intent_id: IntentId | None,
    owner: SeatId | None,
    scope: Scope,
) -> None:
    """A re-roll came up a failure: the operation stops and frees its slot."""
    fail = d.ClockStatusSet(
        delta_id=DeltaId(derived_id(em.state.campaign_id, em.state.tick, "opf", clock_id)),
        reason="operation failed its re-roll",
        clock_id=clock_id,
        status=ClockStatus.FAILED,
    )
    out.append(em.event(ev.DeltaApplied(intent_id=intent_id, delta=fail), actor=owner, scope=scope))


def _advance(
    em: _Emitter,
    out: list[Event],
    clock_id: ClockId,
    intent_id: IntentId | None,
    owner: SeatId | None,
    scope: Scope,
    *,
    done: bool,
    outcome: OutcomeTier = OutcomeTier.SUCCESS,
) -> None:
    tick = em.state.tick
    advance = d.ClockAdvance(
        delta_id=DeltaId(derived_id(em.state.campaign_id, tick, "opa", clock_id)),
        reason="operation runs",
        clock_id=clock_id,
        segments=1,
    )
    out.append(
        em.event(ev.DeltaApplied(intent_id=intent_id, delta=advance), actor=owner, scope=scope)
    )
    if done and intent_id is not None and owner is not None:
        out.append(
            em.event(
                ev.OperationResolved(
                    intent_id=intent_id,
                    clock_id=clock_id,
                    seat_id=owner,
                    outcome=outcome,
                ),
                actor=owner,
                scope=scope,
            )
        )


def _scheduled(em: _Emitter) -> list[Event]:
    state = em.state
    return [
        em.event(ev.ScheduledEventFired(scheduled_id=s.scheduled_id), scope=s.scope)
        for s in sorted(state.scheduled.values(), key=lambda s: s.scheduled_id)
        if not s.fired and s.fire_at_tick <= state.tick
    ]


def _intel(
    em: _Emitter, intents: Sequence[Intent], rolls: Mapping[IntentId, referee.Roll]
) -> tuple[list[Event], list[d.Delta]]:
    """Goal pieces for investigations and decoys for forgeries, from engine code only."""
    out: list[Event] = []
    learned: list[d.Delta] = []
    for intent in intents:
        r = rolls[intent.intent_id]
        found = intel.piece(em.state, intent, r.outcome, r.seed)
        made = [found] if found is not None else intel.decoys(em.state, intent, r.outcome)
        for delta in made:
            applied = ev.DeltaApplied(intent_id=intent.intent_id, delta=delta)
            out.append(em.event(applied, actor=intent.actor))
            learned.append(delta)
    return out, learned


def _goals(em: _Emitter) -> list[Event]:
    """Goal changes at the end of APPLY, each private to the goal's owner."""
    out: list[Event] = []
    for change in goals.check(em.state):
        owner = em.state.goals[change.goal_id].seat_id
        out.append(em.event(change, scope=private_to(owner)))
    return out


def _narration_seats(state: State) -> list[SeatId]:
    """Humans get a private briefing; outlets get a public one from their own view.

    An outlet only ever narrates what its view contains, so a press seat learns a
    secret exactly when someone leaked it and not before (README §11).
    """
    return sorted(
        s.seat_id
        for s in state.seats.values()
        if s.controller is Controller.HUMAN or s.kind is SeatKind.OUTLET
    )


def _in_view(view: View, delta: d.Delta) -> bool:
    obj = referee.touched(delta)
    return (
        obj in view.entities
        or obj in view.assets
        or obj in view.relationships
        or obj in view.meters
        or obj in view.clocks
        or obj in view.facts
        or obj in view.commitments
        or obj in view.scheduled
        or obj in view.seats
    )


def _narrations(
    em: _Emitter, store: EventStore, campaign_id: CampaignId, tail: _Tail
) -> dict[SeatId, tuple[EventId, str]]:
    if tail.narrations:
        return dict(tail.narrations)
    return dict(_tail(list(store.read(campaign_id))).narrations)


def _close(
    em: _Emitter, costs: Sequence[CallCost], *, quiet: bool, reason: str, result: TickResult
) -> TickResult:
    digest = state_hash(em.state)
    closed = ev.TickClosed(quiet=quiet, quiet_reason=reason, costs=tuple(costs), state_hash=digest)
    em.commit([em.event(closed)])
    em.store.save_snapshot(em.state)
    return result.model_copy(update={"quiet": quiet, "quiet_reason": reason, "state_hash": digest})


__all__ = [
    "Deliverer",
    "Narration",
    "Narrator",
    "Proposal",
    "Providers",
    "Resolution",
    "Resolver",
    "TickError",
    "TickResult",
    "file_intent",
    "run_tick",
]
