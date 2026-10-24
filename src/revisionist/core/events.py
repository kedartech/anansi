"""The event log (spec §12, decision #19).

The log is canon. State is a projection of it. Every payload below is a kind
of event the log can hold; ``Event`` is the envelope.

State-mutating kinds (the fold in Layer 2 handles exactly these):
``CampaignCreated, SettingsSet, EntityCreated, AssetCreated, RelationshipSet,
MeterCreated, ClockCreated, FactAsserted, CommitmentCreated, SeatCreated,
AgentAssigned, SeatStatusSet, IntentFiled, IntentRejected, DeltaApplied,
ScheduledEventFired, ContestCreated, ContestMoved, ContestDecided,
ScheduledEventCreated, ScheduledEventSkipped, DivergenceChanged, DateAdvanced,
SessionZeroOpened, CharacterListed, CharacterClaimed,
SessionZeroClosed, SeasonEnded, GoalSet, GoalProgressed, GoalMet, GoalUndone,
TickOpened, TickClosed``, and for realism campaigns ``CardsDeclared,
DocumentIssued, DocumentWritten, OrderDrafted, OrderStepTaken, OrderSettled, QuantitySet``.

Every other kind is a record: it is replayable, searchable and shown by
``/why``, but changes no state.
"""

from datetime import date
from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field

from revisionist.core._base import Label, Model
from revisionist.core.delta import Delta
from revisionist.core.goals import Goal, GoalKind, WinCondition
from revisionist.core.ids import (
    CampaignId,
    ClockId,
    ContestId,
    DeltaId,
    DocumentId,
    EntityId,
    EventId,
    GoalId,
    IntentId,
    OrderId,
    ScheduledEventId,
    SeatId,
)
from revisionist.core.intent import Intent
from revisionist.core.ontology import (
    Asset,
    Character,
    Clock,
    Commitment,
    Contest,
    Entity,
    Fact,
    Meter,
    Persona,
    Relationship,
    ScheduledEvent,
    Seat,
    SeatStatus,
)
from revisionist.core.realism.card import ChannelKind, ParamValue, SeatCard
from revisionist.core.realism.document import DocumentTruth, DocumentView, Feed
from revisionist.core.realism.pipeline import Order, OrderStatus, Pipeline, StepOutcome
from revisionist.core.realism.quantity import Quantity
from revisionist.core.resolution import FeasibilityBand, OutcomeTier, RejectionReason, TickStage
from revisionist.core.settings import Settings
from revisionist.core.state import Ruleset
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
    ruleset: Ruleset = "legacy"
    """Which rules resolve this campaign. Left out of a legacy log, so it hashes as before."""

    late_fields: ClassVar[frozenset[str]] = frozenset({"ruleset"})


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


class SessionZeroOpened(Model):
    """The table locked a point in history with ``/establish`` (spec §5 step 2).

    Ticks wait until Session Zero closes. ``notes`` is where the GM logs each
    reading it chose where historians disagree or the record is silent.
    """

    kind: Literal["session_zero_opened"] = "session_zero_opened"
    start: str = Field(max_length=80)
    notes: tuple[str, ...] = ()


class CharacterListed(Model):
    """One character on the Session Zero roster. Public: every player sees the roster."""

    kind: Literal["character_listed"] = "character_listed"
    character: Character


class CharacterClaimed(Model):
    """A player takes a character from the roster (spec §5 step 3).

    The fold creates the player's seat here, embodying the character. The
    engine gives this event an ID derived from the campaign and the character,
    so a second claim of the same person fails at the store, not just here.
    """

    kind: Literal["character_claimed"] = "character_claimed"
    seat_id: SeatId
    entity_id: EntityId


class SessionZeroClosed(Model):
    """Every player has a character, a goal and an agent: play begins."""

    kind: Literal["session_zero_closed"] = "session_zero_closed"


class SeasonEnded(Model):
    """The season is over and its winners decided (spec §15, decision 36).

    Logged right after the full reveal's grants, ``revealed`` of them, and it
    makes every goal public. ``winners`` is whatever ``season_winners`` says;
    the fold checks it, so a log cannot crown anyone the engine did not.
    """

    kind: Literal["season_ended"] = "season_ended"
    winners: tuple[SeatId, ...]
    revealed: int = Field(ge=0)


