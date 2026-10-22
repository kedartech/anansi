"""The two-step ``revisionist establish`` (spec §5.2): a pitch saves a setting card,
and a confirm opens the game from it. The GM is scripted, so nothing is called."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from revisionist import cli
from revisionist.engine import campaign as cmp
from revisionist.engine.pitch import PendingCard
from revisionist.engine.store import load_state
from tests.studio import Drafts
from tests.test_session_zero import FLORENCE, GM, WASHINGTON, draft

runner = CliRunner()


@pytest.fixture
def card_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("REVISIONIST_DB", str(tmp_path / "game.db"))
    monkeypatch.setenv("REVISIONIST_BRIEFINGS_DIR", str(tmp_path / "briefings"))
    return tmp_path / "revisionist-card.json"


def _gm(monkeypatch: pytest.MonkeyPatch, *drafts: dict[str, Any]) -> GM:
    gm = GM(*drafts)
    monkeypatch.setattr(cli, "transport_factory", lambda: gm)
    return gm


def test_the_pitch_prints_the_card_and_saves_it(
    card_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gm(monkeypatch, draft("washington-1962.yaml"))
    result = runner.invoke(cli.app, ["establish", "pitch", WASHINGTON])
    assert result.exit_code == 0, result.output
    assert "1. Washington, October 1962" in result.output
    assert "Confirm with: revisionist establish confirm <n>" in result.output
    saved = PendingCard.model_validate_json(card_file.read_text())
    assert saved.pitch == WASHINGTON
    assert saved.card.start_points[0].label == "Washington, October 1962"


def test_confirm_with_no_card_refuses(card_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gm = _gm(monkeypatch, draft("washington-1962.yaml"))
    result = runner.invoke(cli.app, ["establish", "confirm", "1"])
    assert result.exit_code == cli.EXIT_REFUSED
    assert "No setting card waiting" in result.output
    assert gm.requests == []


@pytest.mark.parametrize(
    "args",
    [
        ["1", "--strike", "x"],
        ["1", "--strike", "9"],
        ["1", "--end", "soon"],
        ["1", "--end", "1962-10-01"],
        ["2"],
    ],
)
def test_a_bad_choice_strike_or_end_refuses_before_any_draft(
    args: list[str], card_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gm = _gm(monkeypatch, draft("washington-1962.yaml"))
    assert runner.invoke(cli.app, ["establish", "pitch", WASHINGTON]).exit_code == 0
    asked = len(gm.requests)
    result = runner.invoke(cli.app, ["establish", "confirm", *args])
    assert result.exit_code == cli.EXIT_REFUSED, result.output
    assert len(gm.requests) == asked
    assert card_file.exists()


def test_a_strike_drops_that_history_line_from_the_schedule_prompt(
    card_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gm = _gm(monkeypatch, draft("washington-1962.yaml"))
    assert runner.invoke(cli.app, ["establish", "pitch", WASHINGTON]).exit_code == 0
    result = runner.invoke(cli.app, ["establish", "confirm", "1", "--strike", "1"])
    assert result.exit_code == 0, result.output
    prompts = [r.prompt for r in gm.requests if r.call_class == "draft_schedule"]
    assert prompts
    for prompt in prompts:
        assert "The second." in prompt
        assert "The first thing history did next." not in prompt


def test_end_and_days_land_in_the_settings(
    card_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gm(monkeypatch, draft("florence-1478.yaml"))
    assert runner.invoke(cli.app, ["establish", "pitch", FLORENCE]).exit_code == 0
    result = runner.invoke(
        cli.app, ["establish", "confirm", "1", "--end", "1478-12-31", "--days", "30"]
    )
    assert result.exit_code == 0, result.output
    store = cmp.open_store()
    settings = load_state(store, cmp.current_campaign(store)).settings
    assert settings.end_date == date(1478, 12, 31)
    assert settings.real_days == 30


def test_a_refused_draft_keeps_the_card_and_a_second_confirm_opens_the_game(
    card_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = draft("florence-1478.yaml")
    bad["roster"] = []
    gm = _gm(monkeypatch, bad)
    assert runner.invoke(cli.app, ["establish", "pitch", FLORENCE]).exit_code == 0
    failed = runner.invoke(cli.app, ["establish", "confirm", "1"])
    assert failed.exit_code == cli.EXIT_ERROR
    assert "roster: 0 characters" in failed.output
    assert card_file.exists()

    gm.drafts = Drafts(draft("florence-1478.yaml"))
    asked = len(gm.requests)
    result = runner.invoke(cli.app, ["establish", "confirm", "1"])
    assert result.exit_code == 0, result.output
    assert "Session Zero at Florence, April 1478" in result.output
    assert not any(r.call_class == "pitch" for r in gm.requests[asked:])


def test_a_confirm_deletes_the_card_and_prints_the_progress(
    card_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gm(monkeypatch, draft("florence-1478.yaml"))
    assert runner.invoke(cli.app, ["establish", "pitch", FLORENCE]).exit_code == 0
    result = runner.invoke(cli.app, ["establish", "confirm", "1"])
    assert result.exit_code == 0, result.output
    assert "Outline done." in result.output
    assert "Session Zero at Florence, April 1478" in result.output
    assert "characters to choose from:" in result.output
    assert not card_file.exists()
