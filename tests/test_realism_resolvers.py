"""The four quantity resolvers (unit B3): table tests against the sourced record."""

from datetime import date, timedelta

import pytest

from revisionist.core import events as ev
from revisionist.core.fold import apply, replay
from revisionist.core.ids import (
    CampaignId,
    EntityId,
    EventId,
    OrderId,
    PipelineId,
    SeatId,
    derived_id,
)
from revisionist.core.realism.pipeline import (
    Order,
    OrderStatus,
    StepAction,
    StepOutcome,
)
from revisionist.core.realism.quantity import QuantityChange
from revisionist.core.realism.resolvers import (
    ON_DAY,
    ON_ORDER,
    apply_changes,
    opinion,
    production,
    qid,
    resolver_for,
    shipping,
    value,
    votes,
)
from revisionist.core.visibility import PUBLIC
from revisionist.core.world_compile import compile_world, resolve_keys
from revisionist.engine import campaign as cmp

CAMPAIGN = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FAV")
START = date(1941, 9, 1)


class Rig:
    """A world compiled, one person seated, and a state folded event by event."""

    def __init__(self, world_name: str = "autumn-1941", campaign: CampaignId = CAMPAIGN) -> None:
        self.campaign = campaign
        world = cmp.load_world(cmp.world_path(world_name))
        self.ids = resolve_keys(world, campaign)
        events = compile_world(world, campaign, cmp.versions())
        self.state = replay(events)
        self.seq = len(events)
        self.seats: dict[str, SeatId] = {}
        self.n_orders = 0

    def _fold(self, payload: ev.Payload) -> None:
        event = ev.Event(
            event_id=EventId(derived_id(self.campaign, "test-event", self.seq)),
            campaign_id=self.campaign,
            seq=self.seq,
            tick=0,
            scope=PUBLIC,
            payload=payload,
            versions=cmp.versions(),
        )
        self.seq += 1
        self.state = apply(self.state, event)

    def add(self, payload: ev.Payload) -> None:
        self._fold(payload)

    def seat(self, key: str) -> SeatId:
        seat = SeatId(derived_id(self.campaign, "seat", key))
        self.add(ev.CharacterClaimed(seat_id=seat, entity_id=EntityId(self.ids[key])))
        self.seats[key] = seat
        return seat

    def close_session_zero(self) -> None:
        self.add(ev.SessionZeroClosed())

    def draft(self, person: str, lever: str, pipeline: str, filed: date, **params: object) -> Order:
        self.n_orders += 1
        order = Order(
            order_id=OrderId(derived_id(self.campaign, "order", self.n_orders)),
            issuer=self.seats[person],
            lever=lever,
            pipeline=PipelineId(self.ids[pipeline]),
            params=params,
            filed=filed,
        )
        self.add(ev.OrderDrafted(order=order))
        return order

    def pass_steps(self, order: Order, days: list[date]) -> None:
        for n, day in enumerate(days):
            step = self.state.pipelines[order.pipeline].steps[n]
            self.add(
                ev.OrderStepTaken(
                    order_id=order.order_id,
                    outcome=StepOutcome(step=n, person=step.person, action=StepAction.PASS, on=day),
                )
            )

    def settle(self, order: Order, status: OrderStatus = OrderStatus.ENACTED) -> Order:
        self.add(ev.OrderSettled(order_id=order.order_id, status=status))
        return self.state.orders[order.order_id]

    def fold_changes(self, changes: tuple[QuantityChange, ...]) -> None:
        for event in apply_changes(self.state, changes):
            self.add(event)

    def set_value(self, key: str, v: int, as_of: date = START) -> None:
        self.fold_changes((QuantityChange(quantity_id=qid(self.state, key), value=v, as_of=as_of),))

    def enact(
        self, person: str, lever: str, pipeline: str, filed: date, days: list[date], **p: object
    ) -> Order:
        order = self.draft(person, lever, pipeline, filed, **p)
        self.pass_steps(order, days)
        return self.settle(order)


def rig(world: str = "autumn-1941", seat: str = "roosevelt") -> Rig:
    r = Rig(world)
    r.seat(seat)
    r.close_session_zero()
    return r


def vals(r: Rig, *keys: str) -> list[int | None]:
    return [value(r.state, k) for k in keys]


def enact_bill(r: Rig, bill: str, urgency: str = "ordinary", seat: str = "roosevelt") -> Order:
    d = date(1941, 10, 9)
    return r.enact(
        seat,
        "send_bill",
        "bill_through_congress",
        d,
        [d + timedelta(days=n) for n in (8, 9, 29, 39)],
        bill=bill,
        urgency=urgency,
    )


