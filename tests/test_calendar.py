"""The calendar that stretches and shrinks (spec §5.7)."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from revisionist.core import calendar
from revisionist.core.ids import ContestId, ScheduledEventId
from revisionist.core.ontology import (
    Contest,
    ContestKind,
    ScheduledEvent,
    ScheduleSource,
)
from revisionist.core.settings import Settings
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC
from tests import fixtures as fx

START = date(2026, 1, 1)
CONTEST = ContestId("01ARZ3NDEKTSV4RRFFQ69G5KW0")
SECOND = ContestId("01ARZ3NDEKTSV4RRFFQ69G5KW1")
EVENT = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HE0")


def _contest(cid: ContestId, countdown: int, *, decided: bool = False) -> Contest:
    return Contest(
        contest_id=cid,
        kind=ContestKind.RIVALRY,
        name="The road",
        prize="The valley",
        sides=(fx.E_NORTH, fx.E_SOUTH),
        track=0,
        momentum=0,
        countdown_days=countdown,
        decided=decided,
        scope=PUBLIC,
    )


def _history(on: date) -> ScheduledEvent:
    return ScheduledEvent(
        scheduled_id=EVENT,
        fire_at_tick=0,
        name="The treaty is signed",
        scope=PUBLIC,
        source=ScheduleSource.HISTORY,
        on_date=on,
        weight=2,
    )


def _state(
    *,
    tick: int = 0,
    today: date | None = START,
    end: date | None = START + timedelta(days=100),
    real_days: int | None = 10,
    tick_hours: int = 24,
    contests: tuple[Contest, ...] = (),
) -> State:
    settings = Settings(
        end_date=end, real_days=real_days, tick_hours=tick_hours, world_days_per_tick=9
    )
    return State(
        campaign_id=fx.CAMPAIGN,
        tick=tick,
        settings=settings,
        in_world_date=today,
        contests={c.contest_id: c for c in contests},
    )


@pytest.fixture
def no_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendar, "next_due", lambda world, today: None)


def test_base_days_rounds_up_to_reach_the_end() -> None:
    assert calendar.base_days(START, START + timedelta(days=10), 5) == 2
    assert calendar.base_days(START, START + timedelta(days=10), 3) == 4


def test_base_days_is_at_least_one_day() -> None:
    assert calendar.base_days(START, START + timedelta(days=1), 50) == 1


@pytest.mark.parametrize("ticks", [0, -3])
def test_base_days_rejects_no_ticks(ticks: int) -> None:
    with pytest.raises(ValueError):
        calendar.base_days(START, START + timedelta(days=5), ticks)


@pytest.mark.parametrize("gap", [0, -1])
def test_base_days_rejects_an_end_not_after_the_start(gap: int) -> None:
    with pytest.raises(ValueError):
        calendar.base_days(START, START + timedelta(days=gap), 5)


def test_total_ticks_needs_both_an_end_and_a_real_length() -> None:
    end = START + timedelta(days=30)
    assert calendar.total_ticks(Settings(end_date=end)) is None
    assert calendar.total_ticks(Settings(real_days=5)) is None
    assert calendar.total_ticks(Settings()) is None


@pytest.mark.parametrize(
    ("real_days", "tick_hours", "expected"),
    [(3, 24, 3), (3, 12, 6), (1, 7, 4), (1, 48, 1)],
)
def test_total_ticks_rounds_up_and_never_drops_below_one(
    real_days: int, tick_hours: int, expected: int
) -> None:
    settings = Settings(
        end_date=START + timedelta(days=30), real_days=real_days, tick_hours=tick_hours
    )
    assert calendar.total_ticks(settings) == expected


def test_span_falls_back_without_a_calendar(no_history: None) -> None:
    assert calendar.span(_state(today=None)) == 9
    assert calendar.span(_state(end=None)) == 9
    assert calendar.span(_state(real_days=None)) == 9


def test_span_on_the_last_tick_lands_on_the_end(no_history: None) -> None:
    state = _state(tick=9, real_days=10, today=START + timedelta(days=87))
    assert calendar.span(state) == 13
    overdue = _state(tick=12, real_days=10, today=START + timedelta(days=87))
    assert calendar.span(overdue) == 13


def test_span_is_zero_once_the_date_has_reached_the_end(no_history: None) -> None:
    state = _state(tick=4, today=START + timedelta(days=100))
    assert calendar.span(state) == 0


def test_span_stretches_in_a_quiet_spell(no_history: None) -> None:
    # 100 days over 10 ticks is 10 a tick; a quiet spell adds half again.
    assert calendar.span(_state()) == 15


def test_span_stays_at_base_when_the_next_point_is_mid_range(no_history: None) -> None:
    assert calendar.span(_state(contests=(_contest(CONTEST, 45),))) == 10


def test_span_shrinks_near_a_contest_deciding_day(no_history: None) -> None:
    assert calendar.span(_state(contests=(_contest(CONTEST, 25),))) == 5


def test_span_shrink_never_drops_below_one_day(no_history: None) -> None:
    state = _state(real_days=10, end=START + timedelta(days=5), contests=(_contest(CONTEST, 0),))
    assert calendar.span(state) == 1


def test_span_ignores_a_decided_contest(no_history: None) -> None:
    state = _state(contests=(_contest(CONTEST, 0, decided=True),))
    assert calendar.span(state) == 15


def test_span_shrinks_near_a_due_history_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        calendar, "next_due", lambda world, today: _history(today + timedelta(days=5))
    )
    assert calendar.span(_state()) == 5


def test_span_uses_the_nearest_point_of_either_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    far = _history(START + timedelta(days=70))
    monkeypatch.setattr(calendar, "next_due", lambda world, today: far)
    state = _state(contests=(_contest(CONTEST, 25), _contest(SECOND, 90)))
    assert calendar.span(state) == 5


def test_span_stays_at_base_for_a_history_event_mid_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        calendar, "next_due", lambda world, today: _history(today + timedelta(days=40))
    )
    assert calendar.span(_state()) == 10


def test_span_never_exceeds_the_days_left(no_history: None) -> None:
    # Two ticks over three days: base 2, stretched to 3, which is all there is.
    state = _state(tick=0, real_days=2, end=START + timedelta(days=3))
    assert calendar.span(state) == 3
    short = _state(tick=0, real_days=10, end=START + timedelta(days=1))
    assert calendar.span(short) == 1


def test_next_date_is_none_without_a_calendar(no_history: None) -> None:
    assert calendar.next_date(_state(today=None)) is None
    assert calendar.next_date(_state(real_days=None)) is None


def test_next_date_adds_the_span(no_history: None) -> None:
    assert calendar.next_date(_state()) == START + timedelta(days=15)


def test_next_date_on_the_last_tick_is_the_end(no_history: None) -> None:
    end = START + timedelta(days=100)
    state = _state(tick=9, today=end - timedelta(days=13))
    assert calendar.next_date(state) == end


@given(
    gap=st.integers(min_value=1, max_value=2000),
    real_days=st.integers(min_value=1, max_value=60),
    tick_hours=st.sampled_from([6, 12, 24, 48]),
    countdowns=st.lists(st.integers(min_value=0, max_value=3000), max_size=3),
)
def test_simulated_campaign_lands_on_the_end_at_the_last_tick(
    gap: int, real_days: int, tick_hours: int, countdowns: list[int]
) -> None:
    end = START + timedelta(days=gap)
    settings = Settings(end_date=end, real_days=real_days, tick_hours=tick_hours)
    total = calendar.total_ticks(settings)
    assert total is not None
    ids = [ContestId(f"01ARZ3NDEKTSV4RRFFQ69G5K{i:02d}") for i in range(len(countdowns))]
    days = dict(zip(ids, countdowns, strict=True))
    today = START
    with patch.object(calendar, "next_due", lambda world, day: None):
        for tick in range(total):
            contests = {cid: _contest(cid, days[cid]) for cid in ids}
            state = State(
                campaign_id=fx.CAMPAIGN,
                tick=tick,
                settings=settings,
                in_world_date=today,
                contests=contests,
            )
            step = calendar.span(state)
            assert step >= 0
            assert calendar.next_date(state) == today + timedelta(days=step)
            today += timedelta(days=step)
            assert today <= end
            for cid in ids:
                days[cid] = max(0, days[cid] - step)
    assert today == end
