"""The calendar that stretches and shrinks (spec §5.7). Phase 2 unit.

The table picks the in-world end date and the real-life length; the calendar
sets the base in-world days per tick, stretches it in quiet spells and shrinks
it near a contest's deciding point or a due history event.
"""

from datetime import date

from revisionist.core.settings import Settings
from revisionist.core.state import State


def base_days(start: date, end: date, ticks: int) -> int:
    """In-world days per tick that reach ``end`` from ``start`` in ``ticks`` ticks."""
    raise NotImplementedError("Session Zero v3, Phase 2: calendar")


def total_ticks(settings: Settings) -> int | None:
    """Ticks in the campaign: ``real_days`` of ``tick_hours`` turns, rounded up.
    None when the table set no end date or no real-life length."""
    raise NotImplementedError("Session Zero v3, Phase 2: calendar")


def span(state: State) -> int:
    """In-world days the coming tick covers. Falls back to
    ``settings.world_days_per_tick`` when the campaign has no calendar."""
    raise NotImplementedError("Session Zero v3, Phase 2: calendar")


def next_date(state: State) -> date | None:
    """Today's in-world date after the coming tick; None without a calendar."""
    raise NotImplementedError("Session Zero v3, Phase 2: calendar")
