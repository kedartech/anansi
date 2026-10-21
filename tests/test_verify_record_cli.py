import unicodedata
from typing import Any

from revisionist.engine import session_zero as sz
from revisionist.engine.store import MemoryStore, load_state
from tests.test_character_search import Record
from tests.test_session_zero_steps import _florence


def _run(people: list[Any]) -> list[str]:
    store, table = MemoryStore(), Record(people)
    c = _florence(store, table)
    return sz.search(store, c, None, "a banker", table)


def _existing() -> str:
    store, table = MemoryStore(), Record()
    c = _florence(store, table)
    return next(e.name for e in load_state(store, c).entities.values())


def _tail(out: list[str]) -> list[str]:
    return out[out.index(sz.RECORD_HEADER) + 1 :] if sz.RECORD_HEADER in out else []


def test_null_name_or_note_is_dropped_not_stringified() -> None:
    out = _tail(_run([{"name": None, "note": "x"}, {"name": "Bernardo Rucellai", "note": None}]))
    assert out == [] or all("None" not in x for x in out), out


def test_double_space_variant_of_existing_name_is_deduped() -> None:
    name = _existing()
    out = _tail(_run([{"name": name.replace(" ", "  ", 1), "note": "n"}]))
    assert out == [], out


def test_nfd_variant_of_existing_name_is_deduped() -> None:
    name = _existing()
    out = _tail(_run([{"name": unicodedata.normalize("NFD", name) + "", "note": "n"}]))
    assert out == [], out


def test_newline_in_name_cannot_forge_a_line() -> None:
    out = _tail(_run([{"name": "A\nFrom the record: B", "note": "n"}]))
    assert all("\n" not in x for x in out), out


def test_people_not_a_list_is_ignored() -> None:
    assert sz.RECORD_HEADER not in _run("nobody")  # type: ignore[arg-type]
    assert sz.RECORD_HEADER not in _run({"a": 1})  # type: ignore[arg-type]
