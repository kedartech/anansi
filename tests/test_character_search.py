"""Character search also suggests real people the record places at the start (playtest issue 10).

The roster call is unchanged; the record call is one more, and its names are checked
against the world before anyone sees them.
"""

import json
import logging
from typing import Any

import pytest

from revisionist.engine import session_zero as sz
from revisionist.engine.llm import Reply, Request, TransportError, cli_schema
from revisionist.engine.store import MemoryStore, load_state
from tests.test_session_zero import FLORENCE
from tests.test_session_zero_steps import Table, _florence, _section

BANKERS = {"Lorenzo de' Medici", "Francesco de' Pazzi", "Tommaso Vanni (composite)"}


class Record(Table):
    """The roster call behaves as in ``Table``; the record call returns ``people`` or fails."""

    def __init__(self, people: list[Any] | None = None, error: Exception | None = None) -> None:
        super().__init__()
        self.people = people or []
        self.error = error
        self.record_prompts: list[str] = []

    def call(self, request: Request) -> Reply:
        if request.call_class != "character_record":
            return super().call(request)
        self.calls.append(request.call_class)
        self.record_prompts.append(request.prompt)
        if self.error is not None:
            raise self.error
        return Reply({"people": self.people}, request.model, 10, 10, 0, 0.01)


class RosterDown(Table):
    def call(self, request: Request) -> Reply:
        if request.call_class == "character_search":
            raise TransportError("the CLI is down")
        return super().call(request)


RUCELLAI = {"name": "Bernardo Rucellai", "note": "A Florentine banker and Medici rival in 1478."}


def test_record_suggestions_follow_the_roster_under_the_header() -> None:
    store, table = MemoryStore(), Record([RUCELLAI])
    c = _florence(store, table)
    found = sz.search(store, c, None, "someone who moves money", table)
    assert {x.split(":")[0] for x in found[:3]} == BANKERS
    assert found[3:] == [
        sz.RECORD_HEADER,
        "Bernardo Rucellai: A Florentine banker and Medici rival in 1478.",
    ]


def test_a_name_already_in_the_world_is_dropped() -> None:
    people = [
        {"name": "lorenzo de' medici", "note": "Already on the roster."},
        {"name": " Bernardo Rucellai ", "note": "Real, in Florence."},
        {"name": "The Pazzi", "note": "A family, already in the world."},
        {"name": "Bernardo Rucellai", "note": "A duplicate."},
        {"name": "  ", "note": "Blank."},
        "Not even a dict",
    ]
    store, table = MemoryStore(), Record(people)
    c = _florence(store, table)
    found = sz.search(store, c, None, "someone who moves money", table)
    assert found[3:] == [sz.RECORD_HEADER, "Bernardo Rucellai: Real, in Florence."]


def test_more_than_five_suggestions_are_capped() -> None:
    people = [{"name": f"Person {i}", "note": "x" * 300} for i in range(7)]
    store, table = MemoryStore(), Record(people)
    c = _florence(store, table)
    found = sz.search(store, c, None, "someone who moves money", table)
    record = found[found.index(sz.RECORD_HEADER) + 1 :]
    assert [r.split(":")[0] for r in record] == [f"Person {i}" for i in range(5)]
    assert all(len(r.split(": ", 1)[1]) == 160 for r in record)


def test_a_record_call_that_fails_keeps_the_roster_lines(caplog: pytest.LogCaptureFixture) -> None:
    store, table = MemoryStore(), Record(error=TransportError("the CLI is down"))
    c = _florence(store, table)
    with caplog.at_level(logging.WARNING, logger="revisionist.engine.session_zero"):
        found = sz.search(store, c, None, "someone who moves money", table)
    assert {x.split(":")[0] for x in found} == BANKERS
    assert sz.RECORD_HEADER not in found
    assert any("character record" in r.getMessage() for r in caplog.records)


def test_a_roster_call_that_fails_still_raises() -> None:
    store, table = MemoryStore(), RosterDown()
    c = _florence(store, table)
    with pytest.raises(TransportError):
        sz.search(store, c, None, "someone who moves money", table)


def test_the_record_prompt_carries_the_start_and_the_world() -> None:
    store, table = MemoryStore(), Record([RUCELLAI])
    c = _florence(store, table)
    sz.search(store, c, None, "a poet at court", table)
    [prompt] = table.record_prompts
    assert "## Start\n" + FLORENCE + "\n\n" in prompt
    assert "## What the player wants to play\na poet at court" in prompt
    world = _section(prompt, "Already in the world")
    assert {"Lorenzo de' Medici", "The Pazzi"} <= set(world)
    assert world == sorted(e.name for e in load_state(store, c).entities.values())


def test_the_record_schema_has_no_refs() -> None:
    assert "$ref" not in json.dumps(cli_schema(sz.RECORD_SCHEMA))


def test_an_empty_description_makes_no_record_call() -> None:
    store, table = MemoryStore(), Record([RUCELLAI])
    c = _florence(store, table)
    before = len(table.calls)
    with pytest.raises(sz.StepRefusedError, match="Say what you want to play"):
        sz.search(store, c, None, "   ", table)
    assert len(table.calls) == before
