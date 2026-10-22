"""Playtest #36: ``/goal`` crashed when the agent offered four or five valid goals.

The prompt and schema let the agent offer five; the logged event holds three, so the
write failed after the paid call and the player read "the host has the log".
Drives the real ``/goal`` command, not ``propose_goals``.
"""

from typing import Any

import pytest

from revisionist.engine.discord.handlers import BROKEN
from revisionist.engine.llm import Reply, Request
from revisionist.engine.store import load_state
from tests.test_discord_bot import HOST
from tests.test_discord_public import _seated, zero  # noqa: F401
from tests.test_session_zero_steps import Table, _section


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
