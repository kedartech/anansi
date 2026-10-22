"""Contests: the pressures that keep a world moving (spec §5.3). Phase 2 unit.

Pure functions over ``Contest``; the tick emits their events.
"""

from revisionist.core import events as ev
from revisionist.core.ids import ContestId, EntityId
from revisionist.core.intent import Intent, Verb
from revisionist.core.ontology import (
    TRACK_MAX,
    AssetKind,
    AssetStatus,
    Contest,
    ContestKind,
)
from revisionist.core.state import State, View

_EDGE_VERBS = frozenset(
    {
        Verb.ATTACK,
        Verb.DEFEND,
        Verb.SABOTAGE,
        Verb.INVESTIGATE,
        Verb.LEAK,
        Verb.FORGE,
        Verb.CONCEAL,
        Verb.NEGOTIATE,
        Verb.FUND,
    }
)
"""Copied from ``referee``'s force, info, negotiate and fund verbs: referee rates
orders with ``edge``, so importing its sets here would be a cycle."""
_STRENGTH_KINDS = frozenset({AssetKind.FORCE, AssetKind.OFFICE, AssetKind.NETWORK})


def _per_week(momentum: int, days: int) -> int:
    """``momentum * days / 7``, rounded half away from zero, in integers."""
    magnitude = (2 * abs(momentum) * days + 7) // 14
    return magnitude if momentum * days >= 0 else -magnitude


def momentum_moves(state: State, days: int) -> list[ev.ContestMoved]:
    """One ``ContestMoved`` per undecided contest: its momentum, scaled to the
    in-world ``days`` the tick covers, and its countdown run down by ``days``."""
    if days <= 0:
        return []
    moves: list[ev.ContestMoved] = []
    for k in sorted(state.contests.values(), key=lambda k: k.contest_id):
        if k.decided:
            continue
        change = max(k.floor - k.track, min(TRACK_MAX - k.track, _per_week(k.momentum, days)))
        moves.append(
            ev.ContestMoved(
                contest_id=k.contest_id,
                change=change,
                days=min(days, k.countdown_days),
                cause="momentum",
            )
        )
    return moves


def decisions(state: State) -> list[ev.ContestDecided]:
    """A ``ContestDecided`` for every undecided contest whose countdown is zero."""
    return [
        ev.ContestDecided(contest_id=k.contest_id, winner=leader(k))
        for k in sorted(state.contests.values(), key=lambda k: k.contest_id)
        if not k.decided and k.countdown_days == 0
    ]


def leader(contest: Contest) -> EntityId | None:
    """The side the track favours now; None when level or a trial not yet passed."""
    if contest.kind is ContestKind.TRIAL:
        return contest.sides[0] if contest.track == TRACK_MAX else None
    if contest.track > 0:
        return contest.sides[0]
    if contest.track < 0 and len(contest.sides) == 2:
        return contest.sides[1]
    return None


def side_of(world: State | View, entity: EntityId, contest: Contest) -> EntityId | None:
    """The side of ``contest`` that ``entity`` stands on: itself, or the nearest
    entity up its ``parent`` chain that is a side. None when it stands on none."""
    seen: set[EntityId] = set()
    at: EntityId | None = entity
    while at is not None and at not in seen:
        if at in contest.sides:
            return at
        seen.add(at)
        found = world.entities.get(at)
        at = found.parent if found is not None else None
    return None


def bearing(world: State | View, entity: EntityId) -> tuple[ContestId, ...]:
    """The undecided contests ``entity`` stands in (``side_of`` is not None), in
    ID order. Used by the odds edge, the character rule and the goal rule."""
    return tuple(
        k.contest_id
        for k in sorted(world.contests.values(), key=lambda k: k.contest_id)
        if not k.decided and side_of(world, entity, k) is not None
    )


def _actor(world: State | View, intent: Intent) -> EntityId | None:
    if isinstance(world, View):
        return world.me.embodies
    seat = world.seats.get(intent.actor)
    return seat.embodies if seat is not None else None


def _strength(world: State | View, contest: Contest, side: EntityId) -> int:
    """Tiers of the usable force, office and network assets that ``side`` controls.
    A degraded asset counts half; a lost one not at all."""
    total = 0
    for asset in world.assets.values():
        if asset.kind not in _STRENGTH_KINDS or asset.status is AssetStatus.LOST:
            continue
        if side_of(world, asset.controller, contest) != side:
            continue
        total += asset.tier // 2 if asset.status is AssetStatus.DEGRADED else asset.tier
    return total


def _pull(contest: Contest, side: EntityId) -> int:
    """+1 when the track already favours ``side`` by a wide margin, -1 when it
    favours another side that way, else 0."""
    if contest.kind is ContestKind.TRIAL:
        return 1 if contest.track >= 75 else -1 if contest.track <= 25 else 0
    lead = leader(contest)
    if lead is None or abs(contest.track) < 50:
        return 0
    return 1 if lead == side else -1


def edge(world: State | View, intent: Intent) -> int:
    """-2..+2 feasibility steps for an order that bears on a contest: strength
    against strength on that contest (spec §5.3, §13). 0 when it bears on none."""
    if intent.verb not in _EDGE_VERBS:
        return 0
    actor = _actor(world, intent)
    if actor is None or not (ids := bearing(world, actor)):
        return 0
    contest = world.contests[ids[0]]
    side = side_of(world, actor, contest)
    if side is None:
        return 0
    mine = _strength(world, contest, side)
    if contest.kind is ContestKind.TRIAL:
        theirs = (TRACK_MAX - contest.track) // 20
    else:
        theirs = max((_strength(world, contest, s) for s in contest.sides if s != side), default=0)
    base = max(-2, min(2, (mine - theirs) // 3))
    return max(-2, min(2, base + _pull(contest, side)))
