"""The fold: events in, state out (spec #19, build plan Layer 2).

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

from revisionist.core import delta as d
from revisionist.core import events as ev
from revisionist.core.events import MUTATING_KINDS, Event
from revisionist.core.goals import GoalKind, season_winners
from revisionist.core.ids import CampaignId
from revisionist.core.ontology import (
    TRACK_MAX,
    Asset,
    Clock,
    ClockKind,
    ClockStatus,
    Commitment,
    CommitmentStatus,
    Controller,
    EntityKind,
    Fact,
    Phase,
    ScheduledEvent,
    Seat,
    SeatKind,
    SeatStatus,
)
from revisionist.core.realism.document import Document
from revisionist.core.realism.pipeline import ADVANCES, SETTLED, OrderStatus, StepAction
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC, Scope


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
    if isinstance(event.payload, ev.Sealed) and event.payload.holder == "campaign":
        raise FoldError(f"event {event.event_id} is still sealed: its campaign key is missing")
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
            return state.model_copy(update={"ruleset": p.ruleset})
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
            if s.skipped:
                raise FoldError(f"scheduled event {s.scheduled_id!r} was skipped")
            s = s.model_copy(update={"fired": True})
            return state.model_copy(update={"scheduled": _put(state.scheduled, s.scheduled_id, s)})
        case ev.ScheduledEventCreated():
            s = p.scheduled
            _fresh(state.scheduled, s.scheduled_id, "scheduled event")
            if s.fired or s.skipped:
                raise FoldError(f"scheduled event {s.scheduled_id!r} must start pending")
            return state.model_copy(update={"scheduled": _put(state.scheduled, s.scheduled_id, s)})
        case ev.ScheduledEventSkipped():
            s = _need(state.scheduled, p.scheduled_id, "scheduled event")
            if s.fired or s.skipped:
                raise FoldError(f"scheduled event {s.scheduled_id!r} is already settled")
            s = s.model_copy(update={"skipped": True})
            return state.model_copy(update={"scheduled": _put(state.scheduled, s.scheduled_id, s)})
        case ev.ContestCreated():
            k = p.contest
            _fresh(state.contests, k.contest_id, "contest")
            for side in k.sides:
                _need(state.entities, side, "entity")
            if k.decided:
                raise FoldError(f"contest {k.contest_id!r} must start undecided")
            return state.model_copy(update={"contests": _put(state.contests, k.contest_id, k)})
        case ev.ContestMoved():
            k = _need(state.contests, p.contest_id, "contest")
            if k.decided:
                raise FoldError(f"contest {k.contest_id!r} is decided")
            k = k.model_copy(
                update={
                    "track": _clamp(k.track + p.change, k.floor, TRACK_MAX),
                    "countdown_days": max(0, k.countdown_days - p.days),
                }
            )
            return state.model_copy(update={"contests": _put(state.contests, k.contest_id, k)})
        case ev.ContestDecided():
            k = _need(state.contests, p.contest_id, "contest")
            if k.decided:
                raise FoldError(f"contest {k.contest_id!r} is already decided")
            if p.winner is not None and p.winner not in k.sides:
                raise FoldError(f"contest {k.contest_id!r}: winner is not a side")
            k = k.model_copy(update={"decided": True, "winner": p.winner, "countdown_days": 0})
            return state.model_copy(update={"contests": _put(state.contests, k.contest_id, k)})
        case ev.DivergenceChanged():
            return state.model_copy(update={"divergence": max(0, state.divergence + p.change)})
        case ev.DateAdvanced():
            if state.in_world_date is not None and p.to < state.in_world_date:
                raise FoldError("the in-world date never runs backwards")
            return state.model_copy(update={"in_world_date": p.to})
        case ev.SessionZeroOpened():
            humans = any(x.controller is Controller.HUMAN for x in state.seats.values())
            if state.tick != 0 or state.phase is not Phase.PLAY or humans:
                raise FoldError("Session Zero opens once, before anyone plays")
            return state.model_copy(update={"phase": Phase.SESSION_ZERO})
        case ev.CharacterListed():
            ch = p.character
            _fresh(state.roster, ch.entity_id, "character")
            person = _need(state.entities, ch.entity_id, "entity")
            if person.kind is not EntityKind.PERSON:
                raise FoldError(f"character {ch.entity_id} is not a person")
            for asset_id in ch.means:
                if _need(state.assets, asset_id, "asset").controller != ch.entity_id:
                    raise FoldError(f"character {ch.entity_id} does not control {asset_id}")
            return state.model_copy(update={"roster": _put(state.roster, ch.entity_id, ch)})
        case ev.CharacterClaimed():
            if state.phase is not Phase.SESSION_ZERO:
                raise FoldError("a character is claimed during Session Zero")
            ch = _need(state.roster, p.entity_id, "character")
            if ch.claimed_by is not None:
                raise FoldError(f"character {p.entity_id} is already claimed")
            _fresh(state.seats, p.seat_id, "seat")
            taken = sum(x.controller is Controller.HUMAN for x in state.seats.values())
            if taken >= state.settings.max_human_seats:
                raise FoldError("every human seat is taken")
            seat = Seat(
                seat_id=p.seat_id,
                kind=SeatKind.PLAYER,
                name=state.entities[p.entity_id].name,
                embodies=p.entity_id,
                controller=Controller.HUMAN,
                slots=state.settings.starting_slots,
                joined_tick=state.tick,
            )
            ch = ch.model_copy(update={"claimed_by": p.seat_id})
            return state.model_copy(
                update={
                    "seats": _put(state.seats, seat.seat_id, seat),
                    "roster": _put(state.roster, ch.entity_id, ch),
                }
            )
        case ev.SessionZeroClosed():
            if state.phase is not Phase.SESSION_ZERO:
                raise FoldError("Session Zero is not open")
            return state.model_copy(update={"phase": Phase.PLAY})
        case ev.SeasonEnded():
            if state.phase is not Phase.PLAY or state.frozen:
                raise FoldError("a season ends between ticks, in play")
            if p.winners != tuple(sorted(season_winners(state))):
                raise FoldError("season_ended names other winners than the goals do")
            public = {k: g.model_copy(update={"scope": PUBLIC}) for k, g in state.goals.items()}
            return state.model_copy(update={"phase": Phase.SEASON_OVER, "goals": public})
        case ev.GoalSet():
            g = p.goal
            _fresh(state.goals, g.goal_id, "goal")
            seat = _need(state.seats, g.seat_id, "seat")
            clock = _need(state.clocks, g.clock_id, "clock")
            if clock.kind is not ClockKind.OBJECTIVE or clock.owner != seat.seat_id:
                raise FoldError(f"goal {g.goal_id}: clock {g.clock_id} is not its objective")
            if any(o.seat_id == g.seat_id for o in state.goals.values()):
                raise FoldError(f"seat {g.seat_id} already has a goal")
            return state.model_copy(update={"goals": _put(state.goals, g.goal_id, g)})
        case ev.GoalProgressed():
            g = _need(state.goals, p.goal_id, "goal")
            objective = _need(state.clocks, g.clock_id, "clock")
            if p.parts_met > objective.segments_total:
                raise FoldError(f"goal {g.goal_id}: {p.parts_met} parts on a clock that has fewer")
            objective = objective.model_copy(update={"segments_filled": p.parts_met})
            return state.model_copy(
                update={"clocks": _put(state.clocks, objective.clock_id, objective)}
            )
        case ev.GoalMet():
            g = _need(state.goals, p.goal_id, "goal")
            first = g.met_tick if g.met_tick is not None else event.tick
            g = g.model_copy(update={"holding": True, "met_tick": first})
            return state.model_copy(update={"goals": _put(state.goals, g.goal_id, g)})
        case ev.GoalUndone():
            g = _need(state.goals, p.goal_id, "goal")
            if g.kind is GoalKind.LASTING:
                raise FoldError(f"goal {g.goal_id} is lasting and cannot be undone")
            g = g.model_copy(update={"holding": False})
            return state.model_copy(update={"goals": _put(state.goals, g.goal_id, g)})
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
                # An intent with an operation still running stays pending, so the
                # next tick can re-rate and re-roll it (design: the world keeps moving).
                running = {
                    c.intent_id
                    for c in state.clocks.values()
                    if (
                        c.kind is ClockKind.OPERATION
                        and c.status is ClockStatus.OPEN
                        and c.intent_id
                    )
                }
                pending = {k: v for k, v in state.pending.items() if k not in done or k in running}
            return state.model_copy(
                update={"pending": pending, "frozen": (), "tick": event.tick + 1}
            )
        case (
            ev.CardsDeclared()
            | ev.DocumentIssued()
            | ev.DocumentWritten()
            | ev.OrderDrafted()
            | ev.OrderStepTaken()
            | ev.OrderSettled()
            | ev.QuantitySet()
            | ev.FeatureSet()
        ):
            if state.ruleset != "realism":
                raise FoldError(f"{event.kind} belongs to a realism campaign")
            return _realism(state, p)
        case _:
            raise FoldError(f"mutating kind {event.kind!r} has no fold case")


# --- realism --------------------------------------------------------------------


def _realism(state: State, p: ev.Payload) -> State:
    """Store and update realism records. Deciding what they say is the engine's job."""
    match p:
        case ev.CardsDeclared():
            pipelines = dict(state.pipelines)
            for pl in p.pipelines:
                _fresh(pipelines, pl.pipeline_id, "pipeline")
                for step in pl.steps:
                    _need(state.entities, step.person, "entity")
                pipelines[pl.pipeline_id] = pl
            feeds = dict(state.feeds)
            for f in p.feeds:
                _fresh(feeds, f.feed_id, "feed")
                _need(state.entities, f.author, "entity")
                for qid in f.reports:
                    _need(state.quantities, qid, "quantity")
                for sighted in f.sights:
                    _need(state.features, sighted, "feature")
                feeds[f.feed_id] = f
            cards = dict(state.cards)
            for c in p.cards:
                _fresh(cards, c.person, "card")
                person = _need(state.entities, c.person, "entity")
                if person.kind is not EntityKind.PERSON:
                    raise FoldError(f"card {c.person} is not a person")
                named = [*c.principals, *(k.person for k in c.contacts)]
                for entity in [*named, *([c.successor] if c.successor else [])]:
                    _need(state.entities, entity, "entity")
                for fid in c.feeds:
                    _need(feeds, fid, "feed")
                for pid in c.pipelines:
                    _need(pipelines, pid, "pipeline")
                for lever in c.levers:
                    if lever.pipeline not in c.pipelines:
                        raise FoldError(
                            f"card {c.person}: lever {lever.name} names another pipeline"
                        )
                cards[c.person] = c
            return state.model_copy(update={"cards": cards, "feeds": feeds, "pipelines": pipelines})
        case ev.DocumentIssued():
            doc = Document(view=p.document, truth=p.truth)
            _fresh(state.documents, doc.view.doc_id, "document")
            _need(state.entities, doc.view.author, "entity")
            if doc.truth.feed is not None:
                _need(state.feeds, doc.truth.feed, "feed")
            for ref in doc.truth.truth_refs:
                _need(state.quantities, ref.quantity_id, "quantity")
            for sighting in doc.truth.sightings:
                _need(state.features, sighting.feature_id, "feature")
            return state.model_copy(
                update={"documents": _put(state.documents, doc.view.doc_id, doc)}
            )
        case ev.DocumentWritten():
            doc = _need(state.documents, p.doc_id, "document")
            if doc.view.prose is not None:
                raise FoldError(f"document {p.doc_id} is already written")
            view = doc.view.model_copy(update={"prose": p.prose})
            return state.model_copy(
                update={
                    "documents": _put(
                        state.documents, p.doc_id, doc.model_copy(update={"view": view})
                    )
                }
            )
        case ev.OrderDrafted():
            o = p.order
            _fresh(state.orders, o.order_id, "order")
            _need(state.seats, o.issuer, "seat")
            _need(state.pipelines, o.pipeline, "pipeline")
            if o.status is not OrderStatus.MOVING or o.step or o.outcomes:
                raise FoldError(f"order {o.order_id} must start at its first step")
            return state.model_copy(update={"orders": _put(state.orders, o.order_id, o)})
        case ev.OrderStepTaken():
            o = _need(state.orders, p.order_id, "order")
            steps = state.pipelines[o.pipeline].steps
            out = p.outcome
            if o.status in SETTLED:
                raise FoldError(f"order {o.order_id} is settled")
            if out.step != o.step or o.step >= len(steps):
                raise FoldError(f"order {o.order_id} is not at step {out.step}")
            if out.person != steps[o.step].person or out.action not in steps[o.step].allowed:
                raise FoldError(f"order {o.order_id}: step {o.step} does not allow that")
            params = dict(o.params)
            if out.action is StepAction.AMEND:
                if not set(out.amended) <= set(params):
                    raise FoldError(f"order {o.order_id}: an amendment names a new parameter")
                params.update(out.amended)
            o = o.model_copy(
                update={
                    "params": params,
                    "step": o.step + 1 if out.action in ADVANCES else o.step,
                    "outcomes": (*o.outcomes, out),
                }
            )
            return state.model_copy(update={"orders": _put(state.orders, o.order_id, o)})
        case ev.OrderSettled():
            o = _need(state.orders, p.order_id, "order")
            if o.status in SETTLED or p.status not in SETTLED:
                raise FoldError(f"order {o.order_id} cannot settle as {p.status.value}")
            o = o.model_copy(update={"status": p.status})
            return state.model_copy(update={"orders": _put(state.orders, o.order_id, o)})
        case ev.QuantitySet():
            q = p.quantity
            if q.owner is not None:
                _need(state.entities, q.owner, "entity")
            old = state.quantities.get(q.quantity_id)
            if old is not None and q.as_of < old.as_of:
                raise FoldError(f"quantity {q.quantity_id} is already set as of {old.as_of}")
            return state.model_copy(update={"quantities": _put(state.quantities, q.quantity_id, q)})
        case ev.FeatureSet():
            ft = p.feature
            if ft.side is not None:
                _need(state.entities, ft.side, "entity")
            for place in (*ft.places, *([ft.bound_for] if ft.bound_for else [])):
                if _need(state.entities, place, "entity").kind is not EntityKind.LOCATION:
                    raise FoldError(f"feature {ft.feature_id}: {place} is not a place")
            was = state.features.get(ft.feature_id)
            if was is not None and (ft.as_of < was.as_of or ft.kind is not was.kind):
                raise FoldError(f"feature {ft.feature_id} cannot go back or change kind")
            return state.model_copy(update={"features": _put(state.features, ft.feature_id, ft)})
        case _:
            raise FoldError(f"kind {p.kind!r} is not a realism record")


# --- deltas -------------------------------------------------------------------


def apply_delta(state: State, delta: d.Delta, tick: int) -> State:
    """Fold one delta as ``delta_applied`` at ``tick`` would. Raises ``FoldError`` if it cannot."""
    return _apply_delta(state, delta, tick)


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
            # Resolves on the tick the segment fills it, not the tick after.
            fills = c.segments_filled + delta.segments >= c.segments_total
            if status is ClockStatus.OPEN and fills:
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
