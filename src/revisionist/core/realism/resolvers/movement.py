"""The movement resolver (realism, Batch 6; the fifth, by Josh's word).

An enacted order whose lever names it sends one force to one place: the lever's
``force`` parameter names a feature key and ``to`` a place key. The force sets
out the day the order settles and arrives after the great-circle distance at its
kind's pace; it stays where it was until then. Only the issuer's own side moves:
an order naming another side's force changes nothing. An order drops the moves
history still had for that force. Fronts and routes move only with history.
"""

import math
from datetime import date, timedelta

from revisionist.core.ids import EntityId, FeatureId
from revisionist.core.ontology import EntityKind
from revisionist.core.realism import resolvers as rs
from revisionist.core.realism.geography import FORCES, SPEED_KM_PER_DAY, Feature, km_between
from revisionist.core.realism.pipeline import Order, OrderStatus
from revisionist.core.state import State
from revisionist.core.world_compile import object_id

FORCE, TO = "force", "to"
DEPTH = 8
"""How far up a person's parents their side may lie (person, body, polity)."""


def _of_side(state: State, person: EntityId, side: EntityId) -> bool:
    at: EntityId | None = person
    for _ in range(DEPTH):
        if at is None:
            return False
        if at == side:
            return True
        at = state.entities[at].parent if at in state.entities else None
    return False


def resolve(state: State, order: Order) -> tuple[Feature, ...]:
    """The force under way, or halted if sent where it already is."""
    if order.status is not OrderStatus.ENACTED:
        return ()
    cid = state.campaign_id
    force = state.features.get(FeatureId(object_id(cid, "feature", str(order.params.get(FORCE)))))
    to = state.entities.get(EntityId(object_id(cid, "entity", str(order.params.get(TO)))))
    if force is None or force.kind not in FORCES or force.side is None:
        return ()
    if to is None or to.kind is not EntityKind.LOCATION or to.at is None:
        return ()
    if not _of_side(state, state.seats[order.issuer].embodies, force.side):
        return ()
    as_of = max(rs.settled_on(order), force.as_of)
    here = force.places[0]
    if to.entity_id == here:
        return (
            force.model_copy(
                update={"bound_for": None, "arrives": None, "legs": (), "as_of": as_of}
            ),
        )
    origin = state.entities[here].at
    km = 0 if origin is None else km_between(origin, to.at)
    days = max(1, math.ceil(km / SPEED_KM_PER_DAY[force.kind]))
    return (
        force.model_copy(
            update={
                "bound_for": to.entity_id,
                "arrives": as_of + timedelta(days=days),
                "legs": (),
                "as_of": as_of,
            }
        ),
    )


def on_day(state: State, today: date) -> tuple[Feature, ...]:
    """Forces arriving today, and history's moves that fall due for the rest."""
    out: list[Feature] = []
    for f in sorted(state.features.values(), key=lambda x: x.feature_id):
        if f.bound_for is not None and f.arrives is not None and f.arrives <= today:
            out.append(
                f.model_copy(
                    update={
                        "places": (f.bound_for,),
                        "bound_for": None,
                        "arrives": None,
                        "as_of": today,
                    }
                )
            )
        elif f.bound_for is None and f.legs and f.legs[0].since <= today:
            due = [leg for leg in f.legs if leg.since <= today]
            out.append(
                f.model_copy(
                    update={"places": due[-1].places, "legs": f.legs[len(due) :], "as_of": today}
                )
            )
    return tuple(out)
