"""Pipelines (unit B2): due steps, collapsed stretches, humans, settlement, leaks, amendments."""

from datetime import date, timedelta

import pytest

from revisionist.core import events as ev
from revisionist.core.fold import replay
from revisionist.core.ids import CampaignId, EntityId, EventId, OrderId, SeatId, derived_id
from revisionist.core.realism.pipeline import Order, OrderStatus, StepAction
from revisionist.core.realism.pipelines import (
    RealismStep,
    human_step,
    run_pipelines,
    waiting_on_human,
)
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC
from revisionist.core.world_compile import compile_world, resolve_keys
from revisionist.engine import campaign as cmp

CAMPAIGN = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FAV")
S = StepAction


class Rig:
    def __init__(self, human: str | None = None) -> None:
        world = cmp.load_world(cmp.world_path("realism-toy"))
        self.ids = resolve_keys(world, CAMPAIGN)
        self.events = compile_world(world, CAMPAIGN, cmp.versions())
        self.ada = SeatId(derived_id(CAMPAIGN, "seat", "ada"))
        self.add(ev.CharacterClaimed(seat_id=self.ada, entity_id=self.ids["ada"]))
        if human:
            seat = SeatId(derived_id(CAMPAIGN, "seat", human))
            self.add(ev.CharacterClaimed(seat_id=seat, entity_id=self.ids[human]))
        self.add(ev.SessionZeroClosed())
        self.order_id = OrderId(derived_id(CAMPAIGN, "order", 1))
        self.filed = date(1900, 4, 2)
        self.add(
            ev.OrderDrafted(
                order=Order(
                    order_id=self.order_id,
                    issuer=self.ada,
                    lever="propose_levy",
                    pipeline=self.state.cards[EntityId(self.ids["ada"])].levers[0].pipeline,
                    params={"rate": 8, "purpose": "harbour"},
                    filed=self.filed,
                )
            )
        )

    def add(self, payload: ev.Payload) -> None:
        seq = len(self.events)
        self.events.append(
            ev.Event(
                event_id=EventId(derived_id(CAMPAIGN, "test-event", seq)),
                campaign_id=CAMPAIGN,
                seq=seq,
                tick=0,
                scope=PUBLIC,
                payload=payload,
                versions=cmp.versions(),
            )
        )

    def add_all(self, out: list[RealismStep]) -> State:
        for p in out:
            self.add(p)
        return self.state

    @property
    def state(self) -> State:
        return replay(self.events)

    @property
    def order(self) -> Order:
        return self.state.orders[self.order_id]


def _with_loyalty(state: State, person: str, ids: dict[str, str], loyalty: int) -> State:
    key = EntityId(ids[person])
    cards = {**state.cards, key: state.cards[key].model_copy(update={"loyalty": loyalty})}
    return state.model_copy(update={"cards": cards})


def _find(rig: Rig, want: StepAction, state: State | None = None, day: int = 30) -> str:
    """A seed whose policy at bram's step takes ``want``."""
    state = state or rig.state
    today = rig.filed + timedelta(days=day)
    for n in range(2000):
        out = run_pipelines(state, today, f"s{n}")
        taken = [p for p in out if isinstance(p, ev.OrderStepTaken) and p.outcome.step == 1]
        if taken and taken[0].outcome.action is want:
            return f"s{n}"
    raise AssertionError(f"no seed gives {want}")


def test_clerical_passes_on_its_due_day_not_before() -> None:
    rig = Rig()
    assert run_pipelines(rig.state, rig.filed + timedelta(days=1), "s") == []
    out = run_pipelines(rig.state, rig.filed + timedelta(days=2), "s")
    assert len(out) == 1 and isinstance(out[0], ev.OrderStepTaken)
    assert out[0].outcome.action is S.PASS and out[0].outcome.on == rig.filed + timedelta(days=2)
    assert rig.add_all(out).orders[rig.order_id].step == 1


def test_a_collapsed_stretch_runs_several_steps_each_on_its_own_day() -> None:
    rig = Rig()
    state = _with_loyalty(rig.state, "bram", rig.ids, 5)
    seed = _find(rig, S.PASS, state)
    out = run_pipelines(state, rig.filed + timedelta(days=30), seed)
    steps = [p for p in out if isinstance(p, ev.OrderStepTaken)]
    assert steps[0].outcome.on == rig.filed + timedelta(days=2)
    assert steps[1].outcome.on == rig.filed + timedelta(days=7)
    assert isinstance(out[-1], ev.OrderSettled) and out[-1].status is OrderStatus.ENACTED
    assert rig.add_all(out).orders[rig.order_id].status is OrderStatus.ENACTED


