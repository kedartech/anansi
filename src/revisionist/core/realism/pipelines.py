"""Running orders through their pipelines (plan, Phase 2 unit B2).

A due clerical step passes as time. A due decision step is acted on by the
person holding it: a human seat decides for itself (the order waits, and the
desk shows decision needed); anyone else follows a deterministic policy read
from their card, with seeded friction. A leak issues a press document. An
amendment is a typed edit that fits the lever's parameter spec. An order through
its last step settles as enacted; a refusal settles it as refused.
"""

from datetime import date

from revisionist.core import events as ev
from revisionist.core.ids import OrderId
from revisionist.core.ontology import Controller
from revisionist.core.realism.card import ParamValue
from revisionist.core.realism.pipeline import Order, OrderStatus, StepAction, StepKind
from revisionist.core.state import State

RealismStep = ev.OrderStepTaken | ev.OrderSettled | ev.DocumentIssued


def waiting_on_human(state: State, order: Order) -> bool:
    """True when the order is moving and its current step is a decision step held by
    a person a human seat embodies. A clerical step never waits: it passes as time."""
    steps = state.pipelines[order.pipeline].steps
    if order.status is not OrderStatus.MOVING or order.step >= len(steps):
        return False
    step = steps[order.step]
    if step.kind is StepKind.CLERICAL:
        return False
    return any(
        s.embodies == step.person and s.controller is Controller.HUMAN for s in state.seats.values()
    )


def run_pipelines(state: State, today: date, seed: str) -> list[RealismStep]:
    """Every AI and clerical step due on or before ``today``, in date order, each
    dated the day it fell due, plus the settlements and leaks they cause. Steps
    held by a human are left waiting. Pure: same state, day and seed, same list."""
    raise NotImplementedError("unit B2")


def human_step(
    state: State,
    order_id: OrderId,
    action: StepAction,
    today: date,
    amended: dict[str, ParamValue] | None = None,
) -> list[RealismStep]:
    """A human's decision at the step they hold, with what it causes. Raises
    ``ValueError`` when the order is not waiting on them or the action is not allowed."""
    raise NotImplementedError("unit B2")


__all__ = ["RealismStep", "human_step", "run_pipelines", "waiting_on_human"]
