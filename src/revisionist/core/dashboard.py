"""The dashboard and the season's end (spec §15).

A dashboard is built from one seat's ``View`` only: its standing, means and
slots, a few of its own meters, its goal's progress (it sees only its own
goal) and the goal pieces it has gathered on each rival. The season report is
the full reveal: every goal, who met it and when, and who had learned what.
"""

from revisionist.core._base import Model
from revisionist.core.goals import GoalKind, season_winners
from revisionist.core.intel import PIECES
from revisionist.core.ontology import AssetStatus, Controller, SeatKind
from revisionist.core.state import State, View

METERS_SHOWN = 3


class GoalLine(Model):
    text: str
    kind: GoalKind
    parts_met: int
    parts: int
    met_tick: int | None
    holding: bool


class Dashboard(Model):
    seat: str
    tick: int
    slots: int
    means: tuple[str, ...]
    meters: tuple[str, ...]
    goal: GoalLine | None
    rivals: tuple[tuple[str, tuple[str, ...]], ...]
    """Per rival seat: the goal pieces this seat holds about it, in learning order."""


def dashboard(view: View) -> Dashboard:
    me = view.me.embodies
    means = tuple(
        f"{a.name} (tier {a.tier}" + ("" if a.status is AssetStatus.ACTIVE else ", degraded") + ")"
        for a in sorted(view.assets.values(), key=lambda a: a.name)
        if a.controller == me and a.status is not AssetStatus.LOST
    )
    meters = tuple(
        f"{m.name} {m.value}"
        for m in sorted(view.meters.values(), key=lambda m: m.name)
        if m.owner == me
    )[:METERS_SHOWN]
    goal = None
    own = [g for g in view.goals.values() if g.seat_id == view.viewer]
    if own:
        g = own[0]
        clock = view.clocks.get(g.clock_id)
        goal = GoalLine(
            text=g.text,
            kind=g.kind,
            parts_met=clock.segments_filled if clock else 0,
            parts=clock.segments_total if clock else 1,
            met_tick=g.met_tick,
            holding=g.holding,
        )
    rivals = []
    for face in sorted(view.seats.values(), key=lambda s: s.name):
        if face.seat_id == view.viewer or face.kind is not SeatKind.PLAYER:
            continue
        held = {
            f.predicate: str(f.obj)
            for f in view.facts.values()
            if f.subject == face.embodies and f.predicate in PIECES
        }
        if held:
            rivals.append((face.name, tuple(f"{p[5:]}: {held[p]}" for p in PIECES if p in held)))
    return Dashboard(
        seat=view.me.name,
        tick=view.tick,
        slots=view.me.slots,
        means=means,
        meters=meters,
        goal=goal,
        rivals=tuple(rivals),
    )


def _goal_state(g: GoalLine) -> str:
    if g.holding:
        return "met" if g.kind is GoalKind.LASTING else "holding (keep it to season end)"
    if g.met_tick is not None:
        return "undone: it must hold again"
    return "not yet"


def render(d: Dashboard) -> list[str]:
    lines = [f"{d.seat}, tick {d.tick}. Slots {d.slots}."]
    if d.means:
        lines.append("Means: " + "; ".join(d.means) + ".")
    if d.meters:
        lines.append("Standing: " + "; ".join(d.meters) + ".")
    if d.goal is not None:
        g = d.goal
        lines.append(
            f"Your goal ({g.kind.value}): {g.text} Progress {g.parts_met} of {g.parts}; "
            + _goal_state(g)
            + "."
        )
    for name, pieces in d.rivals:
        lines.append(f"On {name}'s aims: " + "; ".join(pieces) + ".")
    return lines


# --- season end -----------------------------------------------------------------------


def season_report(before: State, revealed: int = 0) -> list[str]:
    """The full reveal (spec §15), from the state just before it.

    That state still knows who had gathered which pieces on whom. Every winner
    is listed, or that there were none; ``revealed`` is how many covert secrets
    the reveal made public.
    """
    winners = season_winners(before)
    names = {s.seat_id: s.name for s in before.seats.values()}
    lines = [f"Season's end at tick {before.tick}."]
    lines.append(
        "Winners: " + ", ".join(sorted(names[w] for w in winners)) + "."
        if winners
        else "No one met their goal."
    )
    for g in sorted(before.goals.values(), key=lambda g: names[g.seat_id]):
        when = f"first met at tick {g.met_tick}" if g.met_tick is not None else "never met"
        end = "held at the end" if g.holding else "not held at the end"
        lines.append(f"{names[g.seat_id]} sought: {g.text} ({g.kind.value}; {when}; {end}).")
        target = before.seats[g.seat_id].embodies
        for seeker in sorted(before.seats.values(), key=lambda s: s.name):
            if seeker.seat_id == g.seat_id or seeker.controller is not Controller.HUMAN:
                continue
            got = [
                f
                for f in before.facts.values()
                if f.subject == target
                and f.predicate in PIECES
                and f.scope.visible_to(seeker.seat_id)
            ]
            if got:
                false = sum(not f.truth for f in got)
                lie = f", {false} of them false" if false else ""
                lines.append(f"  {seeker.name} had uncovered {len(got)} of 4 pieces{lie}.")
    if revealed:
        lines.append(f"{revealed} covert secrets are now public.")
    return lines
