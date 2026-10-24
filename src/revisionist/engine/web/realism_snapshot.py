"""Builds the realism JSON shapes from one seat's ``View``.

Every builder reads only the view, with two exceptions the caller supplies: the
pipelines an order runs through (``View`` does not carry them, and a step's holder
is not otherwise known) and the next in-world date (it needs the calendar). Names
replace ids wherever a person is shown. ``snapshot.checked`` is the caller's last step.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from revisionist.core import events as ev
from revisionist.core.events import Event
from revisionist.core.ids import DocumentId, PipelineId, SeatId
from revisionist.core.project import project
from revisionist.core.realism.advance import next_wake
from revisionist.core.realism.card import Lever, ParamKind, ParamSpec
from revisionist.core.realism.document import DocumentView
from revisionist.core.realism.pipeline import Order, OrderStatus, Pipeline, StepAction, StepOutcome
from revisionist.core.state import State, View
from revisionist.engine.web.realism_api import (
    BodyLine,
    Clock,
    ContactLine,
    Desk,
    DocumentListing,
    DocumentListItem,
    DocumentPage,
    LeverChoice,
    LeverLine,
    LeverParam,
    OrderTrack,
    OrderTracker,
    StepLine,
)

_HIDDEN = frozenset({StepAction.STALL, StepAction.LEAK})
"""Outcomes the issuer never sees: an order that stalls is still with that person."""


def words(key: str) -> str:
    """A key in words, for what the world gave no label: "send_bill" -> "Send bill"."""
    spoken = key.replace("_", " ").strip()
    return spoken[:1].upper() + spoken[1:]


def lever_label(lever: Lever) -> str:
    return lever.label or words(lever.name)


def _param_label(spec: ParamSpec) -> str:
    return spec.label or words(spec.name)


def _choices(spec: ParamSpec) -> list[LeverChoice]:
    spoken = spec.choice_labels or tuple(words(c) for c in spec.choices)
    return [LeverChoice(value=v, label=t) for v, t in zip(spec.choices, spoken, strict=True)]


def _term(spec: ParamSpec, value: int | str) -> str:
    shown = str(value)
    if spec.kind is ParamKind.ENUM:
        shown = next((c["label"] for c in _choices(spec) if c["value"] == value), words(shown))
    return f"{_param_label(spec)}: {shown}"


def _item(
    view: View, doc_id: DocumentId, doc: DocumentView, unread: bool, flagged: bool
) -> DocumentListItem:
    return DocumentListItem(
        id=doc_id,
        kind=doc.kind.value,
        title=doc.title,
        author=view.entities[doc.author].name,
        issued=doc.issued.isoformat(),
        arrives=doc.arrives.isoformat(),
        channel=doc.channel.value,
        classification=doc.classification.value,
        unread=unread,
        flagged=flagged,
    )


def documents(
    view: View, read_ids: set[DocumentId], flagged_ids: set[DocumentId]
) -> DocumentListing:
    """The in-tray: arrived pages, newest arrival first, ties by document ID."""
    arrived = sorted(view.documents.items(), key=lambda kv: kv[0])
    arrived.sort(key=lambda kv: kv[1].arrives, reverse=True)
    rows = [
        _item(view, k, d, unread=k not in read_ids, flagged=k in flagged_ids) for k, d in arrived
    ]
    return DocumentListing(
        documents=rows,
        unread=sum(r["unread"] for r in rows),
        flagged=sum(r["flagged"] for r in rows),
    )


def document(view: View, doc_id: DocumentId, flagged: bool = False) -> DocumentPage:
    """One page. KeyError if the seat has not received it; fetching it counts as reading it."""
    doc = view.documents[doc_id]
    return DocumentPage(
        item=_item(view, doc_id, doc, unread=False, flagged=flagged),
        body=[BodyLine(label=b.label, value=b.value, unit=b.unit) for b in doc.body],
        prose=doc.prose,
    )


def _step_lines(order: Order, pipeline: Pipeline, view: View) -> list[StepLine]:
    last: dict[int, StepOutcome] = {}
    for taken in order.outcomes:
        last[taken.step] = taken
    indices = set(last)
    if order.status is OrderStatus.MOVING:
        indices.add(order.step)
    lines = []
    for index in sorted(indices):
        step = pipeline.steps[index]
        outcome = last.get(index)
        action = outcome.action if outcome is not None else None
        lines.append(
            StepLine(
                step=index,
                person=view.entities[step.person].name,
                kind=step.kind.value,
                action=action.value if action is not None and action not in _HIDDEN else None,
                on=outcome.on.isoformat() if outcome is not None else None,
            )
        )
    return lines


def _track(view: View, order: Order, pipeline: Pipeline) -> OrderTrack:
    moving = order.status is OrderStatus.MOVING
    holder = pipeline.steps[order.step].person if moving else None
    lever = (
        next((x for x in view.desk.levers if x.name == order.lever), None) if view.desk else None
    )
    specs = {p.name: p for p in lever.params} if lever is not None else {}
    return OrderTrack(
        id=order.order_id,
        lever=order.lever,
        label=lever_label(lever) if lever is not None else words(order.lever),
        terms=[
            _term(specs[k], v) if k in specs else f"{words(k)}: {v}"
            for k, v in order.params.items()
        ],
        pipeline=pipeline.name,
        params=dict(order.params),
        status=order.status.value,
        filed=order.filed.isoformat(),
        at_step=order.step,
        with_person=view.entities[holder].name if holder is not None else None,
        since=(order.outcomes[-1].on if order.outcomes else order.filed).isoformat(),
        steps=_step_lines(order, pipeline, view),
        decision_needed=holder is not None and holder == view.me.embodies,
    )


def orders(view: View, pipelines: Mapping[PipelineId, Pipeline]) -> OrderTracker:
    """The viewer's own orders, newest first, each trimmed to what reached the issuer."""
    own = sorted(view.orders.values(), key=lambda o: o.order_id)
    own.sort(key=lambda o: o.filed, reverse=True)
    return OrderTracker(orders=[_track(view, o, pipelines[o.pipeline]) for o in own])


