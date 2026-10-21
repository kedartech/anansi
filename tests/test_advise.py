"""Agent chat (playtest #17, decision D5): advice from the player's agent, never an order."""

from typing import Any

import pytest

from revisionist.core.ids import SeatId
from revisionist.engine.advise import advise
from revisionist.engine.llm import Reply, Request, prompt_text
from revisionist.engine.store import MemoryStore
from tests import fixtures as fx

STRANGER = SeatId("01ARZ3NDEKTSV4RRFFQ69G5SX0")


class FakeTransport:
    def __init__(self, output: Any) -> None:
        self.output = output
        self.requests: list[Request] = []

    def call(self, request: Request) -> Reply:
        self.requests.append(request)
        return Reply(self.output, request.model, 8, 4, 0, 0.0)


@pytest.fixture
def store() -> MemoryStore:
    s = MemoryStore()
    s.append_all(fx.creation_events())
    return s


def test_advise_asks_as_advice_and_never_leaks_the_plot(store: MemoryStore) -> None:
    t = FakeTransport({"text": "Hold the passes."})
    advise(store, fx.CAMPAIGN, fx.SOUTH, "What should I do about the north?", t)
    [req] = t.requests
    assert req.call_class == "advise"
    assert "What should I do about the north?" in req.prompt
    assert fx.CANARY not in req.prompt
    assert "roster" not in req.prompt


def test_advise_strips_the_reply(store: MemoryStore) -> None:
    t = FakeTransport({"text": "  Hold the passes.\n"})
    assert advise(store, fx.CAMPAIGN, fx.NORTH, "What now?", t) == "Hold the passes."


def test_advise_gives_the_fallback_for_an_empty_reply(store: MemoryStore) -> None:
    t = FakeTransport({"text": "   "})
    assert advise(store, fx.CAMPAIGN, fx.NORTH, "What now?", t) == "Your agent has nothing to add."


def test_advise_logs_only_the_cost(store: MemoryStore) -> None:
    before = list(store.read(fx.CAMPAIGN))
    advise(store, fx.CAMPAIGN, fx.NORTH, "What now?", FakeTransport({"text": "Wait."}))
    new = list(store.read(fx.CAMPAIGN))[len(before) :]
    assert [e.kind for e in new] == ["cost_logged"]
    assert "intent_filed" not in {e.kind for e in new}
    assert "order_received" not in {e.kind for e in new}


def test_advise_empty_question_makes_no_call(store: MemoryStore) -> None:
    t = FakeTransport({"text": "unused"})
    assert advise(store, fx.CAMPAIGN, fx.NORTH, "   ", t) == "Ask your agent a question."
    assert t.requests == []


def test_advise_unknown_seat_is_a_key_error(store: MemoryStore) -> None:
    with pytest.raises(KeyError):
        advise(store, fx.CAMPAIGN, STRANGER, "What now?", FakeTransport({"text": "x"}))


def test_advise_prompt_file_tells_the_agent_to_suggest_orders() -> None:
    assert "order:" in prompt_text("advise")
