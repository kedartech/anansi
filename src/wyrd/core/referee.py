"""The referee (README §11, §13; build plan Layer 3). Pure. No LLM anywhere.

Five functions, each a total function of its inputs:

- ``validate(state, intent)``: may this intent be filed at all?
- ``rate(world, intent)``: how likely is it? Takes a ``State`` *or* a
  ``View``. ``rate(state, i)`` is the truth; ``rate(project(state, seat), i)``
  is what the seat believes. One function, two inputs, no second code path.
- ``roll(seed, band, exposure)``: the dice. Reproducible from the seed.
- ``clamp(state, intent, deltas, outcome)``: cut a resolver proposal down to
  the table. Idempotent. Nothing a model proposes reaches the fold unclamped.
- ``reveal(state, policy, tick)``: chapter-break knowledge grants (decision 4).

Numbers marked [proposed] are starting values, retuned after the solo slice.
"""

import hashlib
import math
from collections.abc import Iterable, Sequence
from typing import Literal, Protocol

from pydantic import Field

from wyrd.core import delta as d
from wyrd.core._base import Model
from wyrd.core.fold import FoldError, apply_delta
from wyrd.core.ids import (
    AssetId,
    CampaignId,
    ClockId,
    CommitmentId,
    DeltaId,
    EntityId,
    FactId,
    IntentId,
    MeterId,
    SeatId,
    derived_id,
)
from wyrd.core.intent import FREE_VERBS, IRREVERSIBLE_VERBS, Intent, Verb
from wyrd.core.ontology import (
    AssetKind,
    AssetStatus,
    ClockKind,
    ClockStatus,
    CommitmentStatus,
    EntityStatus,
    Seat,
    SeatKind,
    SeatStatus,
)
from wyrd.core.resolution import (
    EXPOSURE_CHANCE,
    SUCCESS_CHANCE,
    SUCCESS_SPLIT,
    FeasibilityBand,
    OutcomeTier,
    RejectionReason,
)
from wyrd.core.settings import Lethality, Plausibility
from wyrd.core.state import State, View
from wyrd.core.visibility import Visibility

World = State | View

MEANS_VERBS: frozenset[Verb] = frozenset(Verb) - FREE_VERBS - {Verb.TRAVEL}
"""Verbs that need committed assets to have any real chance."""

FORCE_VERBS: frozenset[Verb] = frozenset({Verb.ATTACK, Verb.DEFEND, Verb.SABOTAGE})
INFO_VERBS: frozenset[Verb] = frozenset({Verb.INVESTIGATE, Verb.LEAK, Verb.FORGE, Verb.CONCEAL})
DEAL_VERBS: frozenset[Verb] = frozenset(
    {Verb.OFFER, Verb.ACCEPT, Verb.NEGOTIATE, Verb.LOBBY, Verb.FUND, Verb.RECRUIT}
)

OUTLET_VERBS: frozenset[Verb] = frozenset(
    {Verb.INVESTIGATE, Verb.LEAK, Verb.DECLARE, Verb.NEGOTIATE, Verb.WAIT}
)
"""A press outlet reports; it does not raise armies (README §6)."""

PUBLIC_GRANT_VERBS: frozenset[Verb] = frozenset({Verb.LEAK, Verb.DECLARE})
"""Only these may make a secret public in one step (build plan L3: exposure escalates
to Public only through further Investigate or Leak intents)."""

MAX_OBJECTS_PER_INTENT = 6
MAX_TIER_DELTAS_PER_INTENT = 2
BLINDED_SLOTS = 1

CLOCK_SEGMENTS_BY_OUTCOME: dict[OutcomeTier, int] = {
    OutcomeTier.FAIL_COMPLICATION: 0,
    OutcomeTier.PARTIAL: 1,
    OutcomeTier.SUCCESS: 2,
    OutcomeTier.SUCCESS_OPPORTUNITY: 2,
}
NEW_CLOCKS_BY_OUTCOME: dict[OutcomeTier, int] = {
    OutcomeTier.FAIL_COMPLICATION: 1,
    OutcomeTier.PARTIAL: 0,
    OutcomeTier.SUCCESS: 0,
    OutcomeTier.SUCCESS_OPPORTUNITY: 1,
}


# --- helpers -----------------------------------------------------------------


