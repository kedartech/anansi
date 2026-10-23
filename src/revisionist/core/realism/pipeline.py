"""Orders as pipelines (pillar 4).

An order is a document that moves through people: drafted, then each step of
its pipeline, then settled. A step is a decision when the person at it could
say no for their own reasons; otherwise it is clerical and resolves as time.
Amendments are typed edits to the order's parameters, never free text.
"""

from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from revisionist.core._base import Label, Model
from revisionist.core.ids import EntityId, OrderId, PipelineId, SeatId
from revisionist.core.realism.card import ParamValue


class StepKind(StrEnum):
    DECISION = "decision"
    CLERICAL = "clerical"


class StepAction(StrEnum):
    EXPEDITE = "expedite"
    STALL = "stall"
    LEAK = "leak"
    AMEND = "amend"
    REFUSE = "refuse"
    PASS = "pass"


ADVANCES: frozenset[StepAction] = frozenset(
    {StepAction.EXPEDITE, StepAction.LEAK, StepAction.AMEND, StepAction.PASS}
)
"""Outcomes after which the order moves to the next step. A stall holds it there;
a refusal holds it until it is settled."""


class Step(Model):
    person: EntityId
    kind: StepKind
    days: int | None = Field(default=None, ge=0, le=365)
    """Typical days at this step. None while it is an unsourced gap."""
    allowed: frozenset[StepAction] = frozenset({StepAction.PASS})

    @model_validator(mode="after")
    def _shape(self) -> "Step":
        if StepAction.PASS not in self.allowed:
            raise ValueError("every step can pass an order")
        if self.kind is StepKind.CLERICAL and self.allowed != {StepAction.PASS}:
            raise ValueError("a clerical step only passes: it resolves as time")
        return self


class Pipeline(Model):
    pipeline_id: PipelineId
    name: Label
    steps: tuple[Step, ...] = Field(min_length=1, max_length=12)


class StepOutcome(Model):
    """What the person at one step did with the order."""

    step: int = Field(ge=0)
    person: EntityId
    action: StepAction
    on: date
    amended: dict[Label, ParamValue] = Field(default_factory=dict)
    """The parameters an ``amend`` changed, with their new values. Empty otherwise."""

    @model_validator(mode="after")
    def _amend_has_edits(self) -> "StepOutcome":
        if (self.action is StepAction.AMEND) != bool(self.amended):
            raise ValueError("an amendment, and only an amendment, edits parameters")
        return self


class OrderStatus(StrEnum):
    MOVING = "moving"
    ENACTED = "enacted"
    REFUSED = "refused"
    LAPSED = "lapsed"
    WITHDRAWN = "withdrawn"


SETTLED: frozenset[OrderStatus] = frozenset(OrderStatus) - {OrderStatus.MOVING}


class Order(Model):
    order_id: OrderId
    issuer: SeatId
    lever: Label
    pipeline: PipelineId
    params: dict[Label, ParamValue] = Field(default_factory=dict)
    status: OrderStatus = OrderStatus.MOVING
    step: int = Field(default=0, ge=0)
    """Index of the step the order is at. Equal to the step count once through."""
    filed: date
    outcomes: tuple[StepOutcome, ...] = ()


__all__ = [
    "ADVANCES",
    "SETTLED",
    "Order",
    "OrderStatus",
    "Pipeline",
    "Step",
    "StepAction",
    "StepKind",
    "StepOutcome",
]