def test_a_human_held_decision_waits_then_acts() -> None:
    rig = Rig(human="bram")
    rig.add_all(run_pipelines(rig.state, rig.filed + timedelta(days=2), "s"))
    assert waiting_on_human(rig.state, rig.order)
    late = rig.filed + timedelta(days=40)
    assert run_pipelines(rig.state, late, "s") == []
    out = human_step(rig.state, rig.order_id, S.PASS, late)
    assert isinstance(out[0], ev.OrderStepTaken) and out[0].outcome.on == late
    assert rig.add_all(out).orders[rig.order_id].status is OrderStatus.ENACTED


def test_human_step_rejects_a_wrong_action_or_an_order_not_waiting() -> None:
    rig = Rig(human="bram")
    day = rig.filed + timedelta(days=2)
    with pytest.raises(ValueError):  # still at the clerk's step
        human_step(rig.state, rig.order_id, S.PASS, day)
    rig.add_all(run_pipelines(rig.state, day, "s"))
    with pytest.raises(ValueError):
        human_step(rig.state, rig.order_id, S.EXPEDITE, day)
    with pytest.raises(ValueError):
        human_step(rig.state, rig.order_id, S.PASS, day, {"rate": 2})
    with pytest.raises(ValueError):
        human_step(rig.state, rig.order_id, S.AMEND, day)
    with pytest.raises(ValueError):
        human_step(rig.state, OrderId(derived_id(CAMPAIGN, "order", 99)), S.PASS, day)


def test_refuse_settles_refused() -> None:
    rig = Rig(human="bram")
    day = rig.filed + timedelta(days=9)
    rig.add_all(run_pipelines(rig.state, day, "s"))
    out = human_step(rig.state, rig.order_id, S.REFUSE, day)
    assert [p.kind for p in out] == ["order_step_taken", "order_settled"]
    assert rig.add_all(out).orders[rig.order_id].status is OrderStatus.REFUSED


def test_three_stalls_at_one_step_lapse_the_order() -> None:
    rig = Rig(human="bram")
    day = rig.filed + timedelta(days=9)
    rig.add_all(run_pipelines(rig.state, day, "s"))
    for i in range(2):
        out = human_step(rig.state, rig.order_id, S.STALL, day + timedelta(days=i))
        assert len(out) == 1
        assert rig.add_all(out).orders[rig.order_id].step == 1
    out = human_step(rig.state, rig.order_id, S.STALL, day + timedelta(days=2))
    assert isinstance(out[-1], ev.OrderSettled) and out[-1].status is OrderStatus.LAPSED
    assert rig.add_all(out).orders[rig.order_id].status is OrderStatus.LAPSED


def test_ai_stalls_repeatedly_then_lapses() -> None:
    rig = Rig()
    state = _with_loyalty(rig.state, "bram", rig.ids, -5)
    for n in range(300):
        out = run_pipelines(state, rig.filed + timedelta(days=400), f"s{n}")
        if out and isinstance(out[-1], ev.OrderSettled) and out[-1].status is OrderStatus.LAPSED:
            stalls = [
                p for p in out if isinstance(p, ev.OrderStepTaken) and p.outcome.action is S.STALL
            ]
            assert len(stalls) == 3
            assert rig.add_all(out).orders[rig.order_id].status is OrderStatus.LAPSED
            return
    raise AssertionError("no seed lapses")


def test_ai_refusal_settles_refused() -> None:
    rig = Rig()
    state = _with_loyalty(rig.state, "bram", rig.ids, -5)
    seed = _find(rig, S.REFUSE, state)
    out = run_pipelines(state, rig.filed + timedelta(days=30), seed)
    assert isinstance(out[-1], ev.OrderSettled) and out[-1].status is OrderStatus.REFUSED
    assert rig.add_all(out).orders[rig.order_id].status is OrderStatus.REFUSED


def test_a_leak_issues_a_public_press_document_with_no_truth() -> None:
    rig = Rig()
    state = rig.state
    seed = _find(rig, S.LEAK, state)
    out = run_pipelines(state, rig.filed + timedelta(days=30), seed)
    docs = [p for p in out if isinstance(p, ev.DocumentIssued)]
    assert len(docs) == 1
    doc = docs[0]
    on = rig.filed + timedelta(days=7)
    assert doc.document.kind.value == "press" and doc.document.channel.value == "press"
    assert doc.document.scope == PUBLIC and doc.document.title == "Leaked: propose_levy"
    assert doc.document.issued == on and doc.document.arrives == on + timedelta(days=1)
    assert doc.document.author == rig.ids["bram"]
    assert doc.truth.feed is None and doc.truth.truth_refs == ()
    assert {(b.label, b.value) for b in doc.document.body} == {("rate", 8), ("purpose", "harbour")}
    assert doc.document.doc_id in rig.add_all(out).documents


