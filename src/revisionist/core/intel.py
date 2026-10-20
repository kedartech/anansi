"""Intelligence on secret goals (README §7, decision 37).

A rival's goal is learned only by working for it. Each successful
``investigate`` against a seat returns one **piece**, in a fixed order from
vague to exact: that the seat has an aim, its domain, its object, then the
whole condition. Pieces are ordinary Facts in the seeker's knowledge layer.

A partial success may return a piece that is false: a forged decoy the target
planted with ``forge``, or else, on a seeded roll, the right kind of thing with
the wrong object. The engine writes pieces, never a model, and the true goal
text never leaves its owner's view.
"""

import hashlib

from revisionist.core import delta as d
from revisionist.core.goals import (
    AllOf,
    AnyOf,
    CommitmentInForce,
    ControlsAsset,
    DispositionAt,
    EntityIs,
    Goal,
    HoldsOffice,
    Leaf,
    MeterAt,
    WinCondition,
    leaves,
)
from revisionist.core.ids import (
    AssetId,
    CommitmentId,
    DeltaId,
    EntityId,
    FactId,
    MeterId,
    RelationshipId,
    SeatId,
    derived_id,
)
from revisionist.core.intent import Intent, Verb
from revisionist.core.ontology import SeatKind
from revisionist.core.resolution import OutcomeTier
from revisionist.core.state import State, View
from revisionist.core.visibility import Scope, Visibility, private_to

EXISTS, DOMAIN, OBJECT, CONDITION = "goal_exists", "goal_domain", "goal_object", "goal_condition"
PIECES = (EXISTS, DOMAIN, OBJECT, CONDITION)
"""Fact predicates for goal pieces, in the order a seeker learns them."""

HAS_AIM = "has a secret aim"
LABEL_MAX = 60

_LANDS = frozenset({OutcomeTier.PARTIAL, OutcomeTier.SUCCESS, OutcomeTier.SUCCESS_OPPORTUNITY})


def _label(text: str) -> str:
    return text if len(text) <= LABEL_MAX else text[: LABEL_MAX - 3] + "..."


def goal_of(world: State | View, seat: SeatId) -> Goal | None:
    return next((g for g in world.goals.values() if g.seat_id == seat), None)


def held(world: State | View, seeker: SeatId, subject: EntityId) -> frozenset[str]:
    """Goal pieces about ``subject`` that ``seeker`` knows, true or not."""
    return frozenset(
        f.predicate
        for f in world.facts.values()
        if f.subject == subject and f.predicate in PIECES and f.scope.visible_to(seeker)
    )


# --- what a piece says ------------------------------------------------------------


def _name(state: State, ref: str) -> str:
    if (asset := state.assets.get(AssetId(ref))) is not None:
        return asset.name
    if (entity := state.entities.get(EntityId(ref))) is not None:
        return entity.name
    if (meter := state.meters.get(MeterId(ref))) is not None:
        return meter.name
    if (rel := state.relationships.get(RelationshipId(ref))) is not None:
        return f"ties between {_name(state, rel.a)} and {_name(state, rel.b)}"
    if (commitment := state.commitments.get(CommitmentId(ref))) is not None:
        return "a treaty of " + " and ".join(sorted(_name(state, p) for p in commitment.parties))
    return "something unknown"


def _object(leaf: Leaf) -> str:
    match leaf:
        case HoldsOffice():
            return leaf.office
        case ControlsAsset():
            return leaf.asset
        case EntityIs():
            return leaf.entity
        case MeterAt():
            return leaf.meter
        case CommitmentInForce():
            return leaf.commitment
        case DispositionAt():
            return leaf.relationship


def domain(state: State, leaf: Leaf) -> str:
    """The kind of thing a goal is about (README §7: office, wealth, a person, ...)."""
    match leaf:
        case HoldsOffice():
            return "office"
        case ControlsAsset():
            asset = state.assets.get(leaf.asset)
            return asset.kind.value if asset is not None else "an asset"
        case EntityIs():
            entity = state.entities.get(leaf.entity)
            return entity.kind.value if entity is not None else "a person"
        case MeterAt():
            return "standing"
        case CommitmentInForce():
            return "a treaty"
        case DispositionAt():
            return "a relationship"


def render(state: State, c: WinCondition, *, swap: tuple[str, str] | None = None) -> str:
    """The condition in a few words. ``swap`` replaces one object's name with another."""

    def name(ref: str) -> str:
        if swap is not None and ref == swap[0]:
            return swap[1]
        return _name(state, ref)

    match c:
        case AllOf():
            return " and ".join(render(state, x, swap=swap) for x in c.conditions)
        case AnyOf():
            return " or ".join(render(state, x, swap=swap) for x in c.conditions)
        case HoldsOffice():
            return f"{name(c.holder)} holds {name(c.office)}"
        case ControlsAsset():
            return f"{name(c.holder)} controls {name(c.asset)}"
        case EntityIs():
            return f"{name(c.entity)} {c.status.value}"
        case MeterAt():
            return f"{name(c.meter)} {'at least' if c.op == 'ge' else 'at most'} {c.value}"
        case CommitmentInForce():
            return f"{name(c.commitment)} in force"
        case DispositionAt():
            return f"{name(c.relationship)} {'at least' if c.op == 'ge' else 'at most'} {c.value}"