def clock(
    view: View,
    next_wake_at: str | None,
    waiting_on: str | None,
    days_collapsed: int,
    next_world_date: str | None = None,
) -> Clock:
    """Clock and pace. The wake time and next date come from the caller (schedule and calendar)."""
    if view.in_world_date is None:
        raise ValueError("a realism campaign has an in-world date")
    return Clock(
        world_date=view.in_world_date.isoformat(),
        next_wake_at=next_wake_at,
        next_world_date=next_world_date,
        waiting_on=waiting_on,
        days_collapsed=days_collapsed,
    )


def desk(view: View) -> Desk:
    """The viewer's own card, without its AI policy fields (they are not in a view)."""
    card = view.desk
    if card is None:
        raise ValueError(f"no card for the person {view.me.embodies}")
    return Desk(
        office=card.office,
        powers=list(card.powers),
        levers=[
            LeverLine(
                name=lever.name,
                label=lever_label(lever),
                summary=lever.summary,
                pipeline=lever.pipeline,
                params=[
                    LeverParam(
                        name=p.name,
                        label=_param_label(p),
                        kind=p.kind.value,
                        lo=p.lo,
                        hi=p.hi,
                        choices=_choices(p),
                    )
                    for p in lever.params
                ],
            )
            for lever in card.levers
        ],
        contacts=[
            ContactLine(person=view.entities[c.person].name, channel=c.channel.value)
            for c in card.contacts
        ],
        principals=[view.entities[p].name for p in card.principals],
        constraints=list(card.constraints),
        personal=list(card.personal),
        horizon_days=card.horizon_days,
    )


def read_ids(events: Iterable[Event], seat: SeatId) -> set[DocumentId]:
    return {
        e.payload.doc_id
        for e in events
        if isinstance(e.payload, ev.DocumentRead) and e.payload.seat_id == seat
    }


def flagged_ids(events: Iterable[Event], seat: SeatId) -> set[DocumentId]:
    """The documents ``seat`` has flagged: its latest ``document_flagged`` for each wins."""
    standing: dict[DocumentId, bool] = {}
    for e in events:
        if isinstance(e.payload, ev.DocumentFlagged) and e.payload.seat_id == seat:
            standing[e.payload.doc_id] = e.payload.flagged
    return {k for k, v in standing.items() if v}


def seat_realism(
    state: State,
    seat: SeatId,
    read: set[DocumentId],
    flagged: set[DocumentId],
    next_wake_at: str | None,
) -> dict[str, Any]:
    """The ``view.realism`` block of ``/api/me``: in-tray, desk, orders and clock."""
    view = project(state, seat)
    today = view.in_world_date
    if today is None:
        raise ValueError("a realism campaign has an in-world date")
    wake = next_wake(state, today)
    return {
        "documents": documents(view, read, flagged),
        "desk": desk(view),
        "orders": orders(view, state.pipelines),
        "clock": clock(view, next_wake_at, wake.reason.label, 0, wake.to.isoformat()),
    }


__all__ = [
    "clock",
    "desk",
    "document",
    "documents",
    "flagged_ids",
    "lever_label",
    "orders",
    "read_ids",
    "seat_realism",
    "words",
]
