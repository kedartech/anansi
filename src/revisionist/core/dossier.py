"""The opening dossier (playtest #28). Pure: built from one seat's View only.

Everything in a View is something the seat may see, so the dossier cannot name
what the seat does not know, and it costs no model call.
"""

from revisionist.core.dashboard import dashboard
from revisionist.core.ids import AssetId, EntityId, MeterId
from revisionist.core.ontology import AssetStatus, EntityKind
from revisionist.core.referee import holds
from revisionist.core.state import View
from revisionist.core.visibility import Visibility

_POWERS = frozenset({EntityKind.POLITY, EntityKind.FACTION, EntityKind.ORG})


def _name(view: View, obj: str) -> str:
    entity = view.entities.get(EntityId(obj))
    if entity is not None:
        return entity.name
    asset = view.assets.get(AssetId(obj))
    if asset is not None:
        return asset.name
    meter = view.meters.get(MeterId(obj))
    if meter is not None:
        return meter.name
    return "someone unknown"


def _word(disposition: int) -> str:
    if disposition <= -3:
        return "hostile"
    if disposition <= -1:
        return "cold"
    if disposition == 0:
        return "neutral"
    if disposition <= 2:
        return "warm"
    return "close"


def dossier(view: View) -> list[str]:
    """Plain lines. A line ending in ``:`` is a section header."""
    lines = [f"{view.me.name}, tick {view.tick}"]

    held = sorted(
        (
            a
            for a in view.assets.values()
            if holds(view, view.me.embodies, a.asset_id) and a.status is not AssetStatus.LOST
        ),
        key=lambda a: (a.kind.value, a.name),
    )
    lines.append("Your holdings:")
    if not held:
        lines.append("Nothing held.")
    for a in held:
        place = f", at {_name(view, a.location)}" if a.location else ""
        degraded = ", degraded" if a.status is AssetStatus.DEGRADED else ""
        lines.append(f"{a.name} ({a.kind.value}, tier {a.tier}{place}{degraded})")

    meters = sorted(
        (m for m in view.meters.values() if m.owner == view.me.embodies), key=lambda m: m.name
    )
    if meters:
        lines.append("Your standing:")
        lines += [f"{m.name}: {m.value} of {m.hi}" for m in meters]

    board = dashboard(view)
    if board.goal is not None:
        g = board.goal
        lines.append("Your goal:")
        lines.append(f"{g.text} ({g.parts_met} of {g.parts} parts met)")

    played = {s.embodies: s.name for s in view.seats.values()}
    powers = sorted((e for e in view.entities.values() if e.kind in _POWERS), key=lambda e: e.name)
    if powers:
        lines.append("Powers:")
        for e in powers:
            who = f" (played by {played[e.entity_id]})" if e.entity_id in played else ""
            lines.append(f"{e.name}{who}")

    places = sorted(
        (e for e in view.entities.values() if e.kind is EntityKind.LOCATION), key=lambda e: e.name
    )
    if places:
        lines.append("Places:")
        for e in places:
            where = f" (in {_name(view, e.parent)})" if e.parent else ""
            lines.append(f"{e.name}{where}")

    rels = [r for r in view.relationships.values() if view.me.embodies in (r.a, r.b)]
    if rels:
        lines.append("Relationships:")
        for r in sorted(rels, key=lambda r: _name(view, r.b if r.a == view.me.embodies else r.a)):
            other = r.b if r.a == view.me.embodies else r.a
            lines.append(f"{_name(view, other)}: {_word(r.disposition)} ({r.disposition:+d})")

    own_goal_clock = next(
        (g.clock_id for g in view.goals.values() if g.seat_id == view.viewer), None
    )
    clocks = [
        c for c in view.clocks.values() if c.status == "open" and c.clock_id != own_goal_clock
    ]
    if clocks:
        lines.append("Open clocks:")
        lines += [
            f"{c.name}: {c.segments_filled} of {c.segments_total}"
            for c in sorted(clocks, key=lambda c: c.name)
        ]

    public = sorted(
        (f for f in view.facts.values() if f.scope.visibility is Visibility.PUBLIC),
        key=lambda f: f.predicate,
    )
    if public:
        lines.append("Known to all:")
        for f in public:
            obj = _name(view, str(f.obj))
            if obj == "someone unknown":
                obj = str(f.obj)
            lines.append(f"{_name(view, f.subject)} {f.predicate} {obj}")

    secrets = [f for f in view.facts.values() if f.scope.visibility is not Visibility.PUBLIC]
    if secrets:
        lines.append("What you know that others may not:")
        for f in sorted(secrets, key=lambda f: f.predicate):
            obj = _name(view, str(f.obj))
            if obj == "someone unknown":
                obj = str(f.obj)
            lines.append(f"{_name(view, f.subject)} {f.predicate} {obj}")
    return lines
