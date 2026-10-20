"""The fixed, world-agnostic ontology (README §12).

Campaigns create instances of these types and never extend them. Session
Zero maps setting nouns onto them: "oxygen quota" is a Meter, "the Moon
Senate" is an Office asset, a spymaster's informants are a Network asset.

Nothing is ever deleted. Entities and assets change ``status``; the log is
append-only and so is the ontology.
"""

from enum import StrEnum

from pydantic import Field

from wyrd.core._base import Label, Model
from wyrd.core.ids import (
    AssetId,
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
from wyrd.core.intent import Verb
from wyrd.core.visibility import Scope

Tier = int
"""0-5. Validated at the field."""

TIER = Field(ge=0, le=5)


class EntityKind(StrEnum):
    POLITY = "polity"
    FACTION = "faction"
    PERSON = "person"
    ORG = "org"
    LOCATION = "location"


class EntityStatus(StrEnum):
    ACTIVE = "active"
    CAPTURED = "captured"
    TURNED = "turned"
    DEAD = "dead"
    DISSOLVED = "dissolved"


class Entity(Model):
    entity_id: EntityId
    kind: EntityKind
    name: str
    status: EntityStatus = EntityStatus.ACTIVE
    parent: EntityId | None = None
    """Polity for a faction, org for a person, and so on."""
    location: EntityId | None = None
    tags: frozenset[str] = frozenset()


class AssetKind(StrEnum):
    FORCE = "force"
    WEALTH = "wealth"
    NETWORK = "network"
    OFFICE = "office"
    INFO = "info"
    FACILITY = "facility"


class AssetStatus(StrEnum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    LOST = "lost"


class Asset(Model):
    asset_id: AssetId
    kind: AssetKind
    name: str
    tier: Tier = TIER
    controller: EntityId
    location: EntityId | None = None
    loyalty: int = Field(default=0, ge=-5, le=5)
    """To the controller. Negative loyalty is a defection risk."""
    status: AssetStatus = AssetStatus.ACTIVE
    scope: Scope
    """Who knows this asset exists. A secret army is a Covert Force."""


class Relationship(Model):
    relationship_id: RelationshipId
    a: EntityId
    b: EntityId
    disposition: int = Field(ge=-5, le=5)
    price_tags: frozenset[str] = frozenset()
    """What b wants from a (or vice versa) to move disposition."""
    leverage_tags: frozenset[str] = frozenset()
    """What a holds over b."""
    scope: Scope


class Meter(Model):
    meter_id: MeterId
    owner: EntityId
    name: str
    value: int
    lo: int = 0
    hi: int = 100
    max_delta_per_tick: int = Field(default=10, ge=1)
    scope: Scope


class ClockKind(StrEnum):
    OPERATION = "operation"
    """Held by a seat; occupies a slot until resolved."""
    OBJECTIVE = "objective"
    """A Goals-mode objective, scored at checkpoints."""
    THREAT = "threat"
    """A danger ticking toward someone; director fuel."""
    OPPORTUNITY = "opportunity"
    CAPABILITY = "capability"
    """Abstract dangerous capability (README §19). Never narrated as procedure."""


class ClockStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    FAILED = "failed"
    ABANDONED = "abandoned"


class Clock(Model):
    clock_id: ClockId
    kind: ClockKind
    name: str
    owner: SeatId | None = None
    """The seat whose slot this holds, for operations and objectives."""
    target: EntityId | None = None
    """For threat clocks: the entity the danger is ticking toward."""
    opened_tick: int = Field(default=0, ge=0)
    intent_id: IntentId | None = None
    segments_total: int = Field(ge=1, le=12)
    segments_filled: int = Field(default=0, ge=0)
    eta_tick: int | None = None
    exposure: int = Field(default=0, ge=0, le=5)
    """0 = no exposure roll. Rolled independently of the outcome."""
    holds_slot: bool = False
    status: ClockStatus = ClockStatus.OPEN
    scope: Scope

    @property
    def complete(self) -> bool:
        return self.segments_filled >= self.segments_total


class Fact(Model):
    """A structured truth with an ACL. Never prose."""

    fact_id: FactId
    subject: EntityId
    predicate: str
    """Closed vocabulary per world bible, e.g. ``funds``, ``plans_coup_against``."""
    obj: EntityId | AssetId | ClockId | Label
    truth: bool = True
    """False facts exist: forgeries and disinformation are Facts with truth=False."""
    exposure: int = Field(default=0, ge=0, le=5)
    asserted_tick: int = Field(default=0, ge=0)
    scope: Scope
    canary: str | None = None
    """Test-only unique marker scanned for in outbound text. Never set in play."""


class ScheduledEvent(Model):
    scheduled_id: ScheduledEventId
    fire_at_tick: int = Field(ge=0)
    name: str
    source_intent: IntentId | None = None
    source_clock: ClockId | None = None
    scope: Scope
    fired: bool = False


class ClauseKind(StrEnum):
    TRANSFER = "transfer"
    """Move an asset's controller on a condition."""
    ABSTAIN = "abstain"
    """A verb the party will not use against the other."""
    SUPPORT = "support"
    """A verb the party will use for the other on request."""
    PAYMENT = "payment"
    """A Wealth tier per tick."""


class Clause(Model):
    kind: ClauseKind
    party: EntityId
    beneficiary: EntityId
    asset: AssetId | None = None
    verb: Verb | None = None
    amount: int | None = None
    until_tick: int | None = None


class CommitmentStatus(StrEnum):
    OFFERED = "offered"
    ACCEPTED = "accepted"
    BROKEN = "broken"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


class Commitment(Model):
    """A treaty, contract or deal. Promises in chat are not Commitments."""

    commitment_id: CommitmentId
    parties: frozenset[EntityId]
    clauses: tuple[Clause, ...]
    status: CommitmentStatus = CommitmentStatus.OFFERED
    scope: Scope


class Controller(StrEnum):
    HUMAN = "human"
    AI = "ai"


class SeatKind(StrEnum):
    PLAYER = "player"
    """A seat a human can hold; AI-run when nobody does."""
    FACTION = "faction"
    """An unplayed power, run by an agent in permanent deputy mode."""
    OUTLET = "outlet"
    """A press outlet. Has a view, writes narration, holds no slots."""
    NPC = "npc"
    """A named character available for live conversation."""


class SeatStatus(StrEnum):
    ACTIVE = "active"
    BLINDED = "blinded"
    """Agent lost (README §10). Public feed only, 1 slot, no deputy."""
    FALLEN = "fallen"
    """Eliminated. Awaiting redesign or retired."""


class Persona(Model):
    """The player-designed face of their agent. Narrative only; no mechanics."""

    name: str
    role: str
    voice: str = ""
    backstory: str = ""
    relationship: str = ""


class Seat(Model):
    """Anything that holds a view of the world (README §6, §7, §10).

    Humans, AI factions, press outlets and NPCs are all Seats, because they
    all need ``project(state, seat_id)``. Only ``PLAYER`` seats hold slots
    and an agent persona.
    """

    seat_id: SeatId
    kind: SeatKind
    name: str
    embodies: EntityId
    """The person or office in the world this seat acts as."""
    controller: Controller = Controller.AI
    status: SeatStatus = SeatStatus.ACTIVE
    slots: int = Field(default=3, ge=0, le=5)
    agent: EntityId | None = None
    """The in-world agent entity. ``None`` means blinded (or not a player seat)."""
    persona: Persona | None = None
    objectives: frozenset[ClockId] = frozenset()
    standing_orders: tuple[str, ...] = ()
    """Free text the deputy reads. Structured by the agent, not the resolver."""
    joined_tick: int = 0
