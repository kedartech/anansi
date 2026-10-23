"""History's clock in autumn 1941 reads who acts, not meters (act_of, quantity_at)."""

from datetime import date, timedelta

import pytest

from revisionist.core import events as ev
from revisionist.core import history
from revisionist.core.goals import ActOf, QuantityAt, goal_met
from revisionist.core.ids import ScheduledEventId
from revisionist.core.project import project
from tests.test_realism_resolvers import Rig

START, END = date(1941, 9, 1), date(1941, 12, 8)
HUMAN_SKIPS = {
    "house_arms_merchant_ships",
    "senate_neutrality_vote",
    "house_concurrence",
    "soviet_lend_lease_finding",
    "kearny_hit",
    "reuben_james_sunk",
}
WEIGHTS = {
    "greer_incident": 2,
    "konoe_resigns": 3,
    "house_arms_merchant_ships": 2,
    "kearny_hit": 2,
    "tojo_cabinet": 3,
    "reuben_james_sunk": 2,
    "soviet_lend_lease_finding": 2,
    "senate_neutrality_vote": 3,
    "house_concurrence": 3,
    "hull_note": 3,
    "pearl_harbor": 3,
}


def walk(r: Rig) -> tuple[set[str], set[str]]:
    """Settle history day by day to 8 Dec; returns the keys fired and skipped."""
    day = START
    while day <= END:
        if day > START:
            r.add(ev.DateAdvanced(to=day))
        for event in history.settle(r.state, day):
            r.add(event)
        day += timedelta(days=1)
    by_id = {ScheduledEventId(r.ids[k]): k for k in WEIGHTS}
    fired = {by_id[s.scheduled_id] for s in r.state.scheduled.values() if s.fired}
    skipped = {by_id[s.scheduled_id] for s in r.state.scheduled.values() if s.skipped}
    return fired, skipped


def played(key: str | None) -> Rig:
    r = Rig("autumn-1941")
    if key is not None:
        r.seat(key)
        r.close_session_zero()
    return r


def test_no_human_every_event_fires() -> None:
    r = played(None)
    fired, skipped = walk(r)
    assert fired == set(WEIGHTS)
    assert not skipped
    assert r.state.divergence == 0


def test_human_roosevelt_who_files_nothing() -> None:
    r = played("roosevelt")
    fired, skipped = walk(r)
    assert skipped == HUMAN_SKIPS
    assert fired == set(WEIGHTS) - HUMAN_SKIPS
    assert r.state.divergence == sum(WEIGHTS[k] for k in HUMAN_SKIPS)


def test_human_konoe_skips_his_own_acts() -> None:
    r = played("konoe")
    fired, skipped = walk(r)
    assert skipped == {"konoe_resigns", "tojo_cabinet", "pearl_harbor"}
    assert r.state.divergence == 9
    assert "greer_incident" in fired


def test_bill_past_rayburn_makes_the_house_event_fire() -> None:
    r = played("roosevelt")
    order = r.draft(
        "roosevelt",
        "send_bill",
        "bill_through_congress",
        START,
        bill="neutrality_revision",
        urgency="urgent",
    )
    r.pass_steps(order, [date(1941, 9, 9)])
    fired, skipped = walk(r)
    assert "house_arms_merchant_ships" in fired
    assert {"senate_neutrality_vote", "house_concurrence"} <= skipped


def test_view_cannot_see_who_is_human() -> None:
    r = played("roosevelt")
    view = project(r.state, r.seats["roosevelt"])
    act = ActOf(person=r.ids["konoe"])
    assert goal_met(r.state, act)
    assert not goal_met(view, act)


@pytest.mark.parametrize(
    ("op", "value", "met"), [("ge", 1, True), ("ge", 31, False), ("le", 30, True)]
)
def test_quantity_at(op: str, value: int, met: bool) -> None:
    r = played(None)
    cond = QuantityAt(quantity=r.ids["uboats_operational"], op=op, value=value)
    assert goal_met(r.state, cond) is met


def test_quantity_at_without_a_value_is_unmet() -> None:
    r = played(None)
    cond = QuantityAt(quantity=r.ids["neutrality_revision_house_yeas"], op="ge", value=0)
    assert not goal_met(r.state, cond)
