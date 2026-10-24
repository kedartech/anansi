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

A ``ruleset: realism`` world adds cards, feeds, pipelines, quantities and
sources. It names no human seat: any card marked ``playable`` may be claimed
at Session Zero, and every person left unclaimed is run by code. Every number
authored in a quantity, feed or pipeline carries ``source: <a sources key>``
or ``gap: true``, so an unsourced figure can never pass as a sourced one.
"""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from revisionist.core._base import Label, Model
from revisionist.core.goals import WinCondition
from revisionist.core.intent import Verb
from revisionist.core.ontology import (
    AssetKind,
    ClauseKind,
    ClockKind,
    CommitmentStatus,
    ContestKind,
    Controller,
    EntityKind,
    SeatKind,
)
from revisionist.core.realism.card import ChannelKind, ParamSpec
from revisionist.core.realism.document import Bias, Classification, DocumentKind
from revisionist.core.realism.pipeline import StepAction, StepKind
from revisionist.core.realism.quantity import ResolverKey
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


class ContestDecl(Model):
    """A contest (spec §5.3). ``sides`` names entity keys: a rivalry's 2-4 sides,
    or a trial's one power under trial."""

    key: Key
    kind: ContestKind
    name: Label
    prize: str = Field(max_length=200)
    sides: tuple[Key, ...] = Field(min_length=1, max_length=4)
    track: int = Field(default=0, ge=-100, le=100)
    momentum: int = Field(default=0, ge=-20, le=20)
    countdown_days: int = Field(ge=1)
    scope: ScopeSpec = "public"


class HistoryDecl(Model):
    """An item on history's clock (spec §5.4): what really followed the start date.

    The condition names keys, like a goal's. ``weight`` is how central it was.
    """

    key: Key
    name: str = Field(max_length=120)
    on_date: date
    condition: WinCondition
    weight: int = Field(default=1, ge=1, le=3)
    scope: ScopeSpec = "public"


class SideDecl(Model):
    """Where a roster character stands in one contest."""

    contest: Key
    side: Key
    """An entity key: one of the contest's sides."""


class GoalDecl(Model):
    """A player seat's secret goal. In a world file the condition names keys, not IDs.

    The compiler gives the goal its objective clock and its kind; neither is
    written here, because the kind comes from a fixed table (spec §15).
    """

    key: Key
    seat: Key
    text: str = Field(max_length=280)
    condition: WinCondition
    canary: str | None = Field(default=None, max_length=60)


class CharacterDecl(Model):
    """A character on the Session Zero roster. ``entity`` is a person key.

    ``composite`` has no default on purpose: whoever writes the roster must say
    whether the record names this person or the character is built from a role.
    """

    entity: Key
    pitch: str = Field(max_length=200)
    reach: str = Field(max_length=200)
    composite: bool
    means: tuple[Key, ...] = ()
    """Asset keys the character controls at the start date."""
    sides: tuple[SideDecl, ...] = ()
    """The character's side in each contest they bear on. Public."""
    aliases: tuple[Annotated[str, StringConstraints(max_length=60)], ...] = Field(
        default=(), max_length=6
    )
    """Other names people use for this person. Public."""


# --- realism ---------------------------------------------------------------------


class SourceDecl(Model):
    """A published source numbers are cited to. ``verified`` stays false until its
    coverage and licence are checked (plan, 0b)."""

    key: Key
    title: str = Field(max_length=200)
    note: str = Field(default="", max_length=400)
    verified: bool = False


class Cited(Model):
    """A declaration holding authored numbers: cited to a source, or marked a gap."""

    source: Key | None = None
    gap: bool = False

    @model_validator(mode="after")
    def _cited_or_gap(self) -> "Cited":
        if getattr(self, "computed", False):
            if self.source is not None or self.gap:
                raise ValueError("a computed figure has no source and is not a gap")
            return self
        if (self.source is None) == (not self.gap):
            raise ValueError("give exactly one of source: <key> or gap: true")
        return self


class QuantityDecl(Cited):
    key: Key
    owner: Key | None = None
    """An entity key."""
    name: Label
    value: int | None = None
    """Left out only for a gap."""
    unit: Label
    as_of: date | None = None
    """None means the world's start date."""
    public: bool = False
    """Public quantities are in every view; the rest reach seats only as documents."""
    computed: bool = False
    """A resolver's output: no value until code computes one, so neither cited nor a gap."""

    @model_validator(mode="after")
    def _gap_has_no_value(self) -> "QuantityDecl":
        if self.computed and self.value is not None:
            raise ValueError(f"quantity {self.key}: a computed figure starts with no value")
        if self.value is None and not (self.gap or self.computed):
            raise ValueError(f"quantity {self.key}: a value, gap: true or computed: true")
        return self


