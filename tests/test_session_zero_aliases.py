"""Playtest #35: ``pick``'s one model call may say a typed name is a listed person, and
the name normaliser behind the roster lookup."""

from typing import Any

import pytest

from revisionist.engine import session_zero as sz
from revisionist.engine.llm import Reply, Request
from revisionist.engine.store import MemoryStore, load_state
from tests.test_session_zero_steps import A, B, Table, _florence


class Nickname(Table):
    def __init__(self, same_as: str | None) -> None:
        super().__init__()
        self.same_as = same_as

    def call(self, request: Request) -> Reply:
        if request.call_class != "character_pick":
            return super().call(request)
        self.calls.append("character_pick")
        out: dict[str, Any] = {"existed": True, "placed": False, "note": "Known by a nickname."}
        if self.same_as is not None:
            out["same_as"] = self.same_as
        return Reply(out, request.model, 10, 10, 0, 0.01)


def test_the_model_names_the_listed_person_behind_a_nickname() -> None:
    store, table = MemoryStore(), Nickname("Lorenzo de' Medici")
    c = _florence(store, table)
    size = len(load_state(store, c).roster)

    assert sz.pick(store, c, A, "Il Magnifico", table) == "Lorenzo de' Medici"

    state = load_state(store, c)
    assert len(state.roster) == size
    assert table.calls.count("character_pick") == 1
    with pytest.raises(sz.StepRefusedError, match="Lorenzo de' Medici is already taken"):
        sz.pick(store, c, B, "Il Magnifico", table)


def test_an_unknown_same_as_is_ignored() -> None:
    store, table = MemoryStore(), Nickname("Nobody Listed")
    c = _florence(store, table)

    with pytest.raises(sz.StepRefusedError, match="does not place Il Magnifico"):
        sz.pick(store, c, A, "Il Magnifico", table)
    assert not any(ch.claimed_by for ch in load_state(store, c).roster.values())


@pytest.mark.parametrize(
    ("name", "words"),
    [
        ("Lorenzo de' Medici", ["lorenzo", "medici"]),
        ("  Niccolò   DI Bernardo ", ["niccolo", "bernardo"]),
        ("Thomas J. Jackson", ["thomas", "j", "jackson"]),
        ("The Duke of Urbino", ["duke", "urbino"]),
        ("", []),
    ],
)
def test_the_normaliser_drops_case_accents_punctuation_and_particles(
    name: str, words: list[str]
) -> None:
    assert sz._words(name) == words


@pytest.mark.parametrize(
    ("typed", "listed", "same"),
    [
        ("Thomas Jackson", "Thomas J. Jackson", True),
        ("Thomas J Jackson", "Thomas Jackson", True),
        ("J. Jackson", "John Jackson", True),
        ("Thomas Jackson", "Thomas Smith", False),
        ("Jackson", "Thomas Jackson", False),
        ("K. Jackson", "John Jackson", False),
    ],
)
def test_an_initial_fits_a_word_it_starts(typed: str, listed: str, same: bool) -> None:
    assert sz._same_name(sz._words(typed), sz._words(listed)) is same