def _decoy(state: State, leaf: Leaf, seed: str) -> str | None:
    """Another object of the same kind, picked by the seed: right domain, wrong object."""
    true = _object(leaf)
    match leaf:
        case HoldsOffice() | ControlsAsset():
            asset = state.assets.get(AssetId(true))
            pool: list[str] = sorted(
                a
                for a, x in state.assets.items()
                if asset is not None and x.kind is asset.kind and a != true
            )
        case EntityIs():
            entity = state.entities.get(EntityId(true))
            pool = sorted(
                e
                for e, x in state.entities.items()
                if entity is not None and x.kind is entity.kind and e != true
            )
        case MeterAt():
            pool = sorted(m for m in state.meters if m != true)
        case DispositionAt():
            pool = sorted(r for r in state.relationships if r != true)
        case CommitmentInForce():
            pool = []
    if not pool:
        return None
    return pool[_draw(seed, "decoy") % len(pool)]


def _draw(seed: str, purpose: str) -> int:
    return int.from_bytes(hashlib.blake2b(f"{seed}:{purpose}".encode(), digest_size=4).digest())


# --- investigate and forge ------------------------------------------------------


def _target_seat(state: State, intent: Intent) -> SeatId | None:
    for target in intent.targets:
        for seat in sorted(state.seats.values(), key=lambda s: s.seat_id):
            if (
                seat.embodies == target
                and seat.seat_id != intent.actor
                and seat.kind is SeatKind.PLAYER
                and goal_of(state, seat.seat_id) is not None
            ):
                return seat.seat_id
    return None


def _forged(state: State, subject: EntityId, owner: SeatId, predicate: str) -> str | None:
    """A decoy the goal's owner planted for this piece, if any."""
    planted = sorted(
        (f for f in state.facts.values() if f.subject == subject and f.predicate == predicate),
        key=lambda f: f.fact_id,
    )
    for f in planted:
        if not f.truth and f.scope.known_by == {owner} and f.scope.visibility is Visibility.COVERT:
            return str(f.obj)
    return None


def piece(state: State, intent: Intent, outcome: OutcomeTier, seed: str) -> d.FactAssert | None:
    """The next goal piece an ``investigate`` earns, or None.

    Success gives the true piece. A partial success gives the true piece too,
    unless the target planted a decoy for it, or the seeded roll misleads:
    then the object (and the condition built on it) is the wrong one.
    """
    if intent.verb is not Verb.INVESTIGATE or outcome not in _LANDS:
        return None
    target = _target_seat(state, intent)
    if target is None:
        return None
    goal = goal_of(state, target)
    assert goal is not None
    subject = state.seats[target].embodies
    have = held(state, intent.actor, subject)
    nxt = next((p for p in PIECES if p not in have), None)
    if nxt is None:
        return None
    leaf = leaves(goal.condition)[0]
    truth = True
    match nxt:
        case "goal_exists":
            obj = HAS_AIM
        case "goal_domain":
            obj = domain(state, leaf)
            if outcome is OutcomeTier.PARTIAL and (fake := _forged(state, subject, target, nxt)):
                obj, truth = fake, False
        case _:
            wrong: str | None = None
            if outcome is OutcomeTier.PARTIAL:
                wrong = _forged(state, subject, target, OBJECT)
                if wrong is None and _draw(seed, "mislead") % 2 == 0:
                    decoy = _decoy(state, leaf, seed)
                    wrong = _name(state, decoy) if decoy is not None else None
            if nxt == OBJECT:
                obj = wrong or _name(state, _object(leaf))
            else:
                swap = (_object(leaf), wrong) if wrong else None
                obj = render(state, goal.condition, swap=swap)
            truth = wrong is None
    return d.FactAssert(
        delta_id=DeltaId(derived_id(state.campaign_id, state.tick, "piece", intent.intent_id)),
        reason="goal intelligence",
        fact_id=FactId(derived_id(state.campaign_id, state.tick, "piece-fact", intent.intent_id)),
        subject=subject,
        predicate=nxt,
        obj=_label(obj),
        truth=truth,
        scope=private_to(intent.actor),
    )


def decoys(state: State, intent: Intent, outcome: OutcomeTier) -> list[d.FactAssert]:
    """A ``forge`` naming your own seat's entity plants a false domain and object for
    rival spies to find, about the first other thing it names (README §7).

    The decoy is covert and known only to you; an investigation's partial
    success returns it in place of the truth.
    """
    if intent.verb is not Verb.FORGE or outcome not in _LANDS:
        return []
    seat = state.seats.get(intent.actor)
    if seat is None or goal_of(state, seat.seat_id) is None or seat.embodies not in intent.targets:
        return []
    bait = next(
        (
            t
            for t in intent.targets
            if t != seat.embodies
            and (t in state.assets or t in state.entities or t in state.meters)
        ),
        None,
    )
    if bait is None:
        return []
    if (asset := state.assets.get(AssetId(bait))) is not None:
        bait_domain = asset.kind.value
    elif (entity := state.entities.get(EntityId(bait))) is not None:
        bait_domain = entity.kind.value
    else:
        bait_domain = "standing"
    covert = Scope(visibility=Visibility.COVERT, known_by=frozenset({seat.seat_id}))
    out = []
    for predicate, obj in ((DOMAIN, bait_domain), (OBJECT, _name(state, bait))):
        out.append(
            d.FactAssert(
                delta_id=DeltaId(
                    derived_id(state.campaign_id, state.tick, "decoy", intent.intent_id, predicate)
                ),
                reason="forged goal",
                fact_id=FactId(
                    derived_id(
                        state.campaign_id, state.tick, "decoy-f", intent.intent_id, predicate
                    )
                ),
                subject=seat.embodies,
                predicate=predicate,
                obj=_label(obj),
                truth=False,
                scope=covert,
            )
        )
    return out


__all__ = [
    "CONDITION",
    "DOMAIN",
    "EXISTS",
    "HAS_AIM",
    "OBJECT",
    "PIECES",
    "decoys",
    "domain",
    "goal_of",
    "held",
    "piece",
    "render",
]