# --- Goals ---------------------------------------------------------------------


class GoalSet(Model):
    """A seat's secret goal, fixed at Session Zero (spec §15). Private to the owner."""

    kind: Literal["goal_set"] = "goal_set"
    goal: Goal


class GoalProgressed(Model):
    """The goal's objective clock moved: ``parts_met`` of its parts hold now."""

    kind: Literal["goal_progressed"] = "goal_progressed"
    goal_id: GoalId
    parts_met: int = Field(ge=0)


class GoalMet(Model):
    """The goal's condition holds at the end of this tick, first time or again."""

    kind: Literal["goal_met"] = "goal_met"
    goal_id: GoalId


class GoalUndone(Model):
    """An Undoable goal stopped holding. It counts as unmet until it holds again."""

    kind: Literal["goal_undone"] = "goal_undone"
    goal_id: GoalId


class GoalOption(Model):
    text: str = Field(max_length=280)
    condition: WinCondition
    kind: GoalKind


class GoalProposed(Model):
    """The goals a player's agent offered on one route (spec §5 step 4). A record,
    Private to the seat and sealed with its key: these are the player's own words."""

    kind: Literal["goal_proposed"] = "goal_proposed"
    seat_id: SeatId
    route: Literal["history", "could_have", "own"]
    options: tuple[GoalOption, ...] = Field(max_length=3)


class GoalApproved(Model):
    """The host let a goal the balance check flagged stand. The host sees the scores
    and the flag, never the goal. A record."""

    kind: Literal["goal_approved"] = "goal_approved"
    goal_id: GoalId


# --- Tick lifecycle -----------------------------------------------------------


# --- Contests and history's clock (spec §5.3-5.4) --------------------------


class ContestCreated(Model):
    kind: Literal["contest_created"] = "contest_created"
    contest: Contest


class ContestMoved(Model):
    """A contest's track moved, by momentum or by a resolved order, and its
    countdown ran down by the in-world days the tick covered."""

    kind: Literal["contest_moved"] = "contest_moved"
    contest_id: ContestId
    change: int = Field(ge=-200, le=200)
    days: int = Field(default=0, ge=0)
    cause: Literal["momentum", "order"] = "momentum"


class ContestDecided(Model):
    """The countdown reached zero; the track decided it. ``winner`` None is a draw
    or a failed trial. The world keeps running."""

    kind: Literal["contest_decided"] = "contest_decided"
    contest_id: ContestId
    winner: EntityId | None = None


class ScheduledEventCreated(Model):
    """An item on history's clock, written by the world draft."""

    kind: Literal["scheduled_event_created"] = "scheduled_event_created"
    scheduled: ScheduledEvent


class ScheduledEventSkipped(Model):
    """A history event came due but its condition no longer held."""

    kind: Literal["scheduled_event_skipped"] = "scheduled_event_skipped"
    scheduled_id: ScheduledEventId


class DivergenceChanged(Model):
    kind: Literal["divergence_changed"] = "divergence_changed"
    change: int = Field(ge=-100, le=100)
    reason: str = Field(max_length=200)


class DateAdvanced(Model):
    """The calendar moved today's in-world date (spec §5.7)."""

    kind: Literal["date_advanced"] = "date_advanced"
    to: date


class TickOpened(Model):
    kind: Literal["tick_opened"] = "tick_opened"
    intents_frozen: tuple[IntentId, ...]


class CallCost(Model):
    call_class: str
    """resolver | narrator | structurer | persona | press | director | prose | counsel | aide."""
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


# --- Realism (core/realism) ---------------------------------------------------


class CardsDeclared(Model):
    """The scenario's cards, feeds and pipelines, compiled from the world file.

    One event, after the entities it names, so the fold can check every
    reference at once. Engine only: cards carry the AI policy fields.
    """

    kind: Literal["cards_declared"] = "cards_declared"
    cards: tuple[SeatCard, ...] = ()
    feeds: tuple[Feed, ...] = ()
    pipelines: tuple[Pipeline, ...] = ()


