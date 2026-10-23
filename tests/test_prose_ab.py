"""The P0 prose A/B harness: refuses without ``--live``; with a recorded transport it
writes a blind sheet and a separate key. Never run live in tests."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from revisionist.engine.llm import RecordedTransport, Reply, Request
from tests.handwritten import AB

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prose_ab.py"


def ab() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prose_ab", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Forbidden:
    def call(self, request: Request) -> Reply:
        raise AssertionError("no call without --live")


def test_it_refuses_without_live(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    made: list[object] = []

    def factory() -> Forbidden:
        made.append(1)
        return Forbidden()

    assert ab().main(["--out", str(tmp_path)], transport=factory) == 2
    assert "--live" in capsys.readouterr().err
    assert made == [] and not list(tmp_path.iterdir())


def test_a_recorded_run_writes_a_blind_sheet_and_a_key(tmp_path: Path) -> None:
    module = ab()
    transport = RecordedTransport(AB)
    assert module.main(["--live", "--out", str(tmp_path)], transport=lambda: transport) == 0
    assert transport.hits == 10  # five pages, two models
    sheet = (tmp_path / "sheet.md").read_text()
    key = json.loads((tmp_path / "key.json").read_text())
    for model in module.MODELS:
        assert model not in sheet and "haiku" not in sheet.lower() and "sonnet" not in sheet.lower()
    assert sheet.count("### A") == sheet.count("### B") == 5
    assert len(key["pages"]) == 5
    assert {p["A"] for p in key["pages"]} | {p["B"] for p in key["pages"]} == set(module.MODELS)
    assert {p["A"] for p in key["pages"]} == set(module.MODELS)  # the order is shuffled
    for page in key["pages"]:
        assert page["A"] != page["B"]
        for call in page["calls"].values():
            assert call["dropped"] == [] and call["usd"] > 0
    again = tmp_path / "again"
    module.run(RecordedTransport(AB), again)
    assert (again / "key.json").read_text() == (tmp_path / "key.json").read_text()
