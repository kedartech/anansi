"""The world bible format (build plan Layer 7).

A world file is hand-written YAML in the fixed ontology. Everything in it is
named by a short ``key`` (``north``, ``north_levies``) instead of an ID; the
compiler (``revisionist.core.world_compile``) turns keys into IDs derived from the
campaign and the key, so the same world compiled into the same campaign yields
the same events every time.

Keys share one namespace across every section: an entity and an asset may not
both be called ``north``. A reference names a key; the compiler resolves it to
the ID of the right type and refuses a key of the wrong type.

This module only defines the shapes. It is pure: no file reading, no YAML.
``revisionist.engine.worlds.load`` reads a file into a ``WorldFile``.
"""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from revisionist.core._base import Label, Model
from revisionist.core.intent import Verb
from revisionist.core.ontology import (
    AssetKind,
    ClauseKind,
    ClockKind,
    CommitmentStatus,
    Controller,
    EntityKind,
    SeatKind,
)
from revisionist.core.settings import Settings
from revisionist.core.visibility import Visibility

Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)]
"""A world-file name for one object. Lowercase, digits, underscores."""


class ScopeDecl(Model):
    """A non-public scope. ``known_by`` names seat keys."""

    visibility: Visibility
    known_by: tuple[Key, ...] = ()


ScopeSpec = Literal["public"] | ScopeDecl
"""``public`` or a mapping ``{visibility: covert, known_by: [north]}``."""


class EntityDecl(Model):
    key: Key
    kind: EntityKind
    name: Label
    parent: Key | None = None
    location: Key | None = None
    tags: tuple[Label, ...] = ()


class AssetDecl(Model):
    key: Key
    kind: AssetKind
    name: Label
    tier: int = Field(default=1, ge=0, le=5)
    controller: Key
    """An entity key."""
    location: Key | None = None
    loyalty: int = Field(default=0, ge=-5, le=5)
    scope: ScopeSpec = "public"


class RelationshipDecl(Model):
    key: Key
    a: Key
    b: Key
    disposition: int = Field(ge=-5, le=5)
    price_tags: tuple[Label, ...] = ()
    leverage_tags: tuple[Label, ...] = ()
    scope: ScopeSpec = "public"


class MeterDecl(Model):
    key: Key
    owner: Key
    """An entity key."""
    name: Label
    value: int
    lo: int = 0
    hi: int = 100
    max_delta_per_tick: int = Field(default=10, ge=1)
    scope: ScopeSpec = "public"


class ClockDecl(Model):
    key: Key
    kind: ClockKind
    name: Label
    owner: Key | None = None
    """A seat key."""
    target: Key | None = None
    """An entity key."""
    segments_total: int = Field(ge=1, le=12)
    segments_filled: int = Field(default=0, ge=0)
    exposure: int = Field(default=0, ge=0, le=5)
    holds_slot: bool = False
    scope: ScopeSpec = "public"


class FactDecl(Model):
    key: Key
    subject: Key
    """An entity key."""
    predicate: Label
    obj: str = Field(max_length=60)
    """An entity, asset or clock key if one exists by that name; otherwise a literal label."""
    truth: bool = True
    exposure: int = Field(default=0, ge=0, le=5)
    scope: ScopeSpec = "public"
    canary: str | None = Field(default=None, max_length=60)
    """A unique string that must never reach a seat outside ``scope``."""


class ClauseDecl(Model):
    kind: ClauseKind
    party: Key
    beneficiary: Key
    asset: Key | None = None
    verb: Verb | None = None
    amount: int | None = None
    until_tick: int | None = None


class CommitmentDecl(Model):
    key: Key
    parties: tuple[Key, ...] = Field(min_length=2)
    clauses: tuple[ClauseDecl, ...] = Field(min_length=1)
    status: CommitmentStatus = CommitmentStatus.ACCEPTED
    scope: ScopeSpec = "public"


class AgentDecl(Model):
    """The seat's chief of staff. ``entity`` is a person entity key."""

    entity: Key
    name: Label
    role: Label
    voice: str = Field(default="", max_length=200)
    backstory: str = Field(default="", max_length=400)
    relationship: str = Field(default="", max_length=200)


class SeatDecl(Model):
    key: Key
    kind: SeatKind
    name: Label
    embodies: Key
    """An entity key."""
    controller: Controller = Controller.AI
    slots: int | None = Field(default=None, ge=0, le=5)
    """None means ``settings.starting_slots``."""
    objectives: tuple[Key, ...] = ()
    """Clock keys."""
    standing_orders: tuple[str, ...] = ()
    agent: AgentDecl | None = None


Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$", max_length=40)]
"""A world's file name: lowercase, digits, underscores and hyphens."""


class WorldFile(Model):
    world: Slug
    """Slug. Becomes ``CampaignCreated.world``."""
    name: Label
    """Becomes ``CampaignCreated.name``."""
    settings: Settings = Settings()
    entities: tuple[EntityDecl, ...] = ()
    assets: tuple[AssetDecl, ...] = ()
    relationships: tuple[RelationshipDecl, ...] = ()
    meters: tuple[MeterDecl, ...] = ()
    clocks: tuple[ClockDecl, ...] = ()
    facts: tuple[FactDecl, ...] = ()
    commitments: tuple[CommitmentDecl, ...] = ()
    seats: tuple[SeatDecl, ...] = ()


__all__ = [
    "AgentDecl",
    "AssetDecl",
    "ClauseDecl",
    "ClockDecl",
    "CommitmentDecl",
    "EntityDecl",
    "FactDecl",
    "Key",
    "MeterDecl",
    "RelationshipDecl",
    "ScopeDecl",
    "ScopeSpec",
    "SeatDecl",
    "Slug",
    "WorldFile",
]
