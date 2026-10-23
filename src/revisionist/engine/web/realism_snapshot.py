"""Builds the realism JSON shapes from one seat's ``View``.

Every builder reads only the view, with two exceptions the caller supplies: the
pipelines an order runs through (``View`` does not carry them, and a step's holder
is not otherwise known) and the next in-world date (it needs the calendar). Names
replace ids wherever a person is shown. ``snapshot.checked`` is the caller's last step.
"""

from collections.abc import Mapping

from revisionist.core.ids import DocumentId, PipelineId
from revisionist.core.realism.document import DocumentView
from revisionist.core.realism.pipeline import Order, OrderStatus, Pipeline, StepAction, StepOutcome
from revisionist.core.state import View
from revisionist.engine.web.realism_api import (
    BodyLine,
    Clock,
    ContactLine,
    Desk,
    DocumentListing,
    DocumentListItem,
    DocumentPage,
    LeverLine,
    LeverParam,
    OrderTrack,
    OrderTracker,
    StepLine,
)

_HIDDEN = frozenset({StepAction.STALL, StepAction.LEAK})
"""Outcomes the issuer never sees: an order that stalls is still with that person."""


def _item(view: View, doc_id: DocumentId, doc: DocumentView, unread: bool) -> DocumentListItem:
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
    )


def documents(view: View, read_ids: set[DocumentId]) -> DocumentListing:
    """The in-tray: arrived pages, newest arrival first, ties by document ID."""
    arrived = sorted(view.documents.items(), key=lambda kv: kv[0])
    arrived.sort(key=lambda kv: kv[1].arrives, reverse=True)
    rows = [_item(view, k, d, unread=k not in read_ids) for k, d in arrived]
    return DocumentListing(documents=rows, unread=sum(r["unread"] for r in rows))


def document(view: View, doc_id: DocumentId) -> DocumentPage:
    """One page. KeyError if the seat has not received it; fetching it counts as reading it."""
    doc = view.documents[doc_id]
    return DocumentPage(
        item=_item(view, doc_id, doc, unread=False),
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
    return OrderTrack(
        id=order.order_id,
        lever=order.lever,
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
                pipeline=lever.pipeline,
                params=[
                    LeverParam(
                        name=p.name,
                        kind=p.kind.value,
                        lo=p.lo,
                        hi=p.hi,
                        choices=list(p.choices),
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


__all__ = ["clock", "desk", "document", "documents", "orders"]
