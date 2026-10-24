"""Geography: forces, fronts and routes on the map (realism, Batch 6).

A feature is truth, like a quantity: where a fleet lies, where a front runs,
which ports a convoy route joins. No seat sees one. Seats learn of features
from documents whose feed sights them, so a seat's map is only what its paper
says, as of when it said it.

A force sits at one place and moves by order (the movement resolver). A front
or a route joins two or more places and moves only with history: a feature's
``legs`` are the moves the record says it made, and an order to a force drops
the ones still to come.
"""

import math
from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from revisionist.core._base import Label, Model
from revisionist.core.ids import EntityId, FeatureId


class FeatureKind(StrEnum):
    FLEET = "fleet"
    ARMY = "army"
    AIR = "air"
    FRONT = "front"
    ROUTE = "route"


FORCES = frozenset({FeatureKind.FLEET, FeatureKind.ARMY, FeatureKind.AIR})

SPEED_KM_PER_DAY = {
    FeatureKind.FLEET: 450,  # [proposed] about 10 knots, a fleet's cruising pace
    FeatureKind.ARMY: 25,  # [proposed] a field army on the march
    FeatureKind.AIR: 1000,  # [proposed] an air group rebasing, a day's hops
}


class Leg(Model):
    """One move the record says a feature made."""

    since: date
    places: tuple[EntityId, ...] = Field(min_length=1)


class Feature(Model):
    feature_id: FeatureId
    kind: FeatureKind
    name: Label
    side: EntityId | None = None
    """The polity or body it belongs to. None for a front, which belongs to both."""
    tier: int = Field(default=0, ge=0, le=5)
    places: tuple[EntityId, ...] = Field(min_length=1)
    """A force: where it is. A front or route: the places it joins, in order."""
    as_of: date
    bound_for: EntityId | None = None
    """A force under orders: where it is going. It stays at ``places`` until it arrives."""
    arrives: date | None = None
    legs: tuple[Leg, ...] = ()
    """History's moves still to come, earliest first."""

    @model_validator(mode="after")
    def _shape(self) -> "Feature":
        force = self.kind in FORCES
        if force and len(self.places) != 1:
            raise ValueError(f"feature {self.feature_id}: a force is at one place")
        if not force and len(self.places) < 2:
            raise ValueError(f"feature {self.feature_id}: a {self.kind.value} joins two places")
        if (self.bound_for is None) != (self.arrives is None):
            raise ValueError(f"feature {self.feature_id}: bound_for and arrives go together")
        if self.bound_for is not None and not force:
            raise ValueError(f"feature {self.feature_id}: only a force moves by order")
        return self


class Sight(Model):
    """A document line's claim about a feature: what it is, whose, and where."""

    kind: FeatureKind
    side: EntityId | None = None
    places: tuple[EntityId, ...] = Field(min_length=1)


class SightRef(Model):
    """The truth behind one sighting line."""

    feature_id: FeatureId
    places: tuple[EntityId, ...]


EARTH_KM = 6371


def km_between(a: tuple[float, float], b: tuple[float, float]) -> int:
    """Great-circle distance between two (lat, lon) points, whole kilometres."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return round(2 * EARTH_KM * math.asin(math.sqrt(h)))


__all__ = [
    "FORCES",
    "SPEED_KM_PER_DAY",
    "Feature",
    "FeatureKind",
    "Leg",
    "Sight",
    "SightRef",
    "km_between",
]