def test_an_amendment_stays_inside_the_lever_spec() -> None:
    rig = Rig()
    state = rig.state
    state = _with_loyalty(state, "bram", rig.ids, 0)
    for n in range(3000):
        out = run_pipelines(state, rig.filed + timedelta(days=30), f"s{n}")
        am = [p for p in out if isinstance(p, ev.OrderStepTaken) and p.outcome.action is S.AMEND]
        if am:
            assert am[0].outcome.amended == {"rate": 6}  # 8 - (9 // 4)
            assert rig.add_all(out).orders[rig.order_id].params["rate"] == 6
            return
    raise AssertionError("no seed amends")


def test_human_amendment_is_validated_and_folds() -> None:
    rig = Rig(human="bram")
    day = rig.filed + timedelta(days=9)
    rig.add_all(run_pipelines(rig.state, day, "s"))
    for bad in ({"rate": 11}, {"rate": 0}, {"nope": 1}, {"purpose": "tea"}, {"rate": "x"}):
        with pytest.raises(ValueError):
            human_step(rig.state, rig.order_id, S.AMEND, day, bad)  # type: ignore[arg-type]
    out = human_step(rig.state, rig.order_id, S.AMEND, day, {"rate": 2, "purpose": "militia"})
    state = rig.add_all(out)
    assert state.orders[rig.order_id].params == {"rate": 2, "purpose": "militia"}
    assert state.orders[rig.order_id].status is OrderStatus.ENACTED


def test_deterministic_and_seed_sensitive() -> None:
    rig = Rig()
    day = rig.filed + timedelta(days=30)
    assert run_pipelines(rig.state, day, "a") == run_pipelines(rig.state, day, "a")
    assert any(
        run_pipelines(rig.state, day, f"s{n}") != run_pipelines(rig.state, day, "a")
        for n in range(50)
    )


def test_a_gap_length_step_is_never_acted_on() -> None:
    rig = Rig()
    state = rig.state
    pl = state.pipelines[state.orders[rig.order_id].pipeline]
    gap = pl.model_copy(
        update={"steps": (pl.steps[0], pl.steps[1].model_copy(update={"days": None}))}
    )
    state = state.model_copy(update={"pipelines": {pl.pipeline_id: gap}})
    out = run_pipelines(state, date(2100, 1, 1), "s")
    assert len(out) == 1 and isinstance(out[0], ev.OrderStepTaken) and out[0].outcome.step == 0


def test_autumn_1941_runs_and_folds() -> None:
    world = cmp.load_world(cmp.world_path("autumn-1941"))
    ids = resolve_keys(world, CAMPAIGN)
    events = compile_world(world, CAMPAIGN, cmp.versions())

    def add(p: ev.Payload) -> None:
        events.append(
            ev.Event(
                event_id=EventId(derived_id(CAMPAIGN, "t", len(events))),
                campaign_id=CAMPAIGN,
                seq=len(events),
                tick=0,
                scope=PUBLIC,
                payload=p,
                versions=cmp.versions(),
            )
        )

    seat = SeatId(derived_id(CAMPAIGN, "seat", "roosevelt"))
    add(ev.CharacterClaimed(seat_id=seat, entity_id=ids["roosevelt"]))
    add(ev.SessionZeroClosed())
    state = replay(events)
    card = state.cards[EntityId(ids["roosevelt"])]
    lever = next(lv for lv in card.levers if lv.pipeline in {p for p in card.pipelines})
    oid = OrderId(derived_id(CAMPAIGN, "order", "a41"))
    params = {
        p.name: (p.lo if p.lo is not None else p.choices[0] if p.choices else "x")
        for p in lever.params
    }
    add(
        ev.OrderDrafted(
            order=Order(
                order_id=oid,
                issuer=seat,
                lever=lever.name,
                pipeline=lever.pipeline,
                params=params,
                filed=date(1941, 10, 9),
            )
        )
    )
    out = run_pipelines(replay(events), date(1942, 3, 1), "s")
    for p in out:
        add(p)
    assert replay(events).orders[oid].step >= 1 or not out
