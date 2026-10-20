"""State and View (README §7, §12).

``State`` is the full truth, a projection of the event log. ``View`` is what
one seat knows. They are different classes on purpose: a narrator, agent,
faction or outlet takes a ``View``, and passing a ``State`` is a type error
before it is a leak.

Layer 2 provides ``apply``, ``replay`` and ``project``; this module only
defines the shapes.
"""

from typing import ClassVar

from pydantic import Field

from revisionist.core._base import Model
from revisionist.core.goals import Goal
from revisionist.core.ids import (
    AssetId,
    CampaignId,
    ClockId,
    CommitmentId,
    EntityId,
    FactId,
    GoalId,
    IntentId,
    MeterId,
    RelationshipId,
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
    Relationship,
    ScheduledEvent,
    Seat,
    SeatKind,
)
from revisionist.core.settings import Settings


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

    late_fields: ClassVar[frozenset[str]] = frozenset({"goals"})


class SeatFace(Model):
    """What any seat may know about another: who it is, never how it is run.

    README §17: whether a seat is human or AI, its agent, its persona, its
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


class View(_World):
    """One seat's knowledge. The only input to anything that writes prose."""

    viewer: SeatId
    me: Seat
    """The viewer's own seat, in full."""
    seats: dict[SeatId, SeatFace] = Field(default_factory=dict)
    """Every seat, the viewer's included, as others see it."""
    own_pending: tuple[Intent, ...] = ()
    """The viewer's own filed intents; others' intents are never in a view."""