# --- votes ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bill", "chamber", "record"),
    [
        ("neutrality_revision", "house", (259, 138)),
        ("neutrality_revision", "senate", (50, 37)),
        ("neutrality_revision", "concur", (212, 194)),
        ("draft_extension", "house", (203, 202)),
        ("draft_extension", "senate", (45, 30)),
        ("lend_lease_appropriation", "house", (328, 67)),
        ("lend_lease_appropriation", "senate", (59, 13)),
    ],
)
def test_the_blocs_sum_to_the_recorded_vote(
    bill: str, chamber: str, record: tuple[int, int]
) -> None:
    rows = votes.TALLIES[bill][chamber]
    assert (sum(y for _, y, _ in rows), sum(n for _, _, n in rows)) == record


def test_a_bill_settles_into_dated_tallies() -> None:
    r = rig()
    r.set_value("gallup_fdr_about_right", 61)  # where the historical run stands at settlement
    order = enact_bill(r, "neutrality_revision", "urgent")
    changes = votes.resolve(r.state, order)
    r.fold_changes(changes)
    by_key = {
        k: r.state.quantities[qid(r.state, k)]
        for k in (
            "neutrality_revision_house_yeas",
            "neutrality_revision_senate_nays",
            "neutrality_revision_concur_yeas",
            "neutrality_revision_passed",
        )
    }
    assert by_key["neutrality_revision_house_yeas"].as_of == date(1941, 10, 17)
    assert by_key["neutrality_revision_senate_nays"].as_of == date(1941, 11, 7)
    assert by_key["neutrality_revision_concur_yeas"].as_of == date(1941, 11, 13)
    assert by_key["neutrality_revision_passed"].as_of == date(1941, 11, 17)
    house = vals(r, "neutrality_revision_house_yeas", "neutrality_revision_house_nays")
    assert sum(v or 0 for v in house) == 397
    assert value(r.state, "neutrality_revision_passed") == 1


def test_the_other_bills_write_only_their_own_chambers() -> None:
    r = rig()
    order = enact_bill(r, "draft_extension")
    r.fold_changes(votes.resolve(r.state, order))
    assert value(r.state, "draft_extension_senate_yeas") is not None
    assert value(r.state, "draft_extension_concur_yeas") is None
    assert value(r.state, "draft_extension_passed") is not None
    assert value(r.state, "arm_merchant_ships_house_yeas") is None


def test_arm_merchant_ships_borrows_the_neutrality_rows() -> None:
    r = rig()
    order = enact_bill(r, "arm_merchant_ships")
    r.fold_changes(votes.resolve(r.state, order))
    assert (
        sum(
            v or 0
            for v in vals(r, "arm_merchant_ships_senate_yeas", "arm_merchant_ships_senate_nays")
        )
        == 87
    )


def test_a_bill_that_fails_a_chamber_is_not_passed() -> None:
    r = rig()
    r.set_value("gallup_fdr_about_right", 0)
    order = enact_bill(r, "draft_extension")  # 203-202 on the record: no cushion
    r.fold_changes(votes.resolve(r.state, order))
    yeas, nays = vals(r, "draft_extension_house_yeas", "draft_extension_house_nays")
    assert yeas is not None and nays is not None and yeas < nays
    assert value(r.state, "draft_extension_passed") == 0


def test_urgency_and_opinion_move_the_tally_the_right_way() -> None:
    r = rig()
    order = enact_bill(r, "draft_extension")
    base = votes.tally(r.state, order, "house", votes.TALLIES["draft_extension"]["house"])
    urgent = order.model_copy(update={"params": {**order.params, "urgency": "urgent"}})
    assert (
        votes.tally(r.state, urgent, "house", votes.TALLIES["draft_extension"]["house"])[0]
        > base[0]
    )
    r.set_value("gallup_fdr_about_right", 80)
    assert (
        votes.tally(r.state, order, "house", votes.TALLIES["draft_extension"]["house"])[0] > base[0]
    )


def test_votes_are_deterministic_and_seeded_by_the_campaign() -> None:
    a, b = rig(), rig()
    oa, ob = enact_bill(a, "neutrality_revision"), enact_bill(b, "neutrality_revision")
    assert votes.resolve(a.state, oa) == votes.resolve(b.state, ob)
    seen = set()
    for n in range(10):
        other = Rig("autumn-1941", CampaignId(derived_id(CAMPAIGN, "seed", n)))
        other.seat("roosevelt")
        other.close_session_zero()
        o = enact_bill(other, "neutrality_revision")
        seen.add(tuple(c.value for c in votes.resolve(other.state, o)))
    assert len(seen) > 1


