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
    flagged: bool
    """True while the seat's latest ``document_flagged`` for it says so."""


class DocumentListing(TypedDict):
    documents: list[DocumentListItem]
    """Arrived documents in the seat's scope, newest arrival first."""
    unread: int
    flagged: int


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
    label: str
    """The lever's label on the viewer's card, in words."""
    terms: list[str]
    """Each parameter as "Which bill: Revise the Neutrality Act"."""
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
    title: str | None
    """The office on the person's public card face, if they have a card."""
    channel: str
    channels: list[str]
    """How a directive can reach them, their own channel first."""


class LeverChoice(TypedDict):
    value: str
    """What is posted back."""
    label: str


class LeverParam(TypedDict):
    name: str
    label: str
    kind: Literal["int", "enum", "entity", "text"]
    lo: int | None
    hi: int | None
    choices: list[LeverChoice]


class LeverLine(TypedDict):
    name: str
    label: str
    summary: str
    pipeline: str
    params: list[LeverParam]
    """The draft form is built from these."""


class DirectiveProposal(TypedDict):
    """The order the aide read a directive as, for the player to sign or revise."""

    to: str
    channel: str
    text: str
    lever: str | None
    """Posted back on signing; None when it cannot be done."""
    label: str | None
    terms: list[str]
    params: dict[str, int | str]
    reason: str | None
    """Why it cannot be done, in words."""


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


class SituationReading(TypedDict):
    """One report of a figure by one source."""

    value: int | None
    """None: that report was silent on it."""
    issued: str
    arrives: str


class SituationSource(TypedDict):
    """What one person has reported of a figure, latest report first in the fields."""

    author: str
    doc_id: str
    """The latest report, to open it from the board."""
    value: int | None
    """The latest report's figure; None is a gap, never an estimate."""
    issued: str
    arrives: str
    last_given: str | None
    """When a figure was last given (issue date); None if never."""
    trend: Literal["up", "down", "level"] | None
    """The latest figure against the one given before it; None for a gap or a first report."""
    history: list[SituationReading]
    """This source's latest reports of it (at most six), oldest arrival first."""


class SituationFigure(TypedDict):
    """One headline figure, as each source that reports it has it."""

    label: str
    unit: str
    sources: list[SituationSource]
    """By author name. Never averaged: two sources that differ are both shown."""
    disagree: bool
    """True when the sources' latest figures differ."""


class Situation(TypedDict):
    """The situation board: figures from arrived pages only, never from the record."""

    figures: list[SituationFigure]


class MapPlace(TypedDict):
    name: str
    lat: float
    lon: float


class MapMark(TypedDict):
    """One force, front or route as the seat's latest page about it places it."""

    name: str
    kind: Literal["fleet", "army", "air", "front", "route"]
    side: Literal["own", "other", "none"]
    """Own: the seat's own polity or body. None: a front, which is both sides'."""
    side_name: str
    certainty: Literal["known", "reported", "stale"]
    """Known: the seat's own side, freshly reported. Stale: the page is older than
    ``STALE_DAYS``. A feature no page has placed is not on the map at all."""
    places: list[MapPlace]
    reported: str
    """The page's issue date: where it was then, not now."""
    source: str
    """The author's name."""
    document: str
    """The page's id, to open it from the map."""


class SeatMap(TypedDict):
    """The map: places are public; marks come from arrived pages only, never the record."""

    places: list[MapPlace]
    marks: list[MapMark]


__all__ = [
    "BodyLine",
    "Clock",
    "ContactLine",
    "Desk",
    "DirectiveProposal",
    "DocumentListItem",
    "DocumentListing",
    "DocumentPage",
    "LeverChoice",
    "LeverLine",
    "LeverParam",
    "MapMark",
    "MapPlace",
    "OrderTrack",
    "OrderTracker",
    "SeatMap",
    "Situation",
    "SituationFigure",
    "SituationReading",
    "SituationSource",
    "StepLine",
]
