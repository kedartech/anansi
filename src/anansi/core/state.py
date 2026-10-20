"""State and View (README §7, §12).

``State`` is the full truth, a projection of the event log. ``View`` is what
one seat knows. They are different classes on purpose: a narrator, agent,
faction or outlet takes a ``View``, and passing a ``State`` is a type error
before it is a leak.

Layer 2 provides ``apply``, ``replay`` and ``project``; this module only
defines the shapes.
"""

from pydantic import Field

from anansi.core._base import Model
from anansi.core.ids import (
    AssetId,
    CampaignId,
    ClockId,
    CommitmentId,
    EntityId,
    FactId,
    IntentId,
    MeterId,
    RelationshipId,
    ScheduledEventId,
    SeatId,
)
from anansi.core.intent import Intent
from anansi.core.ontology import (
    Asset,
    Clock,
    Commitment,
    Entity,
    Fact,
    Meter,
    Relationship,
    ScheduledEvent,
    Seat,
)
from anansi.core.settings import Settings


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
    seats: dict[SeatId, Seat] = Field(default_factory=dict)


class State(_World):
    """The full truth. Only the referee and the resolver ever hold one."""

    last_seq: int = -1
    pending: dict[IntentId, Intent] = Field(default_factory=dict)
    """Filed, not yet frozen into a tick."""
    frozen: tuple[IntentId, ...] = ()
    """Intents in the tick being resolved."""


class View(_World):
    """One seat's knowledge. The only input to anything that writes prose."""

    viewer: SeatId
    own_pending: tuple[Intent, ...] = ()
    """The viewer's own filed intents; others' intents are never in a view."""
