"""The calendar that stretches and shrinks (spec §5.7). Phase 2 unit.

The table picks the in-world end date and the real-life length; the calendar
sets the base in-world days per tick, stretches it in quiet spells and shrinks
it near a contest's deciding point or a due history event.
"""

from datetime import date, timedelta

from revisionist.core.history import next_due
from revisionist.core.settings import Settings
from revisionist.core.state import State


def base_days(start: date, end: date, ticks: int) -> int:
    """In-world days per tick that reach ``end`` from ``start`` in ``ticks`` ticks."""
    if ticks < 1 or end <= start:
        raise ValueError("a calendar needs at least one tick and an end after the start")
    return max(1, -(-(end - start).days // ticks))


def total_ticks(settings: Settings) -> int | None:
    """Ticks in the campaign: ``real_days`` of ``tick_hours`` turns, rounded up.
    None when the table set no end date or no real-life length."""
    if settings.end_date is None or settings.real_days is None:
        return None
    return max(1, -(-settings.real_days * 24 // settings.tick_hours))


def span(state: State) -> int:
    """In-world days the coming tick covers. Falls back to
    ``settings.world_days_per_tick`` when the campaign has no calendar."""
    calendar = _calendar(state)
    if calendar is None:
        return state.settings.world_days_per_tick
    today, end, ticks = calendar
    remaining = (end - today).days
    if remaining <= 0:
        return 0
    left = max(1, ticks - state.tick)
    if left == 1:
        return remaining
    base = -(-remaining // left)
    nearest = _nearest_deciding_day(state, today)
    if nearest is not None and nearest <= 3 * base:
        return max(1, base // 2)
    if nearest is None or nearest > 6 * base:
        return min(remaining, base + base // 2)
    return base


def next_date(state: State) -> date | None:
    """Today's in-world date after the coming tick; None without a calendar."""
    calendar = _calendar(state)
    if calendar is None:
        return None
    today, _, _ = calendar
    return today + timedelta(days=span(state))


def _calendar(state: State) -> tuple[date, date, int] | None:
    """Today, the end date and the tick count, when the campaign has a calendar."""
    end = state.settings.end_date
    ticks = total_ticks(state.settings)
    if state.in_world_date is None or end is None or ticks is None:
        return None
    return state.in_world_date, end, ticks


def _nearest_deciding_day(state: State, today: date) -> int | None:
    """Days from today to the nearest undecided contest or due history event."""
    days = [c.countdown_days for c in state.contests.values() if not c.decided]
    due = next_due(state, today)
    if due is not None and due.on_date is not None:
        days.append((due.on_date - today).days)
    return min(days, default=None)
