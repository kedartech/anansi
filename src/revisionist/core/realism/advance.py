"""The event-driven advance (plan, Phase 2 unit B1).

The world does not move a fixed number of days a turn. It wakes at the earliest
thing that could need someone: a document arriving or falling due from a feed, an
order's step falling due, a history event, or the horizon of the people at the
table. The days in between pass unseen and are reported as collapsed.
"""

from datetime import date

from revisionist.core._base import Label, Model
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


def next_wake(state: State, today: date) -> Wake:
    """Where the next advance stops. Pure: same state and day, same wake."""
    raise NotImplementedError("unit B1")


__all__ = ["DEFAULT_HORIZON_DAYS", "Wake", "WakeReason", "next_wake"]
