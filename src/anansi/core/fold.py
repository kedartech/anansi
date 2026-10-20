"""The fold: events in, state out (README #19, build plan Layer 2).

``apply`` is total over the mutating kinds and raises ``FoldError`` on
anything it cannot honour: a sequence gap, an unknown ID, a hash mismatch at
``tick_closed``. The log is never silently skipped, because a skipped event
is a state nobody can reproduce.

Bounds applied here are the *type* bounds of the ontology (tier 0-5, meter
lo-hi, disposition -5..+5). The clamp table (Layer 3) is policy and runs
before a delta is ever logged as applied; the fold only guarantees that a
replay can never produce an invalid object.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping

from anansi.core import delta as d
from anansi.core import events as ev
from anansi.core.events import MUTATING_KINDS, Event
from anansi.core.ids import CampaignId
from anansi.core.ontology import (
    Asset,
    Clock,
    ClockKind,
    ClockStatus,
    Commitment,
    CommitmentStatus,
    Fact,
    ScheduledEvent,
    SeatStatus,
)
from anansi.core.state import State
from anansi.core.visibility import PUBLIC, Scope


class FoldError(ValueError):
    """The log cannot be folded. Reproduces on every replay; never caught in play."""


def _put[K, V](items: Mapping[K, V], key: K, value: V) -> dict[K, V]:
    out = dict(items)
    out[key] = value
    return out


def _need[K, V](items: Mapping[K, V], key: K, what: str) -> V:
    try:
        return items[key]
    except KeyError:
        raise FoldError(f"unknown {what} {key!r}") from None


def _fresh[K, V](items: Mapping[K, V], key: K, what: str) -> None:
    if key in items:
        raise FoldError(f"duplicate {what} {key!r}")


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def empty(campaign_id: CampaignId) -> State:
    return State(campaign_id=campaign_id)


def state_hash(state: State) -> str:
    """blake2b-128 over canonical JSON. Set fields serialise sorted (see ``_base``)."""
    canon = json.dumps(
        state.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.blake2b(canon.encode(), digest_size=16).hexdigest()


def apply(state: State, event: Event) -> State:
    if event.campaign_id != state.campaign_id:
        raise FoldError(f"event {event.event_id} belongs to campaign {event.campaign_id}")
    if event.seq != state.last_seq + 1:
        raise FoldError(f"sequence gap: expected {state.last_seq + 1}, got {event.seq}")
    if event.kind in MUTATING_KINDS:
        state = _mutate(state, event)
    return state.model_copy(update={"last_seq": event.seq})


def replay(events: Iterable[Event], *, initial: State | None = None) -> State:
    state = initial
    for event in events:
        if state is None:
            state = empty(event.campaign_id)
        state = apply(state, event)
    if state is None:
        raise FoldError("nothing to replay")
    return state


def replay_to(events: Iterable[Event], tick: int, *, initial: State | None = None) -> State:
    """State at the end of ``tick`` (every event with ``event.tick <= tick``)."""
    return replay((e for e in events if e.tick <= tick), initial=initial)


# --- payloads -----------------------------------------------------------------


def _mutate(state: State, event: Event) -> State:
    p = event.payload
    match p:
        case ev.CampaignCreated():
            return state
        case ev.SettingsSet():
            return state.model_copy(update={"settings": p.settings})
        case ev.EntityCreated():
            _fresh(state.entities, p.entity.entity_id, "entity")
            return state.model_copy(
                update={"entities": _put(state.entities, p.entity.entity_id, p.entity)}
            )
        case ev.AssetCreated():
            _fresh(state.assets, p.asset.asset_id, "asset")
            return state.model_copy(
                update={"assets": _put(state.assets, p.asset.asset_id, p.asset)}
            )
        case ev.RelationshipSet():
            r = p.relationship
            return state.model_copy(
                update={"relationships": _put(state.relationships, r.relationship_id, r)}
            )
        case ev.MeterCreated():
            _fresh(state.meters, p.meter.meter_id, "meter")
            return state.model_copy(
                update={"meters": _put(state.meters, p.meter.meter_id, p.meter)}
            )
        case ev.ClockCreated():
            _fresh(state.clocks, p.clock.clock_id, "clock")
            return state.model_copy(
                update={"clocks": _put(state.clocks, p.clock.clock_id, p.clock)}
            )
        case ev.FactAsserted():
            _fresh(state.facts, p.fact.fact_id, "fact")
            return state.model_copy(update={"facts": _put(state.facts, p.fact.fact_id, p.fact)})
        case ev.CommitmentCreated():
            c = p.commitment
            _fresh(state.commitments, c.commitment_id, "commitment")
            return state.model_copy(
                update={"commitments": _put(state.commitments, c.commitment_id, c)}
            )
        case ev.SeatCreated():
            _fresh(state.seats, p.seat.seat_id, "seat")
            return state.model_copy(update={"seats": _put(state.seats, p.seat.seat_id, p.seat)})
        case ev.AgentAssigned():
            seat = _need(state.seats, p.seat_id, "seat")
            _need(state.entities, p.agent, "agent entity")
            seat = seat.model_copy(
                update={"agent": p.agent, "persona": p.persona, "status": SeatStatus.ACTIVE}
            )
            return state.model_copy(update={"seats": _put(state.seats, seat.seat_id, seat)})
        case ev.SeatStatusSet():
            seat = _need(state.seats, p.seat_id, "seat")
            update: dict[str, object] = {"status": p.status}
            if p.status is SeatStatus.BLINDED:
                update["agent"] = None
            seat = seat.model_copy(update=update)
            return state.model_copy(update={"seats": _put(state.seats, seat.seat_id, seat)})
        case ev.IntentFiled():
            i = p.intent
            _fresh(state.pending, i.intent_id, "intent")
            _need(state.seats, i.actor, "seat")
            return state.model_copy(update={"pending": _put(state.pending, i.intent_id, i)})
        case ev.IntentRejected():
            if p.intent_id not in state.pending:
                return state  # rejected at filing: a record, nothing to remove
            pending = {k: v for k, v in state.pending.items() if k != p.intent_id}
            frozen = tuple(i for i in state.frozen if i != p.intent_id)
            return state.model_copy(update={"pending": pending, "frozen": frozen})
        case ev.DeltaApplied():
            return _apply_delta(state, p.delta, event.tick)
        case ev.ScheduledEventFired():
            s = _need(state.scheduled, p.scheduled_id, "scheduled event")
            s = s.model_copy(update={"fired": True})
            return state.model_copy(update={"scheduled": _put(state.scheduled, s.scheduled_id, s)})
        case ev.TickOpened():
            for intent_id in p.intents_frozen:
                _need(state.pending, intent_id, "intent")
            return state.model_copy(update={"tick": event.tick, "frozen": p.intents_frozen})
        case ev.TickClosed():
            actual = state_hash(state)
            if actual != p.state_hash:
                raise FoldError(
                    f"state hash mismatch at tick {event.tick}: log says {p.state_hash}, "
                    f"replay gives {actual}"
                )
            done = set(state.frozen)
            if p.quiet:
                pending = dict(state.pending)  # nothing resolved; intents carry forward
            else:
                pending = {k: v for k, v in state.pending.items() if k not in done}
            return state.model_copy(
                update={"pending": pending, "frozen": (), "tick": event.tick + 1}
            )
        case _:
            raise FoldError(f"mutating kind {event.kind!r} has no fold case")


# --- deltas -------------------------------------------------------------------


def _apply_delta(state: State, delta: d.Delta, tick: int) -> State:
    match delta:
        case d.AssetTierDelta():
            a = _need(state.assets, delta.asset_id, "asset")
            a = a.model_copy(update={"tier": _clamp(a.tier + delta.change, 0, 5)})
            return state.model_copy(update={"assets": _put(state.assets, a.asset_id, a)})
        case d.AssetStatusSet():
            a = _need(state.assets, delta.asset_id, "asset")
            a = a.model_copy(update={"status": delta.status})
            return state.model_copy(update={"assets": _put(state.assets, a.asset_id, a)})
        case d.AssetControllerSet():
            a = _need(state.assets, delta.asset_id, "asset")
            _need(state.entities, delta.controller, "entity")
            a = a.model_copy(update={"controller": delta.controller})
            return state.model_copy(update={"assets": _put(state.assets, a.asset_id, a)})
        case d.AssetCreate():
            _fresh(state.assets, delta.asset_id, "asset")
            _need(state.entities, delta.controller, "entity")
            a = Asset(
                asset_id=delta.asset_id,
                kind=delta.asset_kind,
                name=delta.name,
                tier=delta.tier,
                controller=delta.controller,
                scope=delta.scope,
            )
            return state.model_copy(update={"assets": _put(state.assets, a.asset_id, a)})
        case d.MeterDelta():
            m = _need(state.meters, delta.meter_id, "meter")
            m = m.model_copy(update={"value": _clamp(m.value + delta.change, m.lo, m.hi)})
            return state.model_copy(update={"meters": _put(state.meters, m.meter_id, m)})
        case d.DispositionDelta():
            r = _need(state.relationships, delta.relationship_id, "relationship")
            r = r.model_copy(update={"disposition": _clamp(r.disposition + delta.change, -5, 5)})
            return state.model_copy(
                update={"relationships": _put(state.relationships, r.relationship_id, r)}
            )
        case d.RelationshipTagSet():
            r = _need(state.relationships, delta.relationship_id, "relationship")
            r = r.model_copy(
                update={
                    "price_tags": r.price_tags | delta.add_price,
                    "leverage_tags": (r.leverage_tags | delta.add_leverage) - delta.remove_leverage,
                }
            )
            return state.model_copy(
                update={"relationships": _put(state.relationships, r.relationship_id, r)}
            )
        case d.ClockAdvance():
            c = _need(state.clocks, delta.clock_id, "clock")
            filled = _clamp(c.segments_filled + delta.segments, 0, c.segments_total)
            status = c.status
            if status is ClockStatus.OPEN and filled >= c.segments_total:
                status = ClockStatus.RESOLVED
            c = c.model_copy(
                update={
                    "segments_filled": filled,
                    "status": status,
                    "holds_slot": c.holds_slot and status is ClockStatus.OPEN,
                }
            )
            return state.model_copy(update={"clocks": _put(state.clocks, c.clock_id, c)})
        case d.ClockCreate():
            _fresh(state.clocks, delta.clock_id, "clock")
            if delta.owner is not None:
                _need(state.seats, delta.owner, "seat")
            c = Clock(
                clock_id=delta.clock_id,
                kind=delta.clock_kind,
                name=delta.name,
                owner=delta.owner,
                target=delta.target,
                intent_id=delta.intent_id,
                segments_total=delta.segments_total,
                exposure=delta.exposure,
                opened_tick=tick,
                holds_slot=delta.clock_kind is ClockKind.OPERATION and delta.owner is not None,
                scope=delta.scope,
            )
            return state.model_copy(update={"clocks": _put(state.clocks, c.clock_id, c)})
        case d.ClockStatusSet():
            c = _need(state.clocks, delta.clock_id, "clock")
            c = c.model_copy(update={"status": delta.status, "holds_slot": False})
            return state.model_copy(update={"clocks": _put(state.clocks, c.clock_id, c)})
        case d.ClockExposureDelta():
            c = _need(state.clocks, delta.clock_id, "clock")
            c = c.model_copy(update={"exposure": _clamp(c.exposure + delta.change, 0, 5)})
            return state.model_copy(update={"clocks": _put(state.clocks, c.clock_id, c)})
        case d.FactAssert():
            _fresh(state.facts, delta.fact_id, "fact")
            _need(state.entities, delta.subject, "entity")
            f = Fact(
                fact_id=delta.fact_id,
                subject=delta.subject,
                predicate=delta.predicate,
                obj=delta.obj,
                truth=delta.truth,
                exposure=delta.exposure,
                asserted_tick=tick,
                scope=delta.scope,
            )
            return state.model_copy(update={"facts": _put(state.facts, f.fact_id, f)})
        case d.KnowledgeGrant():
            return _grant(state, delta)
        case d.EntityStatusSet():
            e = _need(state.entities, delta.entity_id, "entity")
            e = e.model_copy(update={"status": delta.status})
            return state.model_copy(update={"entities": _put(state.entities, e.entity_id, e)})
        case d.CommitmentCreate():
            _fresh(state.commitments, delta.commitment_id, "commitment")
            for party in delta.parties:
                _need(state.entities, party, "entity")
            c2 = Commitment(
                commitment_id=delta.commitment_id,
                parties=delta.parties,
                clauses=delta.clauses,
                scope=delta.scope,
            )
            return state.model_copy(
                update={"commitments": _put(state.commitments, c2.commitment_id, c2)}
            )
        case d.CommitmentStatusSet():
            c2 = _need(state.commitments, delta.commitment_id, "commitment")
            c2 = c2.model_copy(update={"status": CommitmentStatus(delta.status)})
            return state.model_copy(
                update={"commitments": _put(state.commitments, c2.commitment_id, c2)}
            )
        case d.ScheduledEventQueue():
            _fresh(state.scheduled, delta.scheduled_id, "scheduled event")
            s = ScheduledEvent(
                scheduled_id=delta.scheduled_id,
                fire_at_tick=tick + delta.fire_in_ticks,
                name=delta.name,
                source_clock=delta.source_clock,
                scope=delta.scope,
            )
            return state.model_copy(update={"scheduled": _put(state.scheduled, s.scheduled_id, s)})
        case d.SeatSlotsDelta():
            seat = _need(state.seats, delta.seat_id, "seat")
            cap = state.settings.slot_cap
            seat = seat.model_copy(update={"slots": _clamp(seat.slots + delta.change, 0, cap)})
            return state.model_copy(update={"seats": _put(state.seats, seat.seat_id, seat)})
        case _:
            raise FoldError(f"delta kind {delta.kind!r} has no fold case")


def _widen(scope: Scope, grant: d.KnowledgeGrant) -> Scope:
    return PUBLIC if grant.make_public else scope.grant(*grant.seats)


def _grant(state: State, grant: d.KnowledgeGrant) -> State:
    target = grant.target
    if target in state.facts:
        f = state.facts[target]  # type: ignore[index]
        f = f.model_copy(update={"scope": _widen(f.scope, grant)})
        return state.model_copy(update={"facts": _put(state.facts, f.fact_id, f)})
    if target in state.assets:
        a = state.assets[target]  # type: ignore[index]
        a = a.model_copy(update={"scope": _widen(a.scope, grant)})
        return state.model_copy(update={"assets": _put(state.assets, a.asset_id, a)})
    if target in state.clocks:
        c = state.clocks[target]  # type: ignore[index]
        c = c.model_copy(update={"scope": _widen(c.scope, grant)})
        return state.model_copy(update={"clocks": _put(state.clocks, c.clock_id, c)})
    if target in state.commitments:
        cm = state.commitments[target]  # type: ignore[index]
        cm = cm.model_copy(update={"scope": _widen(cm.scope, grant)})
        return state.model_copy(
            update={"commitments": _put(state.commitments, cm.commitment_id, cm)}
        )
    raise FoldError(f"knowledge grant target {target!r} is not a fact, asset, clock or commitment")