def _controls(world: World, entity: EntityId, asset_id: AssetId) -> bool:
    """The entity, or one of its direct children, holds the asset and it is not lost."""
    asset = world.assets.get(asset_id)
    if asset is None or asset.status is AssetStatus.LOST:
        return False
    if asset.controller == entity:
        return True
    holder = world.entities.get(asset.controller)
    return holder is not None and holder.parent == entity


def _held_by(world: World, entity: EntityId) -> Iterable[AssetId]:
    for asset_id in world.assets:
        if _controls(world, entity, asset_id):
            yield asset_id


def _sum_tier(world: World, asset_ids: Iterable[AssetId], kinds: frozenset[AssetKind]) -> int:
    total = 0
    for asset_id in asset_ids:
        asset = world.assets.get(asset_id)
        if asset is not None and asset.kind in kinds and asset.status is not AssetStatus.LOST:
            total += asset.tier if asset.status is AssetStatus.ACTIVE else asset.tier // 2
    return min(5, total)


def _disposition(world: World, a: EntityId, b: EntityId) -> int | None:
    for rel in world.relationships.values():
        if {rel.a, rel.b} == {a, b}:
            return rel.disposition
    return None


def _object_exists(world: World, target: str) -> bool:
    return (
        target in world.entities
        or target in world.assets
        or target in world.relationships
        or target in world.meters
        or target in world.clocks
        or target in world.facts
        or target in world.commitments
    )


def _object_gone(world: World, target: str) -> bool:
    """The target exists but nothing can be done with it any more."""
    entity = world.entities.get(EntityId(target))
    if entity is not None:
        return entity.status in (EntityStatus.DEAD, EntityStatus.DISSOLVED)
    asset = world.assets.get(AssetId(target))
    if asset is not None:
        return asset.status is AssetStatus.LOST
    clock = world.clocks.get(ClockId(target))
    if clock is not None:
        return clock.status is not ClockStatus.OPEN
    return False


# --- slots -------------------------------------------------------------------


class Slots(Model):
    used: int
    """Open operation clocks held by the seat."""
    queued: int
    """Pending slot-holding intents not yet frozen."""
    cap: int

    @property
    def free(self) -> int:
        return max(0, self.cap - self.used - self.queued)


def slots(world: World, seat_id: SeatId, pending: Iterable[Intent] = ()) -> Slots:
    seat = world.seats[seat_id]
    cap = BLINDED_SLOTS if seat.status is SeatStatus.BLINDED else seat.slots
    if seat.kind is not SeatKind.PLAYER and seat.kind is not SeatKind.FACTION:
        cap = 0
    used = sum(
        1
        for c in world.clocks.values()
        if c.owner == seat_id and c.holds_slot and c.status is ClockStatus.OPEN
    )
    queued = sum(1 for i in pending if i.actor == seat_id and i.holds_slot)
    return Slots(used=used, queued=queued, cap=cap)


# --- validate ----------------------------------------------------------------


class SafetyCheck(Protocol):
    """Layer 10 plugs a classifier in here. Returns a detail string to refuse, None to pass."""

    def __call__(self, state: State, intent: Intent, /) -> str | None: ...


class Rejection(Model):
    reason: RejectionReason
    detail: str = Field(default="", max_length=200)


