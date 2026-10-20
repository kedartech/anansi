"""The event log (README §12, decision #19).

The log is canon. State is a projection of it. Every payload below is a kind
of event the log can hold; ``Event`` is the envelope.

State-mutating kinds (the fold in Layer 2 handles exactly these):
``CampaignCreated, SettingsSet, EntityCreated, AssetCreated, RelationshipSet,
MeterCreated, ClockCreated, FactAsserted, CommitmentCreated, SeatCreated,
AgentAssigned, SeatStatusSet, IntentFiled, IntentRejected, DeltaApplied,
ScheduledEventFired, TickOpened, TickClosed``.

Every other kind is a record: it is replayable, searchable and shown by
``/why``, but changes no state.
"""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from revisionist.core._base import Model
from revisionist.core.delta import Delta
from revisionist.core.ids import (
    CampaignId,
    ClockId,
    DeltaId,
    EntityId,
    EventId,
    IntentId,
    ScheduledEventId,
    SeatId,
)
from revisionist.core.intent import Intent
from revisionist.core.ontology import (
    Asset,
    Clock,
    Commitment,
    Entity,
    Fact,
    Meter,
    Persona,
    Relationship,
    Seat,
    SeatStatus,
)
from revisionist.core.resolution import FeasibilityBand, OutcomeTier, RejectionReason, TickStage
from revisionist.core.settings import Settings
from revisionist.core.visibility import Scope


class Versions(Model):
    engine: str
    schema_version: str
    prompt: str = ""
    model: str = ""


# --- Session Zero and world creation ----------------------------------------


class CampaignCreated(Model):
    kind: Literal["campaign_created"] = "campaign_created"
    name: str
    world: str
    """Name of the world bible this campaign was compiled from."""


class SettingsSet(Model):
    kind: Literal["settings_set"] = "settings_set"
    settings: Settings


class EntityCreated(Model):
    kind: Literal["entity_created"] = "entity_created"
    entity: Entity


class AssetCreated(Model):
    kind: Literal["asset_created"] = "asset_created"
    asset: Asset


class RelationshipSet(Model):
    kind: Literal["relationship_set"] = "relationship_set"
    relationship: Relationship


class MeterCreated(Model):
    kind: Literal["meter_created"] = "meter_created"
    meter: Meter


class ClockCreated(Model):
    kind: Literal["clock_created"] = "clock_created"
    clock: Clock


class FactAsserted(Model):
    kind: Literal["fact_asserted"] = "fact_asserted"
    fact: Fact


class CommitmentCreated(Model):
    kind: Literal["commitment_created"] = "commitment_created"
    commitment: Commitment


class SeatCreated(Model):
    kind: Literal["seat_created"] = "seat_created"
    seat: Seat


class AgentAssigned(Model):
    kind: Literal["agent_assigned"] = "agent_assigned"
    seat_id: SeatId
    agent: EntityId
    persona: Persona


class SeatStatusSet(Model):
    kind: Literal["seat_status_set"] = "seat_status_set"
    seat_id: SeatId
    status: SeatStatus
    cause: str = Field(max_length=200)


# --- Orders and intents -------------------------------------------------------


class OrderReceived(Model):
    """Raw player text. Always Private to the seat. Encrypted at rest from Layer 9."""

    kind: Literal["order_received"] = "order_received"
    seat_id: SeatId
    text: str = Field(max_length=4000)


class IntentFiled(Model):
    kind: Literal["intent_filed"] = "intent_filed"
    intent: Intent
    from_order: EventId | None = None
    """``None`` when a deputy or faction filed it."""


class IntentRejected(Model):
    kind: Literal["intent_rejected"] = "intent_rejected"
    intent_id: IntentId
    reason: RejectionReason
    detail: str = Field(default="", max_length=200)


class ClarificationAsked(Model):
    kind: Literal["clarification_asked"] = "clarification_asked"
    seat_id: SeatId
    from_order: EventId
    question: str = Field(max_length=500)


# --- Resolution ---------------------------------------------------------------


class FeasibilityRated(Model):
    kind: Literal["feasibility_rated"] = "feasibility_rated"
    intent_id: IntentId
    band: FeasibilityBand
    perceived: FeasibilityBand
    """What the actor's own view would compute. Equal to ``band`` with full knowledge."""


class Rolled(Model):
    kind: Literal["rolled"] = "rolled"
    intent_id: IntentId
    seed: str
    """Hex of blake2b(campaign_id, tick, intent_id). Reproducible."""
    outcome: OutcomeTier
    exposure_roll: int = Field(ge=0, le=99)
    exposed: bool


class DeltaProposed(Model):
    kind: Literal["delta_proposed"] = "delta_proposed"
    intent_id: IntentId | None
    """``None`` for director and scheduled-event proposals."""
    deltas: tuple[Delta, ...]


class DeltaRejected(Model):
    kind: Literal["delta_rejected"] = "delta_rejected"
    delta_id: DeltaId
    reason: RejectionReason
    detail: str = Field(default="", max_length=200)


class DeltaApplied(Model):
    kind: Literal["delta_applied"] = "delta_applied"
    intent_id: IntentId | None
    delta: Delta


class OperationOpened(Model):
    kind: Literal["operation_opened"] = "operation_opened"
    intent_id: IntentId
    clock_id: ClockId
    seat_id: SeatId


class OperationResolved(Model):
    kind: Literal["operation_resolved"] = "operation_resolved"
    intent_id: IntentId
    clock_id: ClockId
    seat_id: SeatId
    outcome: OutcomeTier


