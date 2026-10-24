"""Running orders through their pipelines (plan, Phase 2 unit B2).

A due clerical step passes as time. A due decision step is acted on by the
person holding it: a human seat decides for itself (the order waits, and the
desk shows decision needed); anyone else follows a deterministic policy read
from their card, with seeded friction. A leak issues a press document. An
amendment is a typed edit that fits the lever's parameter spec. An order through
its last step settles as enacted; a refusal settles it as refused.
"""

from datetime import date, timedelta

from revisionist.core import events as ev
from revisionist.core.ids import DocumentId, EntityId, OrderId, derived_id
from revisionist.core.ontology import Controller
from revisionist.core.realism import resolvers
from revisionist.core.realism.card import (
    ChannelKind,
    Lever,
    ParamKind,
    ParamSpec,
    ParamValue,
    words,
)
from revisionist.core.realism.document import (
    BodyField,
    Classification,
    DocumentKind,
    DocumentTruth,
    DocumentView,
)
from revisionist.core.realism.pipeline import (
    ADVANCES,
    Order,
    OrderStatus,
    StepAction,
    StepKind,
    StepOutcome,
    step_due,
)
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC, Scope, Visibility

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


STALLS_TO_LAPSE = 3  # [proposed] the third stall at one step lapses the order
LEAK_DELAY_DAYS = 1  # [proposed] a leak reaches the press a day after the step
LEAK_BELOW = 5  # [proposed] leak when a draw in [0, 100) falls under this
REFUSE_AT = -6  # [proposed] policy score thresholds
STALL_AT = -2
AMEND_AT = -1
EXPEDITE_AT = 5
FREE_ORDERS = 2  # [proposed] orders a person carries before workload bites


def _lever(state: State, order: Order) -> Lever:
    seat = state.seats[order.issuer]
    for lever in state.cards[seat.embodies].levers:
        if lever.name == order.lever:
            return lever
    raise KeyError(f"order {order.order_id}: lever {order.lever} is not on the issuer's card")


def _specs(state: State, order: Order) -> dict[str, ParamSpec]:
    return {p.name: p for p in _lever(state, order).params}


def _check_amended(state: State, order: Order, amended: dict[str, ParamValue]) -> None:
    specs = _specs(state, order)
    for name, val in amended.items():
        spec = specs.get(name)
        if name not in order.params or spec is None:
            raise ValueError(f"order has no parameter {name}")
        if spec.kind is ParamKind.INT:
            if (
                not isinstance(val, int)
                or spec.lo is None
                or spec.hi is None
                or not spec.lo <= val <= spec.hi
            ):
                raise ValueError(f"{name}: {val!r} is outside the lever's range")
        elif spec.kind is ParamKind.ENUM:
            if val not in spec.choices:
                raise ValueError(f"{name}: {val!r} is not a choice")
        elif not isinstance(val, str):
            raise ValueError(f"{name}: {val!r} does not fit the lever")


def _words(text: str) -> set[str]:
    return set(text.lower().replace("_", " ").split())


def _policy(
    state: State,
    orders: dict[OrderId, Order],
    order: Order,
    seed: str,
    allowed: frozenset[StepAction],
) -> tuple[StepAction, dict[str, ParamValue]]:
    """What an AI-held decision step does with the order. [proposed] throughout."""
    steps = state.pipelines[order.pipeline].steps
    person = steps[order.step].person
    card = state.cards.get(person)
    loyalty = card.loyalty if card else 0
    mine = _words(order.lever) | {
        w for v in order.params.values() if isinstance(v, str) for w in _words(v)
    }
    held = _words(" ".join((*card.interests, *card.doctrine))) if card else set()
    interest = 2 if mine & held else 0
    n = sum(
        1
        for o in orders.values()
        if o.order_id != order.order_id
        and o.status is OrderStatus.MOVING
        and o.step < len(state.pipelines[o.pipeline].steps)
        and state.pipelines[o.pipeline].steps[o.step].person == person
    )
    stalls = _stalls_here(order)
    f = resolvers.friction(state, seed, order.order_id, order.step, stalls, n=10) - 3
    score = loyalty + interest + f - max(0, n - FREE_ORDERS)

    amended: dict[str, ParamValue] = {}
    if score <= REFUSE_AT:
        action = StepAction.REFUSE
    elif score <= STALL_AT:
        action = StepAction.STALL
    elif resolvers.friction(state, seed, order.order_id, order.step, "leak", n=100) < LEAK_BELOW:
        action = StepAction.LEAK
    elif score == AMEND_AT and (amended := _trim(state, order)):
        action = StepAction.AMEND
    elif score >= EXPEDITE_AT:
        action = StepAction.EXPEDITE
    else:
        action = StepAction.PASS
    if action not in allowed:
        return StepAction.PASS, {}
    return action, amended


