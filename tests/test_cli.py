"""Layer 7: the CLI end to end on the bundled world, with a scripted transport.

No model is called here. ``FakeClaude`` answers each call class with a fixed,
schema-valid reply, so these tests prove the wiring: compile, order, deputy
turns, tick, briefings on disk, cost accounting, ``why``, ``replay`` and the
leak gate.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from revisionist import __version__, cli
from revisionist.engine import campaign as cmp
from revisionist.engine.llm import Reply, Request
from revisionist.engine.store import load_state

runner = CliRunner()

BRIEF = {
    "text": "Mira: the harbour is calm.",
    "dilemma": "Toll or trade?",
    "moves": ["a", "b", "c"],
}


class FakeClaude:
    """One canned, valid reply per call class. ``narration`` lets a test plant text."""

    def __init__(self, narration: str = "A quiet week on the strait.") -> None:
        self.narration = narration
        self.calls: list[str] = []

    def call(self, request: Request) -> Reply:
        self.calls.append(request.call_class)
        out: dict[str, Any]
        match request.call_class:
            case "structure":
                out = {
                    "kind": "intent",
                    "intent": {"verb": "declare", "spin": "The strait is open."},
                }
            case "brief":
                out = dict(BRIEF)
            case "deputy":
                out = {"intents": [{"verb": "negotiate"}]}
            case "resolver":
                out = {"proposals": []}
            case "advise":
                out = {"answer": "Hold the strait and wait."}
            case _:
                out = {"text": self.narration}
        return Reply(out, request.model, 100, 20, 0, 0.001)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[FakeClaude]:
    monkeypatch.setenv("REVISIONIST_DB", str(tmp_path / "game.db"))
    claude = FakeClaude()
    monkeypatch.setattr(cli, "transport_factory", lambda: claude)
    yield claude


def _ok(*args: str) -> str:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_version() -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_commands_need_a_campaign(fake: FakeClaude) -> None:
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 1
    assert "revisionist new" in result.output


def test_new_compiles_the_bundled_world(fake: FakeClaude) -> None:
    out = _ok("new", "salt-strait")
    assert "Salt Strait" in out and "First Minister of Varda" in out and "3 AI factions" in out
    store = cmp.open_store()
    state = load_state(store, cmp.current_campaign(store))
    assert state.tick == 0 and len(state.seats) == 5


def test_new_rejects_an_unknown_world(fake: FakeClaude) -> None:
    result = runner.invoke(cli.app, ["new", "atlantis"])
    assert result.exit_code == 1
    assert "no world" in result.output


def test_new_reports_world_problems(fake: FakeClaude, tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    text = (cmp.WORLDS_DIR / "salt-strait.yaml").read_text()
    bad.write_text(text.replace("embodies: varda\n", "embodies: atlantis\n"))
    result = runner.invoke(cli.app, ["new", str(bad)])
    assert result.exit_code == 1
    assert "world has problems" in result.output and "atlantis" in result.output


def test_status_shows_only_what_varda_sees(fake: FakeClaude) -> None:
    _ok("new", "salt-strait")
    out = _ok("status")
    assert "tick 0" in out and "Keep the strait open" in out
    assert "Harrow" not in out  # Ostrel's covert march is not Varda's to see


def test_order_files_an_intent_and_logs_its_cost(fake: FakeClaude) -> None:
    _ok("new", "salt-strait")
    out = _ok("order", "Tell the world the strait stays open.")
    assert out.startswith("filed ") and "declare" in out
    assert "pending" in _ok("status")
    assert "structure" in _ok("cost")


def test_briefing_prints_a_dilemma_and_three_moves(fake: FakeClaude) -> None:
    _ok("new", "salt-strait")
    out = _ok("briefing")
    assert "Dilemma: Toll or trade?" in out and "  3. c" in out


def test_tick_runs_deputies_then_resolves(fake: FakeClaude, tmp_path: Path) -> None:
    _ok("new", "salt-strait")
    _ok("order", "Declare the strait open.")
    out = _ok("tick")
    assert "tick 0 resolved" in out
    assert fake.calls.count("deputy") == 3
    assert "resolver" in fake.calls and "narrator" in fake.calls
    briefings = {p.name for p in (tmp_path / "game-briefings").glob("t000-*.md")}
    assert "t000-first-minister-of-varda.md" in briefings
    assert "0 leaks" in _ok("leaks")


def test_ten_offline_ticks_replay_and_cost(fake: FakeClaude) -> None:
    _ok("new", "salt-strait")
    for _ in range(10):
        _ok("tick", "--offline")
    assert fake.calls == []
    assert "tick 10" in _ok("status")
    assert "hash " in _ok("replay", "--to", "4")
    assert "over 10 ticks" in _ok("cost")
    assert "0 leaks" in _ok("leaks")


def test_why_traces_a_filed_order(fake: FakeClaude) -> None:
    _ok("new", "salt-strait")
    intent_id = _ok("order", "Declare the strait open.").split()[1].rstrip(":")
    _ok("tick")
    out = _ok("why", intent_id)
    assert "order_received" in out and "intent_filed" in out
    assert "nothing found" in _ok("why", "NOPE")


def test_a_leaked_canary_fails_the_tick(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REVISIONIST_DB", str(tmp_path / "game.db"))
    claude = FakeClaude(narration="Rumour: CANARY-OSTREL-HARROW-4c1e")
    monkeypatch.setattr(cli, "transport_factory", lambda: claude)
    _ok("new", "salt-strait")
    result = runner.invoke(cli.app, ["tick"])
    assert result.exit_code == cli.EXIT_LEAK
    assert "LEAK" in result.output
    assert runner.invoke(cli.app, ["leaks"]).exit_code == cli.EXIT_LEAK


def test_a_duo_campaign_needs_the_seat_named(fake: FakeClaude) -> None:
    out = _ok("new", "salt-strait-duo")
    assert "human seats:" in out and "Ostrel War Council" in out and "2 AI factions" in out
    result = runner.invoke(cli.app, ["order", "Declare the strait open."])
    assert result.exit_code == cli.EXIT_ERROR and "pick one with --seat" in result.output
    assert _ok("order", "Declare the strait open.", "--seat", "ostrel war council").startswith(
        "filed "
    )
    assert "seat Ostrel War Council" in _ok("status", "--seat", "Ostrel War Council")
    ai = runner.invoke(cli.app, ["status", "--seat", "Synod of Kesh"])
    assert ai.exit_code == cli.EXIT_REFUSED and "not a human seat" in ai.output
    unknown = runner.invoke(cli.app, ["briefing", "--seat", "Atlantis"])
    assert unknown.exit_code == cli.EXIT_ERROR and "no seat" in unknown.output


def test_spin_is_the_players_and_only_theirs(fake: FakeClaude) -> None:
    _ok("new", "salt-strait")
    _ok("order", "Declare the strait open.")  # FakeClaude's reply carries a spin of its own
    store = cmp.open_store()
    [intent] = load_state(store, cmp.current_campaign(store)).pending.values()
    assert intent.spin is None and intent.scope.visibility.value == "private"
    too_long = runner.invoke(cli.app, ["order", "Declare it.", "--spin", "x" * 281])
    assert too_long.exit_code == cli.EXIT_ERROR and "280" in too_long.output
    assert "(public)" in _ok("order", "Declare it.", "--visibility", "public", "--spin", "Open.")