def validate(
    state: State, intent: Intent, safety: SafetyCheck | None = None, *, filed: bool = False
) -> Rejection | None:
    """None when the intent may proceed. Checks run in the order a player would expect.

    ``filed=True`` re-checks an intent already in ``pending`` (at FREEZE), so the
    duplicate test is skipped and the intent does not count against its own slot.
    """
    seat = state.seats.get(intent.actor)
    if seat is None:
        return Rejection(reason=RejectionReason.MALFORMED, detail="actor is not a seat")
    if seat.status is SeatStatus.FALLEN:
        return Rejection(reason=RejectionReason.SEAT_INACTIVE, detail="seat has fallen")
    if not filed and intent.intent_id in state.pending:
        return Rejection(reason=RejectionReason.DUPLICATE)

    if seat.kind is SeatKind.OUTLET and intent.verb not in OUTLET_VERBS:
        return Rejection(reason=RejectionReason.OUT_OF_REACH, detail="outlets report, not act")
    if seat.status is SeatStatus.BLINDED and intent.verb in IRREVERSIBLE_VERBS:
        return Rejection(
            reason=RejectionReason.OUT_OF_REACH,
            detail="blinded seats take no irreversible steps",
        )

    for target in intent.targets:
        if not _object_exists(state, target):
            return Rejection(reason=RejectionReason.TARGET_UNKNOWN, detail=str(target))
        if _object_gone(state, target):
            return Rejection(reason=RejectionReason.TARGET_UNKNOWN, detail=f"{target} is gone")
    if intent.counterparty is not None and intent.counterparty not in state.entities:
        return Rejection(reason=RejectionReason.TARGET_UNKNOWN, detail="counterparty")

    for asset_id in intent.means:
        if not _controls(state, seat.embodies, asset_id):
            return Rejection(reason=RejectionReason.MEANS_NOT_HELD, detail=str(asset_id))

    if intent.verb is Verb.TRANSFER and not intent.means:
        return Rejection(reason=RejectionReason.MALFORMED, detail="transfer names no asset")
    if intent.verb is Verb.ACCEPT:
        offered = [
            t
            for t in intent.targets
            if t in state.commitments
            and state.commitments[CommitmentId(t)].status is CommitmentStatus.OFFERED
        ]
        if not offered:
            return Rejection(reason=RejectionReason.MALFORMED, detail="nothing offered to accept")

    slotted = seat.kind in (SeatKind.PLAYER, SeatKind.FACTION)
    # An intent whose operation clock is already open holds its slot through the
    # clock, so counting the intent as well would charge the seat twice.
    running = {c.intent_id for c in state.clocks.values() if c.holds_slot and c.intent_id}
    queued = (
        i
        for i in state.pending.values()
        if i.intent_id not in state.frozen
        and i.intent_id != intent.intent_id
        and i.intent_id not in running
    )
    if slotted and intent.holds_slot and slots(state, intent.actor, queued).free <= 0:
        return Rejection(reason=RejectionReason.NO_SLOT)

    if safety is not None:
        detail = safety(state, intent)
        if detail is not None:
            return Rejection(reason=RejectionReason.SAFETY_LINE, detail=detail)
    return None


# --- rate --------------------------------------------------------------------


def _band(score: int) -> FeasibilityBand:
    if score <= -2:
        return FeasibilityBand.LONG_SHOT
    if score <= 0:
        return FeasibilityBand.CONTESTED
    if score == 1:
        return FeasibilityBand.LIKELY
    return FeasibilityBand.ASSURED


def rate(world: World, intent: Intent) -> FeasibilityBand:
    """Feasibility from what ``world`` shows. Impossible only for structural reasons.

    Score starts at 0 (Contested) and moves by: means minus opposition
    (clamped to ±2), counterparty disposition (±1), and the plausibility
    setting (±1 on force and information verbs). Free verbs are Assured:
    talking always happens, whatever comes of it.
    """
    seat = world.seats.get(intent.actor)
    if seat is None:
        return FeasibilityBand.IMPOSSIBLE
    for target in intent.targets:
        if not _object_exists(world, target) or _object_gone(world, target):
            return FeasibilityBand.IMPOSSIBLE
    for asset_id in intent.means:
        if not _controls(world, seat.embodies, asset_id):
            return FeasibilityBand.IMPOSSIBLE
    if intent.verb in FREE_VERBS:
        return FeasibilityBand.ASSURED

    score = 0
    relevant = FORCE_KINDS if intent.verb in FORCE_VERBS else ALL_KINDS
    means_tier = _sum_tier(world, intent.means, relevant)
    opposition = _opposition(world, intent)
    score += max(-2, min(2, means_tier - opposition))

    if intent.counterparty is not None:
        disp = _disposition(world, seat.embodies, intent.counterparty)
        if disp is not None:
            if disp >= 2:
                score += 1
            elif disp <= -2:
                score -= 1

    plaus = world.settings.plausibility
    if intent.verb in FORCE_VERBS or intent.verb in INFO_VERBS:
        if plaus is Plausibility.STRICT:
            score -= 1
        elif plaus is Plausibility.PULP:
            score += 1

    band = _band(score)
    if intent.verb in MEANS_VERBS and not intent.means:
        band = min(band, FeasibilityBand.LONG_SHOT, key=_BAND_ORDER.index)
    return band


