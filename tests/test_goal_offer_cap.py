"""Playtest #36: ``/goal`` crashed when the agent offered four or five valid goals.

The prompt and schema let the agent offer five; the logged event holds three, so the
write failed after the paid call and the player read "the host has the log".
Drives the real ``/goal`` command, not ``propose_goals``.
"""

from typing import Any

import pytest

from revisionist.core import goals as g
from revisionist.engine import session_zero as sz
from revisionist.engine.discord.handlers import BROKEN
from revisionist.engine.llm import Reply, Request
from revisionist.engine.store import MemoryStore, load_state
from tests.test_discord_bot import HOST
from tests.test_discord_public import _seated, zero  # noqa: F401
from tests.test_session_zero_steps import A, Table, _florence, _section


class Crowd(Table):
    """A goal agent that offers as many valid goals as it is asked for."""

    offers = 5

    def call(self, request: Request) -> Reply:
        if request.call_class != "goal":
            return super().call(request)
        self.calls.append("goal")
        view = _section(request.prompt, "What your character knows")
        me = view["me"]["embodies"]
        others = [k for k, e in view["entities"].items() if k != me and e["kind"] != "person"]
        options = [
            {
                "text": f"Outlast {view['entities'][k]['name']}.",
                "condition": {"check": "entity_is", "entity": k, "status": "dissolved"},
            }
            for k in others[: self.offers]
        ]
        assert len(options) == self.offers, "the Florence world has too few non-person entities"
        out: dict[str, Any] = {"options": options}
        return Reply(out, request.model, 10, 10, 0, 0.01)


@pytest.mark.parametrize("offers", [4, 5])
def test_a_crowd_of_valid_goals_is_trimmed_to_three_not_a_crash(zero: Any, offers: int) -> None:  # noqa: F811
    zero.transport = crowd = Crowd()
    crowd.offers = offers
    _seated(zero)
    zero.command("character", HOST, action="claim", text="Lorenzo de' Medici")

    reply = zero.command("goal", HOST, action="history")

    assert reply != BROKEN
    labels = [line for line in reply.splitlines() if line[:1] == "H" or "H1" in line]
    assert reply.count("[") >= 1 and "H4" not in reply and "H5" not in reply
    assert "H1" in reply and "H3" in reply, labels
    assert reply.endswith("/goal confirm <label>, e.g. H1.")

    store = zero.store()
    try:
        state = load_state(store, zero.campaign())
    finally:
        store.close()
    assert state.goals == {}  # offered, not yet confirmed


class Ladder(Table):
    """Five goals, each needing one more rival entity to fall than the last."""

    def __init__(self) -> None:
        super().__init__()
        self.offered: dict[str, dict[str, Any]] = {}

    def _goals(self, prompt: str) -> dict[str, Any]:
        view = _section(prompt, "What your character knows")
        me = view["me"]["embodies"]
        others = [k for k, e in view["entities"].items() if k != me and e["kind"] != "person"]
        options = []
        for n in range(5, 0, -1):
            parts = [{"check": "entity_is", "entity": k, "status": "dissolved"} for k in others[:n]]
            condition = parts[0] if n == 1 else {"check": "all_of", "conditions": parts}
            self.offered[f"Rung {n}"] = condition
            options.append({"text": f"Rung {n}", "condition": condition})
        return {"options": options}


def test_five_goals_keep_the_easiest_the_middle_and_the_hardest() -> None:
    store, table = MemoryStore(), Ladder()
    c = _florence(store, table)
    sz.claim(store, c, A, "Lorenzo de' Medici")

    lines = sz.propose_goals(store, c, A, "history", table)

    state = load_state(store, c)
    difficulty = {
        text: g.difficulty(state, A, sz._CONDITION.validate_python(condition))
        for text, condition in table.offered.items()
    }
    ranked = sorted(difficulty.values())
    assert len(set(ranked)) > 1
    shown = [line.split(". ", 1)[1].split(" [", 1)[0] for line in lines]
    assert [difficulty[text] for text in shown] == [ranked[0], ranked[2], ranked[4]]
    assert len(lines) == 3
