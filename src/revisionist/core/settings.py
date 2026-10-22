"""Session Zero output that the engine reads (spec §5)."""

from datetime import date
from enum import StrEnum
from typing import ClassVar

from pydantic import Field, field_validator

from revisionist.core._base import Model


class Plausibility(StrEnum):
    STRICT = "strict"
    PLAUSIBLE = "plausible"
    PULP = "pulp"


class Inertia(StrEnum):
    NONE = "none"
    TENDENCIES = "tendencies"
    STRONG = "strong"


class Lethality(StrEnum):
    SOFT = "soft"
    """Setbacks only."""
    HARD = "hard"
    """Permanent losses. Default."""
    BRUTAL = "brutal"
    """Elimination from events."""


class Betrayal(StrEnum):
    COOPERATIVE = "cooperative"
    INTRIGUE = "intrigue"
    CUTTHROAT = "cutthroat"


class Mode(StrEnum):
    GOALS = "goals"
    OPEN = "open"


class Settings(Model):
    plausibility: Plausibility = Plausibility.PLAUSIBLE
    inertia: Inertia = Inertia.TENDENCIES
    lethality: Lethality = Lethality.HARD
    betrayal: Betrayal = Betrayal.CUTTHROAT
    elimination: bool = True
    agent_harm: bool = True
    compromised_aide: bool = False
    mode: Mode = Mode.GOALS
    tone: str = "grounded"
    tick_hours: int = Field(default=24, ge=1, le=168)
    world_days_per_tick: int = Field(default=7, ge=1)
    starting_slots: int = Field(default=3, ge=1, le=5)
    slot_cap: int = Field(default=5, ge=1, le=5)
    lines: tuple[str, ...] = ()
    """Topics never on the table. Most restrictive wins. Sorted, unique."""
    veils: tuple[str, ...] = ("atrocity", "execution", "torture")
    """Topics that happen off screen only."""
    content_rating: str | None = None
    max_human_seats: int = Field(default=10, ge=1, le=10)
    end_date: date | None = None
    """The in-world end date the table picked (spec §5.7). None: fixed turns."""
    real_days: int | None = Field(default=None, ge=1, le=365)
    """The real-life length in days. With ``end_date``, sets the turn rate."""

    late_fields: ClassVar[frozenset[str]] = frozenset({"end_date", "real_days"})

    @field_validator("lines", "veils", mode="before")
    @classmethod
    def _sorted_unique(cls, value: object) -> object:
        if isinstance(value, list | tuple | set | frozenset):
            return tuple(sorted(set(value)))
        return value