# --- shipping -------------------------------------------------------------------


def naval(
    r: Rig, order_name: str, zone: str, seat: str = "roosevelt", day: date = date(1941, 9, 11)
) -> Order:
    return r.enact(
        seat,
        "naval_order",
        "naval_order",
        day,
        [day + timedelta(2), day + timedelta(4)],
        order=order_name,
        zone=zone,
    )


@pytest.mark.parametrize(
    ("name", "level"), [("escort_convoys", 1), ("shoot_on_sight", 2), ("hold_in_port", 0)]
)
def test_a_naval_order_sets_the_escort_level_as_of_settlement(name: str, level: int) -> None:
    r = rig()
    order = naval(r, name, "western_atlantic")
    r.fold_changes(shipping.resolve(r.state, order))
    q = r.state.quantities[qid(r.state, "us_atlantic_escort")]
    assert (q.value, q.as_of) == (level, date(1941, 9, 15))


def test_the_pacific_changes_nothing() -> None:
    r = rig()
    assert shipping.resolve(r.state, naval(r, "shoot_on_sight", "pacific")) == ()


def test_a_german_directive_sets_the_posture_whoever_holds_the_card() -> None:
    r = Rig()
    r.seat("hitler")
    r.close_session_zero()
    day = date(1941, 10, 1)
    order = r.enact(
        "hitler",
        "uboat_directive",
        "okw_directive",
        day,
        [
            day + timedelta(n)
            for n in range(len(r.state.pipelines[PipelineId(r.ids["okw_directive"])].steps))
        ],
        posture="unrestricted_atlantic",
        theatre="north_atlantic",
    )
    assert resolver_for(r.state, order) == "shipping"
    r.fold_changes(shipping.resolve(r.state, order))
    assert value(r.state, "uboat_posture") == 1


def month_start(r: Rig, d: date) -> tuple[QuantityChange, ...]:
    return shipping.on_day(r.state, d)


def test_losses_are_computed_on_the_first_and_never_overwrite_september() -> None:
    r = rig()
    assert shipping.on_day(r.state, date(1941, 9, 15)) == ()
    assert shipping.on_day(r.state, date(1941, 10, 1)) == ()  # Sep is sourced
    r.fold_changes(shipping.on_day(r.state, date(1941, 11, 1)))
    assert value(r.state, "uboat_losses_sep") == 209_835
    oct_ = value(r.state, "uboat_losses_oct")
    assert oct_ is not None and abs(oct_ - 209_835) <= 0.08 * 209_835
    assert shipping.on_day(r.state, date(1941, 11, 1)) == ()  # already computed


def test_escort_months_cut_losses() -> None:
    plain, escorted = rig(), rig()
    naval(escorted, "escort_convoys", "iceland_route", day=date(1941, 9, 11))
    for r in (plain, escorted):
        r.fold_changes(shipping.on_day(r.state, date(1941, 11, 1)))
        r.fold_changes(shipping.on_day(r.state, date(1941, 12, 1)))
    p = vals(plain, "uboat_losses_oct", "uboat_losses_nov")
    e = vals(escorted, "uboat_losses_oct", "uboat_losses_nov")
    assert None not in p + e
    assert (
        isinstance(e[0], int)
        and isinstance(e[1], int)
        and isinstance(p[0], int)
        and isinstance(p[1], int)
    )
    assert e[0] < p[0] and e[1] < p[1]
    # Sep escort order: Oct is one month on (0.85), Nov two (0.70), within the +-8% noise.
    assert abs(e[0] / 209_835 - 0.85) <= 0.85 * 0.08 + 1e-9
    assert abs(e[1] / 209_835 - 0.70) <= 0.70 * 0.08 + 1e-9


def test_an_unrestricted_posture_raises_losses() -> None:
    r, base = rig(), rig()
    r.set_value("uboat_posture", 1)
    for x in (r, base):
        x.fold_changes(shipping.on_day(x.state, date(1941, 11, 1)))
    assert (value(r.state, "uboat_losses_oct") or 0) > (value(base.state, "uboat_losses_oct") or 0)


# --- production -----------------------------------------------------------------


def finding(r: Rig, recipient: str, share: int) -> Order:
    d = date(1941, 10, 30)
    return r.enact(
        "roosevelt",
        "lend_lease_finding",
        "lend_lease_allocation",
        d,
        [d + timedelta(n) for n in (1, 3, 5)],
        recipient=recipient,
        share_pct=share,
    )


