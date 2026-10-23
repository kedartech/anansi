"""The JSON a realism campaign's web client reads (plan, Phase 1 snapshot shape).

Types only; nothing serves them yet (unit A3 does). Every shape is built from
the seat's own ``View``, so it inherits the projection's rules, and every
response passes ``snapshot.checked`` before it is sent. Further rules the
builder must keep:

- No truth. A document is its ``DocumentView``: no ``truth_refs``, no bias.
  A quantity appears only if its own scope admits the seat.
- No odds. Realism has no feasibility bands to show.
- No AI policy. A desk is the card without voice, loyalty, doctrine or interests.
- Dates are ISO ``YYYY-MM-DD`` in-world dates, never wall-clock times, except
  ``next_wake_at``.
"""

from typing import Literal, TypedDict


class DocumentListItem(TypedDict):
    """One row of the in-tray. No body: the list is sent without bodies."""

    id: str
    kind: str
    """``DocumentKind`` value."""
    title: str
    author: str
    """The author's name, as the seat knows it."""
    issued: str
    arrives: str
    channel: str
    """``ChannelKind`` value."""
    classification: str
    """``Classification`` value."""
    unread: bool
    """True until the seat logs ``document_read`` for it."""


class DocumentListing(TypedDict):
    documents: list[DocumentListItem]
    """Arrived documents in the seat's scope, newest arrival first."""
    unread: int


class BodyLine(TypedDict):
    label: str
    value: int | str | None
    """None: the author is silent on it."""
    unit: str


class DocumentPage(TypedDict):
    """A single document, fetched on its own."""

    item: DocumentListItem
    body: list[BodyLine]
    prose: str | None


class StepLine(TypedDict):
    """One step of an order as it reached the issuer."""

    step: int
    person: str
    kind: Literal["decision", "clerical"]
    action: str | None
    """``StepAction`` value once acted on; None while the order waits there."""
    on: str | None


class OrderTrack(TypedDict):
    """The pipeline tracker for one of the seat's own orders."""

    id: str
    lever: str
    pipeline: str
    params: dict[str, int | str]
    status: str
    """``OrderStatus`` value."""
    filed: str
    at_step: int
    with_person: str | None
    """Who holds it now ("with Rayburn since 12 Oct"); None once settled."""
    since: str | None
    steps: list[StepLine]
    decision_needed: bool
    """True when the step it waits at is held by this seat."""


class OrderTracker(TypedDict):
    orders: list[OrderTrack]
    """The seat's own orders only, newest first."""


class Clock(TypedDict):
    """Clock and pace: replaces "N days a turn", which an event-driven clock makes false."""

    world_date: str
    next_wake_at: str | None
    """Wall-clock ISO time of the next advance; None while paused."""
    next_world_date: str | None
    """The in-world date the next advance runs to."""
    waiting_on: str | None
    """What the next advance waits for ("Rayburn's count expected ~14 Oct")."""
    days_collapsed: int
    """Quiet in-world days the last advance passed over ("9 quiet days passed")."""


class ContactLine(TypedDict):
    person: str
    channel: str


class LeverParam(TypedDict):
    name: str
    kind: Literal["int", "enum", "entity", "text"]
    lo: int | None
    hi: int | None
    choices: list[str]


class LeverLine(TypedDict):
    name: str
    pipeline: str
    params: list[LeverParam]
    """The draft form is built from these."""


class Desk(TypedDict):
    """The seat's own card, without its AI policy fields."""

    office: str
    powers: list[str]
    levers: list[LeverLine]
    contacts: list[ContactLine]
    principals: list[str]
    constraints: list[str]
    personal: list[str]
    horizon_days: int


__all__ = [
    "BodyLine",
    "Clock",
    "ContactLine",
    "Desk",
    "DocumentListItem",
    "DocumentListing",
    "DocumentPage",
    "LeverLine",
    "LeverParam",
    "OrderTrack",
    "OrderTracker",
    "StepLine",
]
