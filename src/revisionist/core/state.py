"""State and View (spec §7, §12).

``State`` is the full truth, a projection of the event log. ``View`` is what
one seat knows. They are different classes on purpose: a narrator, agent,
faction or outlet takes a ``View``, and passing a ``State`` is a type error
before it is a leak.

Layer 2 provides ``apply``, ``replay`` and ``project``; this module only
defines the shapes.
"""

from datetime import date
from typing import ClassVar, Literal

from pydantic import Field

from revisionist.core._base import Model
from revisionist.core.goals import Goal
from revisionist.core.ids import (
    AssetId,
    CampaignId,
    ClockId,
    CommitmentId,
    ContestId,
    DocumentId,
    EntityId,
    FactId,
    FeedId,
    GoalId,
    IntentId,
    MeterId,
    OrderId,
    PipelineId,
    QuantityId,
    RelationshipId,
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
    Phase,
    Relationship,
    ScheduledEvent,
    Seat,
    SeatKind,
)
from revisionist.core.realism.card import CardFace, Desk, SeatCard
from revisionist.core.realism.document import Document, DocumentView, Feed
from revisionist.core.realism.pipeline import Order, Pipeline
from revisionist.core.realism.quantity import Quantity
from revisionist.core.settings import Settings

Ruleset = Literal["legacy", "realism"]


class _World(Model):
    """Fields common to truth and view."""

    campaign_id: CampaignId
    tick: int = 0
    settings: Settings = Settings()
    entities: dict[EntityId, Entity] = Field(default_factory=dict)
    assets: dict[AssetId, Asset] = Field(default_factory=dict)
    relationships: dict[RelationshipId, Relationship] = Field(default_factory=dict)
    meters: dict[MeterId, Meter] = Field(default_factory=dict)
    clocks: dict[ClockId, Clock] = Field(default_factory=dict)
    facts: dict[FactId, Fact] = Field(default_factory=dict)
    scheduled: dict[ScheduledEventId, ScheduledEvent] = Field(default_factory=dict)
    commitments: dict[CommitmentId, Commitment] = Field(default_factory=dict)
    goals: dict[GoalId, Goal] = Field(default_factory=dict)
    """Secret goals (Layer 10). A view holds only its own seat's."""
    phase: Phase = Phase.PLAY
    """A campaign from a hand-written world is in play from tick 0."""
    roster: dict[EntityId, Character] = Field(default_factory=dict)
    """The characters Session Zero offers. Public."""
    contests: dict[ContestId, Contest] = Field(default_factory=dict)
    """The pressures that keep the world moving (spec §5.3)."""
    divergence: int = Field(default=0, ge=0)
    """How far the world has drifted from the record (spec §5.4). Public."""
    in_world_date: date | None = None
    """Today in the world. None for a campaign with no start date."""
    ruleset: Ruleset = "legacy"
    """Set once, by ``campaign_created``. Public: the client hides odds under realism."""

    late_fields: ClassVar[frozenset[str]] = frozenset(
        {"goals", "phase", "roster", "contests", "divergence", "in_world_date", "ruleset"}
    )


class SeatFace(Model):
    """What any seat may know about another: who it is, never how it is run.

    spec §17: whether a seat is human or AI, its agent, its persona, its
    status (blinded, fallen) and its deputy are never shown to other seats.
    """

    seat_id: SeatId
    kind: SeatKind
    name: str
    embodies: EntityId


class State(_World):
    """The full truth. Only the referee and the resolver ever hold one."""

    seats: dict[SeatId, Seat] = Field(default_factory=dict)
    last_seq: int = -1
    pending: dict[IntentId, Intent] = Field(default_factory=dict)
    """Filed, not yet frozen into a tick."""
    frozen: tuple[IntentId, ...] = ()
    """Intents in the tick being resolved."""
    cards: dict[EntityId, SeatCard] = Field(default_factory=dict)
    """Realism: one card per person, policy fields included."""
    feeds: dict[FeedId, Feed] = Field(default_factory=dict)
    pipelines: dict[PipelineId, Pipeline] = Field(default_factory=dict)
    documents: dict[DocumentId, Document] = Field(default_factory=dict)
    """Both halves of every document issued, arrived or not."""
    orders: dict[OrderId, Order] = Field(default_factory=dict)
    quantities: dict[QuantityId, Quantity] = Field(default_factory=dict)

    late_fields: ClassVar[frozenset[str]] = _World.late_fields | {
        "cards",
        "feeds",
        "pipelines",
        "documents",
        "orders",
        "quantities",
    }


class View(_World):
    """One seat's knowledge. The only input to anything that writes prose."""

    viewer: SeatId
    me: Seat
    """The viewer's own seat, in full."""
    seats: dict[SeatId, SeatFace] = Field(default_factory=dict)
    """Every seat, the viewer's included, as others see it."""
    own_pending: tuple[Intent, ...] = ()
    """The viewer's own filed intents; others' intents are never in a view."""
    cards: dict[EntityId, CardFace] = Field(default_factory=dict)
    """Realism: every card's public face."""
    desk: Desk | None = None
    """Realism: the card of the person the viewer embodies, without its AI policy."""
    documents: dict[DocumentId, DocumentView] = Field(default_factory=dict)
    """Pages in scope that have arrived by today. Never a ``DocumentTruth``."""
    orders: dict[OrderId, Order] = Field(default_factory=dict)
    """The viewer's own orders only."""
    quantities: dict[QuantityId, Quantity] = Field(default_factory=dict)
    """Only quantities whose scope admits the viewer."""

    late_fields: ClassVar[frozenset[str]] = _World.late_fields | {
        "cards",
        "desk",
        "documents",
        "orders",
        "quantities",
    }
