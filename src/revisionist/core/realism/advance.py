"""The event-driven advance (plan, Phase 2 unit B1).

The world does not move a fixed number of days a turn. It wakes at the earliest
thing that could need someone: a document arriving or falling due from a feed, an
order's step falling due, a history event, or the horizon of the people at the
table. The days in between pass unseen and are reported as collapsed.
"""

from datetime import date, timedelta

from revisionist.core import history
from revisionist.core._base import Label, Model
from revisionist.core.ids import FeedId
from revisionist.core.ontology import Controller
from revisionist.core.realism import feeds
from revisionist.core.realism.pipeline import step_due
from revisionist.core.realism.pipelines import waiting_on_human
from revisionist.core.state import State

DEFAULT_HORIZON_DAYS = 7
"""The horizon when no human holds a seat [proposed]."""


class WakeReason(Model):
    """One candidate for the next wake, and what it waits on, in plain words."""

    on: date
    what: Label
    """"document", "feed", "step", "history" or "horizon"."""
    label: Label
    """For the clock line: "Rayburn's count expected ~14 Oct"."""


class Wake(Model):
    to: date
    """The in-world date the next advance runs to. Always after ``today``."""
    reason: WakeReason
    """The earliest candidate. Ties go to the first in the order above."""
    collapsed_days: int
    """Quiet days passed over: ``(to - today).days - 1``."""


CATEGORIES = ("document", "feed", "step", "history", "horizon")


def _reason(what: str, on: date, label: str, today: date) -> WakeReason:
    """A candidate, due no earlier than tomorrow: one on or before today is handled now."""
    due = on if on > today else today + timedelta(days=1)
    return WakeReason(on=due, what=what, label=label[:60])


def _received(state: State) -> set[FeedId]:
    """Feeds some seat's person has a card for. An unreceived feed is not a candidate."""
    cards = (state.cards.get(seat.embodies) for seat in state.seats.values())
    return {fid for card in cards if card for fid in card.feeds}


def _horizon(state: State) -> int:
    """The smallest horizon among the people a human holds."""
    cards = (
        state.cards.get(seat.embodies)
        for seat in state.seats.values()
        if seat.controller is Controller.HUMAN
    )
    return min((card.horizon_days for card in cards if card), default=DEFAULT_HORIZON_DAYS)


def next_wake(state: State, today: date) -> Wake:
    """Where the next advance stops. Pure: same state and day, same wake."""
    found: list[WakeReason] = []
    for doc in state.documents.values():
        view = doc.view
        if view.arrives > today:
            name = f"{view.title} arrives" if view.title else f"a {view.kind} arrives"
            found.append(_reason("document", view.arrives, name, today))
    received = _received(state)
    for feed in state.feeds.values():
        when = feeds.next_issue(state, feed) if feed.feed_id in received else None
        if when is not None:
            found.append(_reason("feed", when, f"{feed.name} expected", today))
    for order in state.orders.values():
        if waiting_on_human(state, order):
            continue
        when = step_due(order, state.pipelines[order.pipeline])
        if when is not None:
            found.append(_reason("step", when, f"{order.lever} at step {order.step + 1}", today))
    event = history.next_due(state, today + timedelta(days=1))
    if event is not None and event.on_date is not None:
        found.append(_reason("history", event.on_date, event.name, today))
    found.append(_reason("horizon", today + timedelta(days=_horizon(state)), "horizon", today))
    reason = min(found, key=lambda r: (r.on, CATEGORIES.index(r.what), r.label))
    return Wake(to=reason.on, reason=reason, collapsed_days=(reason.on - today).days - 1)


__all__ = ["DEFAULT_HORIZON_DAYS", "Wake", "WakeReason", "next_wake"]