class ScheduledEventFired(Model):
    kind: Literal["scheduled_event_fired"] = "scheduled_event_fired"
    scheduled_id: ScheduledEventId


# --- Narration and delivery ---------------------------------------------------


class NarrationWritten(Model):
    """Prose for exactly one seat, written from that seat's view."""

    kind: Literal["narration_written"] = "narration_written"
    seat_id: SeatId
    text: str
    from_deltas: tuple[DeltaId, ...] = ()


class StatementIssued(Model):
    """A player's spin, made public as the tick freezes (Layer 9b).

    Spin is the one free text a player addresses to the world (design §5), so
    it is logged Public whatever the order's own scope: a covert operation can
    come with a public denial. A record: it changes no state.
    """

    kind: Literal["statement_issued"] = "statement_issued"
    seat_id: SeatId
    intent_id: IntentId
    text: str = Field(max_length=280)


class BriefingDelivered(Model):
    kind: Literal["briefing_delivered"] = "briefing_delivered"
    seat_id: SeatId
    narration: EventId
    channel: str
    idempotency_key: str


# --- Tick lifecycle -----------------------------------------------------------


class TickOpened(Model):
    kind: Literal["tick_opened"] = "tick_opened"
    intents_frozen: tuple[IntentId, ...]


class CallCost(Model):
    call_class: str
    """resolver | narrator | structurer | persona | press | director."""
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    usd: float = Field(ge=0)


class TickStageCompleted(Model):
    kind: Literal["tick_stage_completed"] = "tick_stage_completed"
    stage: TickStage
    costs: tuple[CallCost, ...] = ()
    """Model calls made during the stage. ``tick_closed`` sums them."""


class TickClosed(Model):
    kind: Literal["tick_closed"] = "tick_closed"
    quiet: bool = False
    """True when the resolver failed and intents carried forward."""
    quiet_reason: str = Field(default="", max_length=200)
    costs: tuple[CallCost, ...] = ()
    state_hash: str
    """blake2b of the canonical state JSON after this tick. Replay must match."""


class CostLogged(Model):
    """Model calls made outside a tick (structuring an order, a briefing, a deputy turn).

    Tick costs live on ``tick_closed``; everything else lands here so ``revisionist cost``
    can account for every call. A record kind: it changes no state.
    """

    kind: Literal["cost_logged"] = "cost_logged"
    seat_id: SeatId | None = None
    purpose: str = Field(max_length=60)
    costs: tuple[CallCost, ...]


# --- Admin --------------------------------------------------------------------


class AdminRead(Model):
    kind: Literal["admin_read"] = "admin_read"
    actor: str
    """Admin identity, not a seat."""
    events: tuple[EventId, ...]
    purpose: str = Field(max_length=200)


# --- At rest -------------------------------------------------------------------


class Sealed(Model):
    """What a sealed event looks like at rest (Layer 9b): the real event, whole,
    is encrypted into the envelope's ``payload_enc``.

    The store unseals on read, so the tick, the fold and every reader see the
    plaintext event. A ``seat``-held event whose key was destroyed (erasure)
    stays sealed: it is prose the fold never needed. A ``campaign``-held one
    that reaches the fold is an error: its key is missing.
    """

    kind: Literal["sealed"] = "sealed"
    key_id: str = Field(max_length=64)
    holder: Literal["campaign", "seat"]


Payload = Annotated[
    CampaignCreated
    | SettingsSet
    | EntityCreated
    | AssetCreated
    | RelationshipSet
    | MeterCreated
    | ClockCreated
    | FactAsserted
    | CommitmentCreated
    | SeatCreated
    | AgentAssigned
    | SeatStatusSet
    | OrderReceived
    | IntentFiled
    | IntentRejected
    | ClarificationAsked
    | FeasibilityRated
    | Rolled
    | DeltaProposed
    | DeltaRejected
    | DeltaApplied
    | OperationOpened
    | OperationResolved
    | ScheduledEventFired
    | NarrationWritten
    | StatementIssued
    | BriefingDelivered
    | TickOpened
    | TickStageCompleted
    | TickClosed
    | CostLogged
    | AdminRead
    | Sealed,
    Field(discriminator="kind"),
]

MUTATING_KINDS: frozenset[str] = frozenset(
    {
        "campaign_created",
        "settings_set",
        "entity_created",
        "asset_created",
        "relationship_set",
        "meter_created",
        "clock_created",
        "fact_asserted",
        "commitment_created",
        "seat_created",
        "agent_assigned",
        "seat_status_set",
        "intent_filed",
        "intent_rejected",
        "delta_applied",
        "scheduled_event_fired",
        "tick_opened",
        "tick_closed",
    }
)


class Event(Model):
    model_config = ConfigDict(
        frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64"
    )

    event_id: EventId
    campaign_id: CampaignId
    seq: int = Field(ge=0)
    """Dense, per campaign. The store refuses gaps and repeats."""
    tick: int = Field(ge=0)
    actor: SeatId | None = None
    scope: Scope
    payload: Payload
    versions: Versions
    payload_enc: bytes | None = None
    """At rest only: nonce and AES-GCM ciphertext of the whole event when ``payload``
    is ``Sealed`` (Layer 9b). Empty on every event a reader sees."""

    @property
    def kind(self) -> str:
        return self.payload.kind

    @property
    def mutates(self) -> bool:
        return self.kind in MUTATING_KINDS
