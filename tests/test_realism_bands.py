"""The Phase 0c historical run: the first gate of the realism build.

Ten campaign ids play autumn 1941 with the scripted orders of the record. The base
rates in the resolvers are [fitted] to this record, so a pass shows the machinery
(urgency, opinion, friction) keeps outcomes in range, not that it predicts them.
Prediction is Phase 6. Pass rule (BANDS.md): at least 80% of assertions in band,
none outside twice its band, votes 1-3 pass in every seed.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from revisionist.core import events as ev
from revisionist.core.ids import CampaignId, ScheduledEventId, derived_id
from revisionist.core.realism.pipeline import Order, StepAction, StepOutcome
from revisionist.core.realism.resolvers import ON_DAY, ON_ORDER, resolver_for, settled_on, value
from tests.test_realism_resolvers import Rig

SEEDS = [
    CampaignId(derived_id(CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FAV"), "seed", n)) for n in range(10)
]
START, END = date(1941, 9, 1), date(1941, 12, 1)
FIRED = {
    date(1941, 9, 4): "greer_incident",
    date(1941, 10, 17): "kearny_hit",
    date(1941, 10, 31): "reuben_james_sunk",
}


@dataclass
class Script:
    person: str
    lever: str
    pipeline: str
    filed: date
    steps: list[date]
    settled: date
    params: dict[str, object]


SCRIPT = [
    Script("roosevelt", "public_address", "public_address", date(1941, 9, 9),
           [date(1941, 9, 10)], date(1941, 9, 10), {"theme": "freedom_of_the_seas"}),
    Script("roosevelt", "naval_order", "naval_order", date(1941, 9, 11),
           [date(1941, 9, 13), date(1941, 9, 15)], date(1941, 9, 15),
           {"order": "shoot_on_sight", "zone": "western_atlantic"}),
    Script("roosevelt", "send_bill", "bill_through_congress", date(1941, 10, 9),
           [date(1941, 10, 17), date(1941, 10, 18), date(1941, 11, 7), date(1941, 11, 17)],
           date(1941, 11, 17), {"bill": "neutrality_revision", "urgency": "urgent"}),
    Script("roosevelt", "lend_lease_finding", "lend_lease_allocation", date(1941, 10, 30),
           [date(1941, 11, 1), date(1941, 11, 3), date(1941, 11, 5)], date(1941, 11, 5),
           {"recipient": "ussr", "share_pct": 10}),
]  # fmt: skip


@dataclass
class Run:
    house: tuple[int, int]
    senate: tuple[int, int]
    concur: tuple[int, int]
    passed: int
    losses: tuple[int, int, int]
    finding_days: int
    eligible: int
    about_24sep: int


def play(seed: CampaignId) -> Run:
    """One campaign from 1 Sep to 1 Dec; the figures the bands read."""
    r = Rig("autumn-1941", seed)
    r.seat("roosevelt")
    r.close_session_zero()
    orders: dict[int, Order] = {}
    about: dict[date, int | None] = {}
    day = START
    while day <= END:
        if day > START:
            r.add(ev.DateAdvanced(to=day))
        settled: list[Order] = []
        for n, s in enumerate(SCRIPT):
            if s.filed == day:
                orders[n] = r.draft(s.person, s.lever, s.pipeline, s.filed, **s.params)
            if n in orders and day in s.steps:
                k = s.steps.index(day)
                done = r.state.orders[orders[n].order_id]
                step = r.state.pipelines[done.pipeline].steps[k]
                r.add(
                    ev.OrderStepTaken(
                        order_id=done.order_id,
                        outcome=StepOutcome(
                            step=k, person=step.person, action=StepAction.PASS, on=day
                        ),
                    )
                )
            if n in orders and s.settled == day:
                settled.append(r.settle(orders[n]))
        if day in FIRED:
            r.add(ev.ScheduledEventFired(scheduled_id=ScheduledEventId(r.ids[FIRED[day]])))
        for order in settled:
            key = resolver_for(r.state, order)
            r.fold_changes(ON_ORDER[key](r.state, order))
        for fn in ON_DAY.values():
            r.fold_changes(fn(r.state, day))
        about[day] = value(r.state, "gallup_fdr_about_right")
        day += timedelta(days=1)

    def v(k: str) -> int:
        got = value(r.state, k)
        assert got is not None, k
        return got

    return Run(
        house=(v("neutrality_revision_house_yeas"), v("neutrality_revision_house_nays")),
        senate=(v("neutrality_revision_senate_yeas"), v("neutrality_revision_senate_nays")),
        concur=(v("neutrality_revision_concur_yeas"), v("neutrality_revision_concur_nays")),
        passed=v("neutrality_revision_passed"),
        losses=(v("uboat_losses_sep"), v("uboat_losses_oct"), v("uboat_losses_nov")),
        finding_days=(settled_on(r.state.orders[orders[3].order_id]) - SCRIPT[3].filed).days,
        eligible=v("soviet_lend_lease_eligible"),
        about_24sep=v_on(about, date(1941, 9, 24)),
    )


def v_on(series: dict[date, int | None], day: date) -> int:
    got = series[day]
    assert got is not None
    return got


@dataclass
class Check:
    seed: int
    band: str
    got: float
    lo: float
    hi: float

    @property
    def inside(self) -> bool:
        return self.lo <= self.got <= self.hi

    @property
    def twice(self) -> bool:
        mid, half = (self.lo + self.hi) / 2, (self.hi - self.lo) / 2
        return abs(self.got - mid) <= 2 * half


def margin(t: tuple[int, int]) -> int:
    return t[0] - t[1]


def checks_for(seed: int, f: Run) -> list[Check]:
    total = sum(f.losses)
    return [
        Check(seed, "1 House margin 121+-20", margin(f.house), 101, 141),
        Check(seed, "2 Senate margin 13+-6", margin(f.senate), 7, 19),
        Check(seed, "3 concurrence margin 18+-10", margin(f.concur), 8, 28),
        Check(seed, "4 losses 507k+-25%", total, 507_000 * 0.75, 507_000 * 1.25),
        Check(seed, "4 Oct above Nov", f.losses[1] - f.losses[2], 1, 10**9),
        Check(seed, "5 Soviet finding within 21 days", f.finding_days, 0, 21),
        Check(seed, "6 about right 24 Sep 57+-7", f.about_24sep, 50, 64),
    ]


def table(results: list[Run], checks: list[Check]) -> str:
    rows = ["seed  house      senate    concur      losses(Sep,Oct,Nov)      find  24Sep  passed"]
    for i, f in enumerate(results):
        rows.append(
            f"{i:>4}  {f.house!s:<10} {f.senate!s:<9} {f.concur!s:<11} "
            f"{f.losses!s:<24} {f.finding_days:>4}  {f.about_24sep:>5}  {f.passed}"
        )
    bad = [f"  seed {c.seed} {c.band}: {c.got}" for c in checks if not c.inside]
    return "\n".join([*rows, "out of band:", *(bad or ["  none"])])


def test_the_historical_run_stays_in_its_bands() -> None:
    results = [play(s) for s in SEEDS]
    checks = [c for i, f in enumerate(results) for c in checks_for(i, f)]
    report = table(results, checks)
    print(report)
    for f in results:
        assert f.house[0] > f.house[1] and f.senate[0] > f.senate[1], report
        assert f.concur[0] > f.concur[1], report
        assert f.passed == 1 and f.eligible == 1, report
    assert all(c.twice for c in checks), report
    share = sum(c.inside for c in checks) / len(checks)
    assert share >= 0.8, f"{share:.0%} in band\n{report}"