class DocumentIssued(Model):
    """A document is written. Its envelope scope is the view's; seats see it on arrival."""

    kind: Literal["document_issued"] = "document_issued"
    document: DocumentView
    truth: DocumentTruth


class DocumentWritten(Model):
    """The prose model wrote a document's page from its view and its author's voice.

    Scoped like the document. Folded into the view; a document is written at most
    once, so a resumed tick cannot write it twice."""

    kind: Literal["document_written"] = "document_written"
    doc_id: DocumentId
    prose: str = Field(min_length=1, max_length=4000)


class DocumentRead(Model):
    """A seat opened a document (the in-tray's unread mark). A record."""

    kind: Literal["document_read"] = "document_read"
    seat_id: SeatId
    doc_id: DocumentId


class DocumentFlagged(Model):
    """A seat flagged a document, or took the flag off. A record; the latest event wins."""

    kind: Literal["document_flagged"] = "document_flagged"
    seat_id: SeatId
    doc_id: DocumentId
    flagged: bool


class OrderDrafted(Model):
    """A seat pulled a lever: the order enters its pipeline at step 0."""

    kind: Literal["order_drafted"] = "order_drafted"
    order: Order


class OrderStepTaken(Model):
    """The person at the order's current step acted on it."""

    kind: Literal["order_step_taken"] = "order_step_taken"
    order_id: OrderId
    outcome: StepOutcome


class OrderSettled(Model):
    """The order left its pipeline: enacted, refused, lapsed or withdrawn."""

    kind: Literal["order_settled"] = "order_settled"
    order_id: OrderId
    status: OrderStatus


class QuantitySet(Model):
    """A quantity's value as of a date: authored at compile, computed by a resolver after."""

    kind: Literal["quantity_set"] = "quantity_set"
    quantity: Quantity


class CounselHeld(Model):
    """A seat consulted a person on its card. The words are prose and change nothing;
    a record, sealed with the seat's key like an order's text."""

    kind: Literal["counsel_held"] = "counsel_held"
    seat_id: SeatId
    person: EntityId
    channel: ChannelKind
    text: str = Field(max_length=8000)


class DirectiveGiven(Model):
    """A seat signed a directive: its own words to a contact, and the order the aide
    read them as, filed in the same batch. A record, sealed with the seat's key."""

    kind: Literal["directive_given"] = "directive_given"
    seat_id: SeatId
    person: EntityId
    channel: ChannelKind
    text: str = Field(max_length=1000)
    order_id: OrderId
    lever: Label
    params: dict[Label, ParamValue] = Field(default_factory=dict)


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
    | ContestCreated
    | ContestMoved
    | ContestDecided
    | ScheduledEventCreated
    | ScheduledEventSkipped
    | DivergenceChanged
    | DateAdvanced
    | NarrationWritten
    | StatementIssued
    | SessionZeroOpened
    | CharacterListed
    | CharacterClaimed
    | SessionZeroClosed
    | SeasonEnded
    | GoalSet
    | GoalProgressed
    | GoalMet
    | GoalUndone
    | GoalProposed
    | GoalApproved
    | BriefingDelivered
    | TickOpened
    | TickStageCompleted
    | TickClosed
    | CostLogged
    | AdminRead
    | CardsDeclared
    | DocumentIssued
    | DocumentWritten
    | DocumentRead
    | DocumentFlagged
    | OrderDrafted
    | OrderStepTaken
    | OrderSettled
    | QuantitySet
    | CounselHeld
    | DirectiveGiven
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
        "contest_created",
        "contest_moved",
        "contest_decided",
        "scheduled_event_created",
        "scheduled_event_skipped",
        "divergence_changed",
        "date_advanced",
        "session_zero_opened",
        "character_listed",
        "character_claimed",
        "session_zero_closed",
        "season_ended",
        "goal_set",
        "goal_progressed",
        "goal_met",
        "goal_undone",
        "tick_opened",
        "tick_closed",
        "cards_declared",
        "document_issued",
        "document_written",
        "order_drafted",
        "order_step_taken",
        "order_settled",
        "quantity_set",
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