def _trim(state: State, order: Order) -> dict[str, ParamValue]:
    """The first int parameter cut by a quarter of its range, or empty if it cannot move."""
    for spec in _specs(state, order).values():
        cur = order.params.get(spec.name)
        if (
            spec.kind is ParamKind.INT
            and isinstance(cur, int)
            and spec.lo is not None
            and spec.hi is not None
        ):
            new = max(spec.lo, cur - max(1, (spec.hi - spec.lo) // 4))
            return {spec.name: new} if new != cur else {}
    return {}


def _stalls_here(order: Order) -> int:
    return sum(1 for o in order.outcomes if o.step == order.step and o.action is StepAction.STALL)


def _param_label(spec: ParamSpec | None, name: str) -> str:
    return (spec.label if spec is not None else "") or words(name)


def _param_value(spec: ParamSpec | None, value: ParamValue) -> int | str:
    """A parameter's value in words: an enum choice by its label, never its key."""
    if isinstance(value, int):
        return value
    if spec is not None and spec.kind is ParamKind.ENUM and value in spec.choices:
        if spec.choice_labels:
            return spec.choice_labels[spec.choices.index(value)]
        return words(value)[:60]
    return value[:60]


def _leak(state: State, order: Order, author: EntityId, on: date) -> ev.DocumentIssued:
    doc_id = DocumentId(derived_id(state.campaign_id, "leak", order.order_id, order.step))
    lever = _lever(state, order)
    specs = {p.name: p for p in lever.params}
    body = tuple(
        BodyField(label=_param_label(specs.get(k), k), value=_param_value(specs.get(k), v))
        for k, v in order.params.items()
    )
    return ev.DocumentIssued(
        document=DocumentView(
            doc_id=doc_id,
            kind=DocumentKind.PRESS,
            author=author,
            issued=on,
            arrives=on + timedelta(days=LEAK_DELAY_DAYS),
            channel=ChannelKind.PRESS,
            title=f"Leaked: {lever.label or words(lever.name)}"[:60],
            body=body,
            scope=PUBLIC,
        ),
        truth=DocumentTruth(doc_id=doc_id),
    )


_DONE: dict[StepAction, str] = {
    StepAction.PASS: "passed it on",
    StepAction.EXPEDITE: "pressed it forward",
    StepAction.STALL: "held it",
    StepAction.AMEND: "amended it",
    StepAction.REFUSE: "refused it",
    StepAction.LEAK: "leaked it",
}

_REPLY_KIND: dict[ChannelKind, DocumentKind] = {
    ChannelKind.CABLE: DocumentKind.CABLE,
    ChannelKind.LETTER: DocumentKind.LETTER,
    ChannelKind.COURIER: DocumentKind.LETTER,
    ChannelKind.TELEPHONE: DocumentKind.MEMO,
    ChannelKind.IN_PERSON: DocumentKind.MEMO,
    ChannelKind.RADIO: DocumentKind.MEMO,
    ChannelKind.PRESS: DocumentKind.PRESS,
}
"""A call or a meeting reaches the page as a note of it."""


def _trim_title(text: str, limit: int = 60) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,:;") + "…"


def _reply(
    state: State, order: Order, outcome: StepOutcome, status: OrderStatus | None
) -> ev.DocumentIssued | None:
    """The word that comes back to the issuer from the person at the step, or None
    when that person is the issuer's own."""
    seat = state.seats[order.issuer]
    if outcome.person == seat.embodies:
        return None
    lever = _lever(state, order)
    specs = {p.name: p for p in lever.params}
    channel = next(
        (c.channel for c in state.cards[seat.embodies].contacts if c.person == outcome.person),
        ChannelKind.LETTER,
    )
    label = lever.label or words(lever.name)
    lead = lever.params[0] if lever.params else None
    given = order.params.get(lead.name) if lead is not None else None
    if lead is not None and lead.choice_labels and isinstance(given, str):
        title = f"On your order: {_param_value(lead, given)}"
    else:
        title = f"On your order: {label}"
    body = [
        BodyField(label="What was done", value=_DONE[outcome.action]),
        BodyField(label="Order", value=label[:60]),
        *(
            BodyField(
                label=f"Changed: {_param_label(specs.get(k), k)}"[:60],
                value=_param_value(specs.get(k), v),
            )
            for k, v in outcome.amended.items()
        ),
    ]
    if status is not None:
        body.append(BodyField(label="Outcome", value=status.value))
    doc_id = DocumentId(derived_id(state.campaign_id, "reply", order.order_id, len(order.outcomes)))
    return ev.DocumentIssued(
        document=DocumentView(
            doc_id=doc_id,
            kind=_REPLY_KIND[channel],
            author=outcome.person,
            issued=outcome.on,
            arrives=outcome.on,  # [proposed] no channel delay yet
            channel=channel,
            classification=Classification.CONFIDENTIAL,  # [proposed]
            title=_trim_title(title),
            body=tuple(body),
            scope=Scope(visibility=Visibility.PRIVATE, known_by=frozenset({order.issuer})),
        ),
        truth=DocumentTruth(doc_id=doc_id),
    )


def _act(
    state: State,
    order: Order,
    action: StepAction,
    on: date,
    amended: dict[str, ParamValue],
) -> tuple[Order, list[RealismStep]]:
    """One step taken on ``order`` on ``on``: the events and the order as fold leaves it."""
    steps = state.pipelines[order.pipeline].steps
    step = steps[order.step]
    outcome = StepOutcome(
        step=order.step, person=step.person, action=action, on=on, amended=amended
    )
    out: list[RealismStep] = [ev.OrderStepTaken(order_id=order.order_id, outcome=outcome)]
    if action is StepAction.LEAK:
        out.append(_leak(state, order, step.person, on))
    status: OrderStatus | None = None
    stalls = _stalls_here(order) + (action is StepAction.STALL)
    nxt = order.step + (action in ADVANCES)
    if action is StepAction.REFUSE:
        status = OrderStatus.REFUSED
    elif action is StepAction.STALL and stalls >= STALLS_TO_LAPSE:
        status = OrderStatus.LAPSED
    elif nxt >= len(steps):
        status = OrderStatus.ENACTED
    if status is not None:
        out.append(ev.OrderSettled(order_id=order.order_id, status=status))
    if (reply := _reply(state, order, outcome, status)) is not None:
        out.append(reply)
    moved = order.model_copy(
        update={
            "params": {**order.params, **amended},
            "step": nxt,
            "outcomes": (*order.outcomes, outcome),
            "status": status or order.status,
        }
    )
    return moved, out


def run_pipelines(state: State, today: date, seed: str) -> list[RealismStep]:
    """Every AI and clerical step due on or before ``today``, in date order, each
    dated the day it fell due, plus the settlements and leaks they cause. Steps
    held by a human are left waiting. Pure: same state, day and seed, same list.

    ``expedite`` advances like ``pass``: it has no timing effect yet."""
    orders = {
        o.order_id: o
        for o in state.orders.values()
        if o.status is OrderStatus.MOVING and not waiting_on_human(state, o)
    }
    events: list[RealismStep] = []
    while True:
        due = sorted(
            (d, oid)
            for oid, o in orders.items()
            if o.status is OrderStatus.MOVING
            and (d := step_due(o, state.pipelines[o.pipeline])) is not None
            and d <= today
        )
        if not due:
            return events
        on, oid = due[0]
        order = orders[oid]
        step = state.pipelines[order.pipeline].steps[order.step]
        if waiting_on_human(state, order):
            del orders[oid]
            continue
        amended: dict[str, ParamValue] = {}
        if step.kind is StepKind.CLERICAL:
            action = StepAction.PASS
        else:
            action, amended = _policy(state, orders, order, seed, step.allowed)
        orders[oid], out = _act(state, order, action, on, amended)
        events += out


def human_step(
    state: State,
    order_id: OrderId,
    action: StepAction,
    today: date,
    amended: dict[str, ParamValue] | None = None,
) -> list[RealismStep]:
    """A human's decision at the step they hold, with what it causes. Raises
    ``ValueError`` when the order is not waiting on them or the action is not allowed."""
    order = state.orders.get(order_id)
    if order is None or not waiting_on_human(state, order):
        raise ValueError(f"order {order_id} is not waiting on a human")
    step = state.pipelines[order.pipeline].steps[order.step]
    if action not in step.allowed:
        raise ValueError(f"step {order.step} does not allow {action.value}")
    if (action is StepAction.AMEND) != bool(amended):
        raise ValueError("an amendment, and only an amendment, carries edits")
    if amended:
        _check_amended(state, order, amended)
    return _act(state, order, action, today, amended or {})[1]


__all__ = ["RealismStep", "human_step", "run_pipelines", "waiting_on_human"]