_BAND_ORDER = list(FeasibilityBand)
FORCE_KINDS = frozenset({AssetKind.FORCE})
ALL_KINDS = frozenset(AssetKind)


def _opposition(world: World, intent: Intent) -> int:
    """Tier the intent must overcome: a targeted asset's tier, or the target entity's forces."""
    best = 0
    for target in intent.targets:
        asset = world.assets.get(AssetId(target))
        if asset is not None:
            best = max(best, asset.tier)
            continue
        if target in world.entities:
            kinds = FORCE_KINDS if intent.verb in FORCE_VERBS else ALL_KINDS
            best = max(best, _sum_tier(world, _held_by(world, EntityId(target)), kinds))
    if intent.verb in DEAL_VERBS and intent.counterparty is not None:
        # A deal costs more the richer the other side already is.
        best = max(best, _sum_tier(world, _held_by(world, intent.counterparty), ALL_KINDS) // 2)
    return best


# --- roll --------------------------------------------------------------------


class Roll(Model):
    seed: str
    outcome: OutcomeTier
    exposure_roll: int = Field(ge=0, le=99)
    exposed: bool


def seed_for(campaign_id: CampaignId, tick: int, intent_id: IntentId) -> str:
    return hashlib.blake2b(
        f"{campaign_id}\x1f{tick}\x1f{intent_id}".encode(), digest_size=16
    ).hexdigest()


def roll(seed: str, band: FeasibilityBand, exposure: int = 0) -> Roll:
    """Deterministic dice. Uniform draws come from blake2b over the seed, not ``random``."""
    digest = hashlib.blake2b(seed.encode(), digest_size=12).digest()
    u_outcome = int.from_bytes(digest[0:4], "big") % 10_000 / 10_000
    u_split = int.from_bytes(digest[4:8], "big") % 10_000 / 10_000
    exposure_roll = int.from_bytes(digest[8:12], "big") % 100

    if u_outcome < SUCCESS_CHANCE[band]:
        outcome = OutcomeTier.SUCCESS_OPPORTUNITY
        acc = 0.0
        for tier, share in SUCCESS_SPLIT:
            acc += share
            if u_split < acc:
                outcome = tier
                break
    else:
        outcome = OutcomeTier.FAIL_COMPLICATION

    exposed = exposure_roll < EXPOSURE_CHANCE[max(0, min(5, exposure))]
    return Roll(seed=seed, outcome=outcome, exposure_roll=exposure_roll, exposed=exposed)


# --- clamp -------------------------------------------------------------------


class Cut(Model):
    delta_id: DeltaId
    reason: RejectionReason
    detail: str = Field(default="", max_length=200)


class Clamped(Model):
    kept: tuple[d.Delta, ...]
    cut: tuple[Cut, ...]
    """Everything dropped or trimmed, for ``delta_rejected`` events."""


def touched(delta: d.Delta) -> str:
    """The ID of the one object a delta changes or creates."""
    match delta:
        case d.AssetTierDelta() | d.AssetStatusSet() | d.AssetControllerSet() | d.AssetCreate():
            return delta.asset_id
        case d.MeterDelta():
            return delta.meter_id
        case d.DispositionDelta() | d.RelationshipTagSet():
            return delta.relationship_id
        case d.ClockAdvance() | d.ClockCreate() | d.ClockStatusSet() | d.ClockExposureDelta():
            return delta.clock_id
        case d.FactAssert():
            return delta.fact_id
        case d.KnowledgeGrant():
            return delta.target
        case d.EntityStatusSet():
            return delta.entity_id
        case d.CommitmentCreate() | d.CommitmentStatusSet():
            return delta.commitment_id
        case d.ScheduledEventQueue():
            return delta.scheduled_id
        case d.SeatSlotsDelta():
            return delta.seat_id


def _budget(totals: dict[MeterId, int], meter_id: MeterId, cap: int, change: int) -> int:
    """Net change allowed for this meter given what the tick already spent."""
    so_far = totals.get(meter_id, 0)
    net = max(-cap, min(cap, so_far + change))
    totals[meter_id] = net
    return net - so_far


def _sign(n: int) -> int:
    return 1 if n > 0 else -1


def _at_risk(state: State, entity_id: EntityId) -> bool:
    return any(
        c.kind is ClockKind.THREAT and c.target == entity_id and c.status is ClockStatus.OPEN
        for c in state.clocks.values()
    )


def _is_agent(state: State, entity_id: EntityId) -> bool:
    return any(s.agent == entity_id for s in state.seats.values())


def _actor_entity(state: State, intent: Intent | None) -> EntityId | None:
    if intent is None:
        return None
    seat: Seat | None = state.seats.get(intent.actor)
    return None if seat is None else seat.embodies


def clamp(
    state: State, intent: Intent | None, proposal: Sequence[d.Delta], outcome: OutcomeTier
) -> Clamped:
    """Cut a proposal to the Layer 3 table. ``intent`` is None for director and scheduled deltas.

    Trimmed deltas are kept at the limit and also reported in ``cut`` so the log
    shows what the resolver asked for. Dropped deltas appear only in ``cut``.
    """
    kept: list[d.Delta] = []
    cut: list[Cut] = []
    objects: set[str] = set()
    tier_deltas = 0
    new_clocks = 0
    meter_total: dict[MeterId, int] = {}
    actor = _actor_entity(state, intent)
    fail = outcome is OutcomeTier.FAIL_COMPLICATION

    def drop(delta: d.Delta, reason: RejectionReason, detail: str) -> None:
        cut.append(Cut(delta_id=delta.delta_id, reason=reason, detail=detail))

    def trim(delta: d.Delta, replacement: d.Delta, detail: str) -> None:
        kept.append(replacement)
        cut.append(Cut(delta_id=delta.delta_id, reason=RejectionReason.CLAMPED, detail=detail))

    for delta in proposal:
        obj = touched(delta)
        if obj not in objects and len(objects) >= MAX_OBJECTS_PER_INTENT:
            drop(delta, RejectionReason.CLAMPED, f"over {MAX_OBJECTS_PER_INTENT} objects")
            continue

        match delta:
            case d.AssetTierDelta():
                asset = state.assets.get(delta.asset_id)
                if asset is None:
                    drop(delta, RejectionReason.TARGET_UNKNOWN, "asset")
                    continue
                if tier_deltas >= MAX_TIER_DELTAS_PER_INTENT:
                    drop(delta, RejectionReason.CLAMPED, "tier changes on more than 2 assets")
                    continue
                if fail and intent is not None:
                    own = actor is not None and _controls(state, actor, delta.asset_id)
                    if delta.change > 0 or not own:
                        drop(
                            delta,
                            RejectionReason.TIER_DISALLOWS,
                            "failure only lowers own assets",
                        )
                        continue
                tier_deltas += 1
                if abs(delta.change) > 1:
                    trim(
                        delta,
                        delta.model_copy(update={"change": _sign(delta.change)}),
                        "tier ±1",
                    )
                else:
                    kept.append(delta)

            case d.MeterDelta():
                meter = state.meters.get(delta.meter_id)
                if meter is None:
                    drop(delta, RejectionReason.TARGET_UNKNOWN, "meter")
                    continue
                cap = meter.max_delta_per_tick
                change = _budget(meter_total, delta.meter_id, cap, delta.change)
                if change == 0 and delta.change != 0:
                    drop(delta, RejectionReason.CLAMPED, f"meter cap {cap} per tick reached")
                    continue
                if change != delta.change:
                    trim(delta, delta.model_copy(update={"change": change}), f"meter cap {cap}")
                else:
                    kept.append(delta)

            case d.DispositionDelta():
                rel = state.relationships.get(delta.relationship_id)
                if rel is None:
                    drop(delta, RejectionReason.TARGET_UNKNOWN, "relationship")
                    continue
                limit = 1
                if (
                    intent is not None
                    and intent.scope.visibility is Visibility.PUBLIC
                    and intent.counterparty is not None
                    and {rel.a, rel.b} == {actor, intent.counterparty}
                ):
                    limit = 2
                if abs(delta.change) > limit:
                    trim(
                        delta,
                        delta.model_copy(update={"change": _sign(delta.change) * limit}),
                        f"disposition ±{limit}",
                    )
                else:
                    kept.append(delta)

            case d.ClockAdvance():
                if delta.clock_id not in state.clocks:
                    drop(delta, RejectionReason.TARGET_UNKNOWN, "clock")
                    continue
                limit = CLOCK_SEGMENTS_BY_OUTCOME[outcome] if intent is not None else 2
                if delta.segments > limit:
                    if limit == 0:
                        drop(delta, RejectionReason.CLAMPED, "no progress on a failure")
                        continue
                    trim(
                        delta,
                        delta.model_copy(update={"segments": limit}),
                        f"{limit} segments max on {outcome}",
                    )
                else:
                    kept.append(delta)

            case d.ClockCreate():
                if intent is not None and new_clocks >= NEW_CLOCKS_BY_OUTCOME[outcome]:
                    drop(delta, RejectionReason.CLAMPED, f"no new clock on {outcome}")
                    continue
                if delta.clock_kind is ClockKind.OPERATION and intent is not None:
                    drop(
                        delta,
                        RejectionReason.CLAMPED,
                        "operations are opened by the engine",
                    )
                    continue
                new_clocks += 1
                kept.append(delta)

            case d.EntityStatusSet():
                entity = state.entities.get(delta.entity_id)
                if entity is None:
                    drop(delta, RejectionReason.TARGET_UNKNOWN, "entity")
                    continue
                lethal = delta.status in (EntityStatus.DEAD, EntityStatus.DISSOLVED)
                harm = lethal or delta.status in (EntityStatus.CAPTURED, EntityStatus.TURNED)
                if lethal and state.settings.lethality is Lethality.SOFT:
                    drop(delta, RejectionReason.LETHALITY_DISALLOWS, "soft lethality")
                    continue
                if lethal and not _at_risk(state, delta.entity_id):
                    drop(delta, RejectionReason.LETHALITY_DISALLOWS, "no threat clock at risk")
                    continue
                if harm and _is_agent(state, delta.entity_id) and not state.settings.agent_harm:
                    drop(delta, RejectionReason.LETHALITY_DISALLOWS, "agent harm is off")
                    continue
                kept.append(delta)

            case d.KnowledgeGrant():
                if (
                    delta.make_public
                    and intent is not None
                    and intent.verb not in PUBLIC_GRANT_VERBS
                ):
                    drop(
                        delta,
                        RejectionReason.CLAMPED,
                        "only leak or declare goes public",
                    )
                    continue
                kept.append(delta)

            case d.SeatSlotsDelta():
                if delta.seat_id not in state.seats:
                    drop(delta, RejectionReason.TARGET_UNKNOWN, "seat")
                    continue
                if abs(delta.change) > 1:
                    trim(
                        delta,
                        delta.model_copy(update={"change": _sign(delta.change)}),
                        "slots ±1",
                    )
                else:
                    kept.append(delta)

            case d.AssetStatusSet() | d.AssetControllerSet():
                if delta.asset_id not in state.assets:
                    drop(delta, RejectionReason.TARGET_UNKNOWN, "asset")
                    continue
                if (
                    fail
                    and intent is not None
                    and actor is not None
                    and not _controls(state, actor, delta.asset_id)
                ):
                    drop(
                        delta,
                        RejectionReason.TIER_DISALLOWS,
                        "failure only touches own assets",
                    )
                    continue
                kept.append(delta)

            case d.ClockStatusSet() | d.ClockExposureDelta():
                if delta.clock_id not in state.clocks:
                    drop(delta, RejectionReason.TARGET_UNKNOWN, "clock")
                    continue
                kept.append(delta)

            case d.RelationshipTagSet():
                if delta.relationship_id not in state.relationships:
                    drop(delta, RejectionReason.TARGET_UNKNOWN, "relationship")
                    continue
                kept.append(delta)

            case d.CommitmentStatusSet():
                if delta.commitment_id not in state.commitments:
                    drop(delta, RejectionReason.TARGET_UNKNOWN, "commitment")
                    continue
                kept.append(delta)

            case d.AssetCreate() | d.FactAssert() | d.CommitmentCreate() | d.ScheduledEventQueue():
                kept.append(delta)

        objects.add(obj)

    return Clamped(kept=tuple(kept), cut=tuple(cut))


def admissible(state: State, deltas: Sequence[d.Delta]) -> Clamped:
    """Last pass: drop any delta the fold would refuse, in order, against a scratch state.

    ``clamp`` bounds how much a proposal may change; this bounds what can exist. The
    resolver is a model and can name an asset where the schema wants an entity, reuse a
    fact ID, or create something under an owner that is not there. One bad delta must
    cost that delta, never the tick: a refused fold would fail the tick on every retry.
    """
    kept: list[d.Delta] = []
    cut: list[Cut] = []
    for delta in deltas:
        try:
            state = apply_delta(state, delta, state.tick)
        except FoldError as exc:
            cut.append(
                Cut(
                    delta_id=delta.delta_id,
                    reason=RejectionReason.TARGET_UNKNOWN,
                    detail=str(exc)[:200],
                )
            )
            continue
        kept.append(delta)
    return Clamped(kept=tuple(kept), cut=tuple(cut))


def cap_meters(state: State, deltas: Sequence[d.Delta]) -> Clamped:
    """Second pass across every intent in a tick: net meter change stays within the cap.

    ``clamp`` budgets per intent; the orchestrator calls this over the union so
    three seats cannot each spend the full cap on the same meter.
    """
    kept: list[d.Delta] = []
    cut: list[Cut] = []
    totals: dict[MeterId, int] = {}
    for delta in deltas:
        if not isinstance(delta, d.MeterDelta) or delta.meter_id not in state.meters:
            kept.append(delta)
            continue
        cap = state.meters[delta.meter_id].max_delta_per_tick
        change = _budget(totals, delta.meter_id, cap, delta.change)
        if change == 0 and delta.change != 0:
            cut.append(
                Cut(delta_id=delta.delta_id, reason=RejectionReason.CLAMPED, detail="tick cap")
            )
            continue
        if change != delta.change:
            kept.append(delta.model_copy(update={"change": change}))
            cut.append(
                Cut(delta_id=delta.delta_id, reason=RejectionReason.CLAMPED, detail="tick cap")
            )
        else:
            kept.append(delta)
    return Clamped(kept=tuple(kept), cut=tuple(cut))


# --- reveal ------------------------------------------------------------------


class Partial(Model):
    kind: Literal["partial"] = "partial"
    fraction: float = Field(gt=0, le=1)
    min_age_ticks: int = Field(default=0, ge=0)


class Full(Model):
    kind: Literal["full"] = "full"


RevealPolicy = Partial | Full


def _pick_key(state: State, tick: int, target: str) -> str:
    return derived_id(state.campaign_id, tick, "pick", target)


def reveal(state: State, policy: RevealPolicy, tick: int) -> tuple[d.KnowledgeGrant, ...]:
    """Chapter-break grants (README §15). Covert facts and clocks become public.

    Deterministic: the subset chosen under ``Partial`` depends only on the
    campaign, the tick and the item IDs, so a replay reveals the same things.
    """
    candidates: list[tuple[str, FactId | ClockId]] = []
    for fact in state.facts.values():
        if fact.scope.visibility is Visibility.COVERT:
            age = tick - fact.asserted_tick
            if isinstance(policy, Full) or age >= policy.min_age_ticks:
                candidates.append((_pick_key(state, tick, fact.fact_id), fact.fact_id))
    for clock in state.clocks.values():
        if clock.scope.visibility is Visibility.COVERT:
            age = tick - clock.opened_tick
            if isinstance(policy, Full) or age >= policy.min_age_ticks:
                candidates.append((_pick_key(state, tick, clock.clock_id), clock.clock_id))

    candidates.sort()
    if isinstance(policy, Partial):
        candidates = candidates[: math.ceil(policy.fraction * len(candidates))]

    label = "season reveal" if isinstance(policy, Full) else "chapter reveal"
    return tuple(
        d.KnowledgeGrant(
            delta_id=DeltaId(derived_id(state.campaign_id, tick, "reveal", target)),
            reason=label,
            target=target,
            seats=frozenset(),
            make_public=True,
        )
        for _, target in candidates
    )


__all__ = [
    "Clamped",
    "Cut",
    "Full",
    "Partial",
    "Rejection",
    "RevealPolicy",
    "Roll",
    "SafetyCheck",
    "Slots",
    "World",
    "admissible",
    "cap_meters",
    "clamp",
    "rate",
    "reveal",
    "roll",
    "seed_for",
    "slots",
    "touched",
    "validate",
]
