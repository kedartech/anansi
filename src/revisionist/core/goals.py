"""Secret goals and win conditions (README §15, decisions 23, 36-38).

A goal compiles at Session Zero into a ``WinCondition``: a closed set of
checks over State, combined with all-of and any-of. No model ever decides
whether a goal is met; ``goal_met`` does, from State alone.

Each goal has a kind from a fixed table keyed on its condition. A Lasting
goal, once met, stays met whatever the world does later. An Undoable goal
counts only while it holds, and must hold at season end to win.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, model_validator

from revisionist.core._base import Model
from revisionist.core.ids import (
    AssetId,
    ClockId,
    CommitmentId,
    EntityId,
    GoalId,
    MeterId,
    RelationshipId,
    SeatId,
)
from revisionist.core.ontology import AssetKind, AssetStatus, CommitmentStatus, EntityStatus
from revisionist.core.visibility import Scope

if TYPE_CHECKING:
    from revisionist.core.events import GoalMet, GoalProgressed, GoalUndone
    from revisionist.core.state import State, View

MAX_PARTS = 8
MAX_DEPTH = 3

Op = Literal["ge", "le"]


def _compare(value: int, op: Op, target: int) -> bool:
    return value >= target if op == "ge" else value <= target


# --- conditions ---------------------------------------------------------------


class HoldsOffice(Model):
    check: Literal["holds_office"] = "holds_office"
    holder: EntityId
    office: AssetId


class ControlsAsset(Model):
    check: Literal["controls_asset"] = "controls_asset"
    holder: EntityId
    asset: AssetId


class EntityIs(Model):
    check: Literal["entity_is"] = "entity_is"
    entity: EntityId
    status: EntityStatus


class MeterAt(Model):
    check: Literal["meter_at"] = "meter_at"
    meter: MeterId
    op: Op
    value: int


class CommitmentInForce(Model):
    check: Literal["commitment_in_force"] = "commitment_in_force"
    commitment: CommitmentId


class DispositionAt(Model):
    check: Literal["disposition_at"] = "disposition_at"
    relationship: RelationshipId
    op: Op
    value: int = Field(ge=-5, le=5)


class AllOf(Model):
    check: Literal["all_of"] = "all_of"
    conditions: tuple[WinCondition, ...] = Field(min_length=1, max_length=MAX_PARTS)


class AnyOf(Model):
    check: Literal["any_of"] = "any_of"
    conditions: tuple[WinCondition, ...] = Field(min_length=1, max_length=MAX_PARTS)


Leaf = HoldsOffice | ControlsAsset | EntityIs | MeterAt | CommitmentInForce | DispositionAt

WinCondition = Annotated[
    HoldsOffice
    | ControlsAsset
    | EntityIs
    | MeterAt
    | CommitmentInForce
    | DispositionAt
    | AllOf
    | AnyOf,
    Field(discriminator="check"),
]

AllOf.model_rebuild()
AnyOf.model_rebuild()


def depth(c: WinCondition) -> int:
    if isinstance(c, AllOf | AnyOf):
        return 1 + max(depth(x) for x in c.conditions)
    return 1


def leaves(c: WinCondition) -> tuple[Leaf, ...]:
    if isinstance(c, AllOf | AnyOf):
        return tuple(leaf for x in c.conditions for leaf in leaves(x))
    return (c,)


# --- goals --------------------------------------------------------------------


class GoalKind(StrEnum):
    LASTING = "lasting"
    """The world cannot reverse it. Once met, it stays met."""
    UNDOABLE = "undoable"
    """Counts only while it holds; must hold at season end."""


class Goal(Model):
    """One player seat's secret goal. Private to its owner (decision 37)."""

    goal_id: GoalId
    seat_id: SeatId
    text: str = Field(max_length=280)
    """The goal as the player agreed it, in words. Never shown to another seat."""
    condition: WinCondition
    kind: GoalKind
    clock_id: ClockId
    """The objective clock that shows progress to the owner."""
    met_tick: int | None = None
    """The first tick the goal held, if it ever has."""
    holding: bool = False
    """Met now, as far as winning goes: always true once a Lasting goal is met."""
    scope: Scope
    canary: str | None = None
    """Test-only unique marker, as on Fact."""

    @model_validator(mode="after")
    def _shape(self) -> Goal:
        if depth(self.condition) > MAX_DEPTH:
            raise ValueError(f"a win condition nests at most {MAX_DEPTH} deep")
        if len(leaves(self.condition)) > MAX_PARTS:
            raise ValueError(f"a win condition has at most {MAX_PARTS} checks")
        if self.kind is not goal_kind(self.condition):
            raise ValueError("a goal's kind comes from its condition")
        return self