class FeedDecl(Cited):
    key: Key
    name: Label
    author: Key
    """A person key."""
    kind: DocumentKind
    channel: ChannelKind
    classification: Classification = Classification.OPEN
    every_days: int | None = Field(default=None, ge=1, le=365)
    latency_days: int | None = Field(default=None, ge=0, le=365)
    assumed: bool = False
    """The cadence and latency are a labelled default by document kind, not sourced."""
    reports: tuple[Key, ...] = ()
    """Quantity keys."""
    bias: Bias = Bias()

    @model_validator(mode="after")
    def _assumed_is_set(self) -> "FeedDecl":
        if self.assumed and (self.every_days is None or self.latency_days is None):
            raise ValueError(f"feed {self.key}: an assumed timing still needs its numbers")
        return self


class StepDecl(Model):
    person: Key
    kind: StepKind
    days: int | None = Field(default=None, ge=0, le=365)
    assumed: bool = False
    """``days`` is a labelled default by step kind, not sourced."""
    allowed: tuple[StepAction, ...] = (StepAction.PASS,)

    @model_validator(mode="after")
    def _assumed_is_set(self) -> "StepDecl":
        if self.assumed and self.days is None:
            raise ValueError(f"step at {self.person}: an assumed length still needs its days")
        return self


class PipelineDecl(Cited):
    key: Key
    name: Label
    steps: tuple[StepDecl, ...] = Field(min_length=1, max_length=12)


class LeverDecl(Model):
    name: Label
    pipeline: Key
    resolver: ResolverKey
    params: tuple[ParamSpec, ...] = ()
    label: Label = ""
    summary: str = Field(default="", max_length=300)


class ContactDecl(Model):
    person: Key
    channel: ChannelKind


class CardDecl(Model):
    """A person's seat card. ``person`` is a person key; the card has no key of its own."""

    person: Key
    office: Label
    playable: bool = False
    principals: tuple[Key, ...] = ()
    powers: tuple[Label, ...] = ()
    levers: tuple[LeverDecl, ...] = ()
    contacts: tuple[ContactDecl, ...] = ()
    feeds: tuple[Key, ...] = ()
    pipelines: tuple[Key, ...] = ()
    constraints: tuple[Label, ...] = ()
    personal: tuple[Label, ...] = ()
    successor: Key | None = None
    horizon_days: int = Field(ge=1, le=365)
    voice: str = Field(default="", max_length=200)
    loyalty: int = Field(default=0, ge=-5, le=5)
    doctrine: tuple[Label, ...] = ()
    interests: tuple[Label, ...] = ()


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
    goals: tuple[GoalDecl, ...] = ()
    start: str = Field(default="", max_length=80)
    """A point in history, "Florence, April 1478". Set, it opens Session Zero."""
    roster: tuple[CharacterDecl, ...] = ()
    notes: tuple[Annotated[str, StringConstraints(max_length=200)], ...] = ()
    """Readings the GM chose where the record is disputed or silent (spec §5)."""
    start_date: date | None = None
    """The in-world start date (Session Zero v3). Set, the draft needs contests."""
    contests: tuple[ContestDecl, ...] = ()
    schedule: tuple[HistoryDecl, ...] = ()
    """History's clock: the key events that really followed ``start_date``."""
    ruleset: Literal["legacy", "realism"] = "legacy"
    cards: tuple[CardDecl, ...] = ()
    feeds: tuple[FeedDecl, ...] = ()
    pipelines: tuple[PipelineDecl, ...] = ()
    quantities: tuple[QuantityDecl, ...] = ()
    sources: tuple[SourceDecl, ...] = ()


__all__ = [
    "AgentDecl",
    "AssetDecl",
    "CardDecl",
    "CharacterDecl",
    "Cited",
    "ClauseDecl",
    "ClockDecl",
    "CommitmentDecl",
    "ContactDecl",
    "ContestDecl",
    "EntityDecl",
    "FactDecl",
    "FeedDecl",
    "GoalDecl",
    "HistoryDecl",
    "Key",
    "LeverDecl",
    "MeterDecl",
    "PipelineDecl",
    "QuantityDecl",
    "RelationshipDecl",
    "ScopeDecl",
    "ScopeSpec",
    "SeatDecl",
    "SideDecl",
    "Slug",
    "SourceDecl",
    "StepDecl",
    "WorldFile",
]
