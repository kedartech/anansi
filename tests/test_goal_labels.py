"""Playtest #12: each offered goal carries its route's label, and confirm takes that label.

Before this, both lists numbered 1-3 and ``confirm 1`` took whichever list came last.
"""

import pytest

from revisionist.core import events as ev
from revisionist.core.ids import CampaignId
from revisionist.engine import session_zero as sz
from revisionist.engine.store import MemoryStore, load_state
from tests.test_session_zero_steps import A, B, Table, _florence


def _seated() -> tuple[MemoryStore, Table, CampaignId]:
    store, table = MemoryStore(), Table()
    c = _florence(store, table)
    sz.claim(store, c, A, "Lorenzo de' Medici")
    sz.claim(store, c, B, "Tommaso Vanni")
    return store, table, c


def _goal_sets(store: MemoryStore, c: CampaignId) -> list[ev.GoalSet]:
    return [e.payload for e in store.read(c) if isinstance(e.payload, ev.GoalSet)]


def test_lines_carry_the_route_letter() -> None:
    store, table, c = _seated()
    history = sz.propose_goals(store, c, A, "history", table)
    could = sz.propose_goals(store, c, A, "could_have", table)
    assert history[0].startswith("H1. ") and history[1].startswith("H2. ")
    assert could[0].startswith("C1. ")


def test_label_fixes_the_option_from_its_own_list() -> None:
    store, table, c = _seated()
    sz.propose_goals(store, c, A, "history", table)
    sz.propose_goals(store, c, A, "could_have", table)
    assert "Your goal is fixed" in sz.confirm_goal(store, c, A, "C1")
    [goal] = load_state(store, c).goals.values()
    assert goal.text.startswith("Take the bank and the bell.")


def test_bare_number_is_refused_when_two_lists_are_open() -> None:
    store, table, c = _seated()
    sz.propose_goals(store, c, A, "history", table)
    sz.propose_goals(store, c, A, "could_have", table)
    with pytest.raises(sz.StepRefusedError, match="more than one list"):
        sz.confirm_goal(store, c, A, "1")
    with pytest.raises(sz.StepRefusedError, match="more than one list"):
        sz.confirm_goal(store, c, A, 1)
    assert _goal_sets(store, c) == []


def test_bare_number_works_with_one_list() -> None:
    store, table, c = _seated()
    sz.propose_goals(store, c, A, "history", table)
    assert "Hold the Gonfalonier of Justice" in sz.confirm_goal(store, c, A, "1")
    assert len(_goal_sets(store, c)) == 1


def test_bare_int_works_with_one_list() -> None:
    store, table, c = _seated()
    sz.propose_goals(store, c, B, "history", table)
    assert "Hold the Gonfalonier of Justice" in sz.confirm_goal(store, c, B, 1)
    assert len(_goal_sets(store, c)) == 1


def test_label_is_case_insensitive() -> None:
    store, table, c = _seated()
    sz.propose_goals(store, c, A, "history", table)
    sz.propose_goals(store, c, A, "could_have", table)
    assert "Your goal is fixed" in sz.confirm_goal(store, c, A, " c1 ")
    assert len(_goal_sets(store, c)) == 1


@pytest.mark.parametrize("label", ["H9", "C2", "O1"])
def test_label_outside_the_offers_is_refused(label: str) -> None:
    store, table, c = _seated()
    sz.propose_goals(store, c, A, "history", table)
    sz.propose_goals(store, c, A, "could_have", table)
    with pytest.raises(sz.StepRefusedError, match="Choose one of the goals"):
        sz.confirm_goal(store, c, A, label)
    assert _goal_sets(store, c) == []


@pytest.mark.parametrize("label", ["X9", "", "H", "1H"])
def test_malformed_label_is_refused(label: str) -> None:
    store, table, c = _seated()
    sz.propose_goals(store, c, A, "history", table)
    with pytest.raises(sz.StepRefusedError, match="Give the label"):
        sz.confirm_goal(store, c, A, label)
    assert _goal_sets(store, c) == []