_LASTING_STATUSES = frozenset({EntityStatus.DEAD, EntityStatus.DISSOLVED})


def goal_kind(c: WinCondition) -> GoalKind:
    """The fixed table (README §15). Only death and dissolution cannot be undone."""
    if isinstance(c, AllOf | AnyOf):
        kinds = {goal_kind(x) for x in c.conditions}
        return GoalKind.LASTING if kinds == {GoalKind.LASTING} else GoalKind.UNDOABLE
    if isinstance(c, EntityIs) and c.status in _LASTING_STATUSES:
        return GoalKind.LASTING
    return GoalKind.UNDOABLE


# --- checks -------------------------------------------------------------------


def goal_met(world: State | View, c: WinCondition) -> bool:
    """Whether ``c`` holds in ``world``. An ID the world does not hold is unmet."""
    match c:
        case HoldsOffice():
            office = world.assets.get(c.office)
            return (
                office is not None
                and office.kind is AssetKind.OFFICE
                and office.controller == c.holder
                and office.status is not AssetStatus.LOST
            )
        case ControlsAsset():
            asset = world.assets.get(c.asset)
            return (
                asset is not None
                and asset.controller == c.holder
                and asset.status is not AssetStatus.LOST
            )
        case EntityIs():
            entity = world.entities.get(c.entity)
            return entity is not None and entity.status is c.status
        case MeterAt():
            meter = world.meters.get(c.meter)
            return meter is not None and _compare(meter.value, c.op, c.value)
        case CommitmentInForce():
            commitment = world.commitments.get(c.commitment)
            return commitment is not None and commitment.status is CommitmentStatus.ACCEPTED
        case DispositionAt():
            rel = world.relationships.get(c.relationship)
            return rel is not None and _compare(rel.disposition, c.op, c.value)
        case AllOf():
            return all(goal_met(world, x) for x in c.conditions)
        case AnyOf():
            return any(goal_met(world, x) for x in c.conditions)


def parts(c: WinCondition) -> int:
    """How many parts the objective clock counts: the top-level checks of an all-of."""
    return len(c.conditions) if isinstance(c, AllOf) else 1


def progress(world: State | View, c: WinCondition) -> int:
    """Parts met now, out of ``parts(c)``. Shown to the owner as "2 of 3"."""
    if isinstance(c, AllOf):
        return sum(goal_met(world, x) for x in c.conditions)
    return int(goal_met(world, c))


def check(state: State) -> list[GoalProgressed | GoalMet | GoalUndone]:
    """What changed for every goal since the last check, in goal order.

    Runs at the end of APPLY, so a goal is met when its condition holds at the
    end of a tick. A Lasting goal stops being checked once met.
    """
    from revisionist.core.events import GoalMet, GoalProgressed, GoalUndone  # events imports us

    out: list[GoalProgressed | GoalMet | GoalUndone] = []
    for goal in sorted(state.goals.values(), key=lambda g: g.goal_id):
        if goal.holding and goal.kind is GoalKind.LASTING:
            continue
        clock = state.clocks.get(goal.clock_id)
        now = progress(state, goal.condition)
        if clock is not None and clock.segments_filled != now:
            out.append(GoalProgressed(goal_id=goal.goal_id, parts_met=now))
        met = goal_met(state, goal.condition)
        if met and not goal.holding:
            out.append(GoalMet(goal_id=goal.goal_id))
        elif not met and goal.holding:
            out.append(GoalUndone(goal_id=goal.goal_id))
    return out


def season_winners(state: State) -> frozenset[SeatId]:
    """Every seat whose goal counts as met at season end (decision 36)."""
    return frozenset(g.seat_id for g in state.goals.values() if g.holding)
