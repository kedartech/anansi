"""Seat cards: one record per person, for every office (pillar 2).

A card says what its person can do and how: formal powers, the levers that
actually move things, the people within reach and the channel to each, the
reports they receive, the pipelines their orders go through. One schema
serves every figure. A card marked ``playable`` can be claimed by a human;
every other person is run by code from the same card.

Three nested shapes, by who may read them:

- ``CardFace``: what anyone knows of the office. Every seat sees every face.
- ``Desk``: the face plus what the person works with. Only the person's own seat.
- ``SeatCard``: the desk plus the policy the engine runs an AI person by. Engine only.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from revisionist.core._base import Label, Model
from revisionist.core.ids import EntityId, FeedId, PipelineId
from revisionist.core.realism.quantity import ResolverKey


def words(key: str) -> str:
    """A key in words, for what the world gave no label: "send_bill" -> "Send bill"."""
    spoken = key.replace("_", " ").strip()
    return spoken[:1].upper() + spoken[1:]


class ChannelKind(StrEnum):
    """How words travel. Each trades speed against exposure (direction, channels table)."""

    IN_PERSON = "in_person"
    TELEPHONE = "telephone"
    CABLE = "cable"
    LETTER = "letter"
    COURIER = "courier"
    RADIO = "radio"
    PRESS = "press"


class ParamKind(StrEnum):
    INT = "int"
    ENUM = "enum"
    ENTITY = "entity"
    TEXT = "text"


class ParamSpec(Model):
    """One typed parameter of a lever. An order's params and every amendment must fit it."""

    name: Label
    kind: ParamKind
    lo: int | None = None
    hi: int | None = None
    choices: tuple[Label, ...] = ()
    # Empty display fields are left out of every dump, so campaigns logged before
    # they existed keep their state hash.
    label: Label = Field(default="", exclude_if=lambda v: not v)
    """What the form calls it. Empty: the client words the name itself."""
    choice_labels: tuple[Label, ...] = Field(default=(), exclude_if=lambda v: not v)
    """One per choice, in order, or none."""

    @model_validator(mode="after")
    def _shape(self) -> "ParamSpec":
        bounded = self.lo is not None or self.hi is not None
        if self.kind is ParamKind.INT:
            if self.lo is None or self.hi is None or self.lo > self.hi:
                raise ValueError(f"int parameter {self.name}: needs lo <= hi")
        elif bounded:
            raise ValueError(f"{self.kind.value} parameter {self.name}: only an int has bounds")
        if self.kind is ParamKind.ENUM:
            if not self.choices or len(set(self.choices)) != len(self.choices):
                raise ValueError(f"enum parameter {self.name}: needs distinct choices")
        elif self.choices:
            raise ValueError(f"{self.kind.value} parameter {self.name}: only an enum has choices")
        if self.choice_labels and len(self.choice_labels) != len(self.choices):
            raise ValueError(f"parameter {self.name}: needs one choice label per choice")
        return self


ParamValue = int | Annotated[str, StringConstraints(max_length=200)]
"""An int, an enum choice, an entity ID or a short text, as the spec says."""


class Lever(Model):
    """Something the person can set moving. Names a pipeline and a resolver, never a verb."""

    name: Label
    pipeline: PipelineId
    resolver: ResolverKey
    params: tuple[ParamSpec, ...] = ()
    label: Label = Field(default="", exclude_if=lambda v: not v)
    summary: str = Field(default="", max_length=300, exclude_if=lambda v: not v)

    @model_validator(mode="after")
    def _distinct(self) -> "Lever":
        names = [p.name for p in self.params]
        if len(set(names)) != len(names):
            raise ValueError(f"lever {self.name}: a parameter is named twice")
        return self


class Contact(Model):
    """A person this one can consult, and how the words travel."""

    person: EntityId
    channel: ChannelKind


class CardFace(Model):
    """What anyone knows of the office: who holds it, its powers and its channels."""

    person: EntityId
    office: Label
    playable: bool = False
    powers: tuple[Label, ...] = ()
    """Formal powers, as the constitution or the record states them."""
    contacts: tuple[Contact, ...] = ()


class Desk(CardFace):
    """The face plus what the person works with. Shown only to the person's own seat."""

    principals: tuple[EntityId, ...] = ()
    """Whom the person answers to: an electorate, a sovereign, a superior."""
    levers: tuple[Lever, ...] = ()
    """Actual levers: what moves things, as against the formal powers."""
    feeds: tuple[FeedId, ...] = ()
    """The reports this person receives."""
    pipelines: tuple[PipelineId, ...] = ()
    """The pipelines this person's orders may go through."""
    constraints: tuple[Label, ...] = ()
    personal: tuple[Label, ...] = ()
    """Personal factors: health, travel, absence from the capital."""
    successor: EntityId | None = None
    horizon_days: int = Field(ge=1, le=365)
    """How far ahead this person's decisions play out (pillar 5)."""


class SeatCard(Desk):
    """The whole card. The policy fields below are how code runs an unclaimed person;
    they never leave the engine."""

    voice: str = Field(default="", max_length=200)
    """How the person speaks and writes. Read by the prose and counsel models only."""
    loyalty: int = Field(default=0, ge=-5, le=5)
    """Disposition to the principal."""
    doctrine: tuple[Label, ...] = ()
    interests: tuple[Label, ...] = ()

    def face(self) -> CardFace:
        return CardFace.model_validate(self.model_dump(include=set(CardFace.model_fields)))

    def desk(self) -> Desk:
        return Desk.model_validate(self.model_dump(include=set(Desk.model_fields)))


__all__ = [
    "CardFace",
    "ChannelKind",
    "Contact",
    "Desk",
    "Lever",
    "ParamKind",
    "ParamSpec",
    "ParamValue",
    "SeatCard",
    "words",
]