def test_a_soviet_finding_makes_them_eligible_and_allocates_a_share() -> None:
    r = rig()
    r.fold_changes(production.resolve(r.state, finding(r, "ussr", 10)))
    assert value(r.state, "soviet_lend_lease_eligible") == 1
    assert value(r.state, "lend_lease_share_ussr") == 598_500_000
    assert r.state.quantities[qid(r.state, "soviet_lend_lease_eligible")].as_of == date(1941, 11, 4)
    assert value(r.state, "lend_lease_second_appropriation") == 5_985_000_000


def test_another_recipient_gets_a_share_but_no_eligibility() -> None:
    r = rig()
    r.fold_changes(production.resolve(r.state, finding(r, "uk", 50)))
    assert value(r.state, "lend_lease_share_uk") == 2_992_500_000
    assert value(r.state, "soviet_lend_lease_eligible") is None


# --- opinion --------------------------------------------------------------------


def address(r: Rig, theme: str) -> Order:
    d = date(1941, 9, 9)
    return r.enact(
        "roosevelt", "public_address", "public_address", d, [d + timedelta(1)], theme=theme
    )


@pytest.mark.parametrize(
    ("theme", "target"),
    [("aid_to_allies", 57), ("freedom_of_the_seas", 58), ("national_unity", 56)],
)
def test_an_address_moves_the_target_from_the_current_figure(theme: str, target: int) -> None:
    r = rig()
    r.fold_changes(opinion.resolve(r.state, address(r, theme)))
    assert value(r.state, "opinion_target") == target
    assert value(r.state, "gallup_fdr_about_right") == 55


def test_the_target_is_clamped() -> None:
    r = rig()
    r.set_value("opinion_target", 99)
    r.fold_changes(opinion.resolve(r.state, address(r, "freedom_of_the_seas")))
    assert value(r.state, "opinion_target") == 100


def test_mondays_step_one_point_toward_the_target() -> None:
    r = rig()
    r.fold_changes(opinion.resolve(r.state, address(r, "freedom_of_the_seas")))
    assert opinion.on_day(r.state, date(1941, 9, 11)) == ()  # a Thursday
    for monday, expect in (
        (date(1941, 9, 15), 56),
        (date(1941, 9, 22), 57),
        (date(1941, 9, 29), 58),
    ):
        r.fold_changes(opinion.on_day(r.state, monday))
        assert value(r.state, "gallup_fdr_about_right") == expect
    assert opinion.on_day(r.state, date(1941, 10, 6)) == ()  # at target


def test_an_incident_lifts_the_target_once_on_the_next_monday() -> None:
    r = rig()
    greer = r.state.scheduled[
        next(i for i, s in r.state.scheduled.items() if i == r.ids["greer_incident"])
    ]
    r.add(ev.ScheduledEventFired(scheduled_id=greer.scheduled_id))
    r.fold_changes(opinion.on_day(r.state, date(1941, 9, 8)))
    assert vals(r, "opinion_target", "gallup_fdr_about_right") == [56, 56]
    r.fold_changes(opinion.on_day(r.state, date(1941, 9, 15)))
    assert value(r.state, "opinion_target") == 56


# --- the contract ---------------------------------------------------------------


def test_a_resolver_gives_nothing_for_an_order_that_is_not_enacted_or_not_its_lever() -> None:
    r = rig()
    d = date(1941, 10, 9)
    moving = r.draft(
        "roosevelt",
        "send_bill",
        "bill_through_congress",
        d,
        bill="draft_extension",
        urgency="urgent",
    )
    assert all(fn(r.state, moving) == () for fn in ON_ORDER.values())
    refused = r.settle(moving, OrderStatus.REFUSED)
    assert all(fn(r.state, refused) == () for fn in ON_ORDER.values())
    bill = enact_bill(r, "draft_extension")
    assert (
        shipping.resolve(r.state, bill)
        == opinion.resolve(r.state, bill)
        == production.resolve(r.state, bill)
        == ()
    )


def test_every_change_folds_and_the_toy_world_gives_nothing() -> None:
    r = rig()
    for order in (
        enact_bill(r, "neutrality_revision", "urgent"),
        finding(r, "ussr", 10),
        naval(r, "shoot_on_sight", "western_atlantic"),
    ):
        for fn in ON_ORDER.values():
            r.fold_changes(fn(r.state, order))
    toy = rig("realism-toy", "ada")
    for day in (date(1900, 4, 2), date(1900, 5, 1), date(1900, 6, 4)):
        assert all(fn(toy.state, day) == () for fn in ON_DAY.values())
    d = date(1900, 4, 2)
    order = toy.draft("ada", "propose_levy", "levy_bill", d, rate=3, purpose="harbour")
    done = order.model_copy(update={"status": OrderStatus.ENACTED})
    assert all(fn(toy.state, done) == () for fn in ON_ORDER.values())
