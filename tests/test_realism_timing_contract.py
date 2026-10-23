"""Wave B contract: labelled timings, computed quantities and step timing."""

from datetime import date

import pytest
from pydantic import ValidationError

from revisionist.core.ids import EntityId, OrderId, PipelineId, SeatId
from revisionist.core.realism.check import assumed, gaps
from revisionist.core.realism.pipeline import (
    Order,
    OrderStatus,
    Pipeline,
    Step,
    StepAction,
    StepKind,
    StepOutcome,
    step_due,
)
from revisionist.core.world import FeedDecl, QuantityDecl, StepDecl
from revisionist.engine import campaign as cmp

P = EntityId("01ARZ3NDEKTSV4RRFFQ69G5FAA")
PIPE = Pipeline(
    pipeline_id=PipelineId("01ARZ3NDEKTSV4RRFFQ69G5FAB"),
    name="p",
    steps=(
        Step(person=P, kind=StepKind.CLERICAL, days=2),
        Step(person=P, kind=StepKind.DECISION, days=None, allowed=frozenset(StepAction)),
    ),
)


def _order(**kw: object) -> Order:
    base: dict[str, object] = {
        "order_id": OrderId("01ARZ3NDEKTSV4RRFFQ69G5FAC"),
        "issuer": SeatId("01ARZ3NDEKTSV4RRFFQ69G5FAD"),
        "lever": "l",
        "pipeline": PIPE.pipeline_id,
        "filed": date(1941, 10, 9),
    }
    return Order.model_validate(base | kw)


def test_an_assumed_timing_needs_its_numbers() -> None:
    with pytest.raises(ValidationError):
        StepDecl(person="a", kind=StepKind.CLERICAL, assumed=True)
    with pytest.raises(ValidationError):
        FeedDecl.model_validate(
            {
                "key": "f",
                "name": "f",
                "author": "a",
                "kind": "memo",
                "channel": "letter",
                "every_days": 7,
                "assumed": True,
                "gap": True,
            }
        )


def test_a_computed_quantity_has_no_value_source_or_gap() -> None:
    QuantityDecl(key="q", name="q", unit="votes", computed=True)
    with pytest.raises(ValidationError):
        QuantityDecl(key="q", name="q", unit="votes", computed=True, value=1)
    with pytest.raises(ValidationError):
        QuantityDecl(key="q", name="q", unit="votes", computed=True, gap=True)
    with pytest.raises(ValidationError):
        QuantityDecl(key="q", name="q", unit="votes")


def test_autumn_1941_lists_assumed_timings_apart_from_gaps() -> None:
    world = cmp.load_world(cmp.world_path("autumn-1941"))
    labelled = assumed(world)
    assert labelled and all("assumed" in g.what for g in labelled)
    assert not any("days is None" in g.what for g in gaps(world))
    bill = next(p for p in world.pipelines if p.key == "bill_through_congress")
    assert bill.source == "hjr237_timeline"
    assert [s.days for s in bill.steps] == [8, 1, 20, 10]
    assert [s.assumed for s in bill.steps] == [False, True, False, False]
    assert all(f.every_days and f.latency_days is not None for f in world.feeds)


def test_step_due_counts_from_filing_then_from_the_last_outcome() -> None:
    order = _order()
    assert step_due(order, PIPE) == date(1941, 10, 11)
    moved = _order(
        step=1,
        outcomes=(StepOutcome(step=0, person=P, action=StepAction.PASS, on=date(1941, 10, 11)),),
    )
    assert step_due(moved, PIPE) is None  # a gap length never falls due
    assert step_due(_order(status=OrderStatus.ENACTED), PIPE) is None
    assert step_due(_order(step=2), PIPE) is None


def test_only_a_human_held_decision_step_waits() -> None:
    from revisionist.core import events as ev
    from revisionist.core.fold import replay
    from revisionist.core.ids import CampaignId, EventId, derived_id
    from revisionist.core.realism.pipelines import waiting_on_human
    from revisionist.core.visibility import PUBLIC
    from revisionist.core.world_compile import compile_world, resolve_keys

    cid = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FAV")
    world = cmp.load_world(cmp.world_path("realism-toy"))
    ids = resolve_keys(world, cid)
    events = compile_world(world, cid, cmp.versions())
    seat = SeatId(derived_id(cid, "seat", "bram"))

    def add(payload: ev.Payload) -> None:
        events.append(
            ev.Event(
                event_id=EventId(derived_id(cid, "test-event", len(events))),
                campaign_id=cid,
                seq=len(events),
                tick=0,
                scope=PUBLIC,
                payload=payload,
                versions=cmp.versions(),
            )
        )

    add(ev.CharacterClaimed(seat_id=seat, entity_id=EntityId(ids["bram"])))
    add(ev.SessionZeroClosed())
    order = _order(issuer=seat, pipeline=PipelineId(ids["levy_bill"]), lever="propose_levy")
    add(ev.OrderDrafted(order=order))
    state = replay(events)
    assert not waiting_on_human(state, state.orders[order.order_id])  # cora's clerical step
    at_bram = state.orders[order.order_id].model_copy(update={"step": 1})
    assert waiting_on_human(state, at_bram)
    assert not waiting_on_human(state, at_bram.model_copy(update={"status": OrderStatus.REFUSED}))
