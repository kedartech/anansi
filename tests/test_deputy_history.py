"""Layer 6: the deputy follows history's clock (spec §5.4): it knows the next recorded event."""

from datetime import date
from typing import Any

import pytest

from revisionist.core.fold import replay
from revisionist.core.ids import ScheduledEventId
from revisionist.core.ontology import ScheduledEvent, ScheduleSource
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC
from revisionist.engine.agent import Agent
from revisionist.engine.llm import Reply, Request
from tests import fixtures as fx

TODAY = date(1500, 3, 1)
ORDERS = ("hold the harbour and report",)


class FakeTransport:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = list(outputs)
        self.requests: list[Request] = []

    def call(self, request: Request) -> Reply:
        self.requests.append(request)
        return Reply(self.outputs.pop(0), request.model, 8, 4, 0, 0.0)


@pytest.fixture
def state() -> State:
    return replay(fx.creation_events())


def _history(n: int, name: str, on: date, **kw: Any) -> ScheduledEvent:
    return ScheduledEvent(
        scheduled_id=ScheduledEventId(f"01ARZ3NDEKTSV4RRFFQ69G5HC{n}"),
        fire_at_tick=0,
        name=name,
        scope=PUBLIC,
        source=ScheduleSource.HISTORY,
        on_date=on,
        **kw,
    )


def _with_schedule(state: State, *events: ScheduledEvent) -> State:
    seated = state.seats[fx.NORTH].model_copy(update={"standing_orders": ORDERS})
    return state.model_copy(
        update={
            "seats": {**state.seats, fx.NORTH: seated},
            "in_world_date": TODAY,
            "scheduled": {s.scheduled_id: s for s in events},
        }
    )


def _deputy_prompt(state: State) -> str:
    t = FakeTransport([{"intents": []}])
    Agent.for_seat(state, fx.NORTH, t).deputy_turn()
    return t.requests[0].prompt


def _clock(prompt: str) -> str:
    """The History's clock section alone: the knowledge JSON lists every scheduled event."""
    start = prompt.index("## History's clock")
    return prompt[start : prompt.index("## What this seat knows")]


def test_the_deputy_names_the_next_pending_history_event_and_its_date(state: State) -> None:
    amended = _with_schedule(
        state,
        _history(1, "Treaty of Harrow", date(1500, 4, 1), weight=2),
        _history(2, "Siege of Vell", date(1500, 6, 1)),
    )
    prompt = _deputy_prompt(amended)
    assert _clock(prompt) == (
        "## History's clock\nNext as recorded: Treaty of Harrow (1500-04-01). "
        "Act as the record has this seat act unless the world as it stands has made that "
        "impossible; then replan from what you know.\n\n"
    )


def test_the_history_section_sits_after_the_standing_orders(state: State) -> None:
    amended = _with_schedule(state, _history(1, "Treaty of Harrow", date(1500, 4, 1)))
    prompt = _deputy_prompt(amended)
    orders_at = prompt.index("## Standing orders")
    clock_at = prompt.index("## History's clock")
    knows_at = prompt.index("## What this seat knows")
    assert orders_at < clock_at < knows_at


def test_the_deputy_omits_fired_and_skipped_history_events(state: State) -> None:
    amended = _with_schedule(
        state,
        _history(1, "Old Pact", date(1500, 3, 20), fired=True),
        _history(2, "Failed Coup", date(1500, 3, 25), skipped=True),
        _history(3, "Treaty of Harrow", date(1500, 4, 1)),
    )
    clock = _clock(_deputy_prompt(amended))
    assert "Next as recorded: Treaty of Harrow (1500-04-01)." in clock
    assert "Old Pact" not in clock and "Failed Coup" not in clock


def test_no_pending_history_leaves_the_prompt_as_it_was(state: State) -> None:
    amended = _with_schedule(state, _history(1, "Old Pact", date(1500, 3, 20), fired=True))
    seat = amended.seats[fx.NORTH]
    view = Agent.for_seat(amended, fx.NORTH, FakeTransport([])).view
    old = (
        f"## Seat: {seat.name}, tick {view.tick}\n"
        "## Standing orders\n"
        + "\n".join(f"- {o}" for o in ORDERS)
        + "\n\n## What this seat knows\n"
        + view.model_dump_json(exclude={"own_pending", "roster"}, exclude_none=True)
        + "\n\nReturn up to three intents that carry out the orders."
    )
    assert _deputy_prompt(amended) == old
    assert "History's clock" not in old


def test_a_world_with_no_schedule_gives_the_old_prompt(state: State) -> None:
    assert "History's clock" not in _deputy_prompt(_with_schedule(state))
