"""Layer 8: ``revisionist db`` copy and verify, and the SQLite-to-Postgres migration test.

No model is called: orders go through the scripted ``FakeClaude`` from the CLI
tests and ticks run on the offline fixture providers.
"""

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from revisionist import cli
from revisionist.core.fold import replay, state_hash
from revisionist.core.resolution import TickStage
from revisionist.engine import campaign as cmp
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.providers import KeywordSafety, ScriptedResolver, TemplateNarrator
from revisionist.engine.store import SqliteStore, load_state
from revisionist.engine.tick import Providers, run_tick
from tests.pg import PgDb
from tests.test_cli import FakeClaude

runner = CliRunner()


def _run(*args: str) -> tuple[int, str]:
    result = runner.invoke(cli.app, list(args))
    return result.exit_code, result.output


def _ok(*args: str) -> str:
    code, out = _run(*args)
    assert code == 0, out
    return out


@pytest.fixture
def sqlite_campaign(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Three closed offline ticks with orders, then a fourth stopped after APPLY."""
    path = tmp_path / "solo.db"
    monkeypatch.setenv("REVISIONIST_DB", str(path))
    monkeypatch.setenv("REVISIONIST_BRIEFINGS_DIR", str(tmp_path / "briefings"))
    monkeypatch.setattr(cli, "transport_factory", FakeClaude)
    _ok("new", "salt-strait")
    for n in range(3):
        _ok("order", f"Declare the strait open, week {n}.")
        _ok("tick", "--offline")
    _ok("order", "Declare the strait open again.")
    store = SqliteStore(path)
    providers = Providers(
        ScriptedResolver(),
        TemplateNarrator(),
        cmp.FileDeliverer(tmp_path / "briefings", {}, 3),
        KeywordSafety(),
    )
    campaign_id = store.campaign_ids()[-1]
    stopped = run_tick(
        store, campaign_id, providers, versions=cmp.versions(), stop_after=TickStage.APPLY
    )
    assert stopped.stopped_after is TickStage.APPLY
    store.close()
    return path


def test_sqlite_campaign_moves_to_postgres_and_keeps_playing(
    sqlite_campaign: Path, pg_db: PgDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _ok("db", "copy", "--from", str(sqlite_campaign), "--to", pg_db.app_dsn)
    assert "copied " in out and "(0 already there)" in out

    lite = SqliteStore(sqlite_campaign)
    pg = PostgresEventStore(pg_db.app_dsn)
    campaign_id = lite.campaign_ids()[-1]
    assert pg.campaign_ids() == [campaign_id]
    source = list(lite.read(campaign_id))
    assert list(pg.read(campaign_id)) == source  # event for event
    assert state_hash(load_state(pg, campaign_id)) == state_hash(replay(source))
    assert pg.latest_snapshot(campaign_id) == lite.latest_snapshot(campaign_id)

    # rerunning the copy is a no-op
    assert ": 0 events (" in _ok(
        "db", "copy", "--from", str(sqlite_campaign), "--to", pg_db.app_dsn
    )

    # play on: the tick left after APPLY resumes on Postgres and closes
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    assert "tick 3" in _ok("status")
    assert "tick 3 resolved" in _ok("tick", "--offline")
    events = list(pg.read(campaign_id))
    opened = [e for e in events if e.kind == "tick_opened" and e.tick == 3]
    closed = [e for e in events if e.kind == "tick_closed"]
    assert len(opened) == 1 and len(closed) == 4
    assert "tick 4 resolved" in _ok("tick", "--offline")
    assert "ok " in _ok("db", "verify")
    assert "0 leaks" in _ok("leaks")
    assert lite.last_seq(campaign_id) == len(source) - 1  # the source is untouched
    lite.close()
    pg.close()


def test_copy_refuses_a_target_that_diverges(sqlite_campaign: Path, tmp_path: Path) -> None:
    other = tmp_path / "other.db"
    lite, elsewhere = SqliteStore(sqlite_campaign), SqliteStore(other)
    campaign_id = lite.campaign_ids()[-1]
    first = next(iter(lite.read(campaign_id)))
    elsewhere.append(first.model_copy(update={"tick": 7}))
    lite.close()
    elsewhere.close()
    code, out = _run("db", "copy", "--from", str(sqlite_campaign), "--to", str(other))
    assert code == cli.EXIT_ERROR and "diverges from source at seq 0" in out


def test_copy_needs_an_existing_source(tmp_path: Path) -> None:
    code, out = _run("db", "copy", "--from", str(tmp_path / "nope.db"), "--to", "x.db")
    assert code == cli.EXIT_ERROR and "no database" in out


def test_verify_passes_a_clean_log(sqlite_campaign: Path) -> None:
    out = _ok("db", "verify")
    assert out.startswith("ok ") and "tick 3" in out


def test_verify_catches_an_edited_log(sqlite_campaign: Path) -> None:
    conn = sqlite3.connect(sqlite_campaign)
    conn.execute("DROP TRIGGER events_no_update")
    conn.execute(
        "UPDATE events SET body = json_set(body, '$.payload.state_hash', 'deadbeef')"
        " WHERE seq = (SELECT max(seq) FROM events WHERE kind = 'tick_closed')"
    )
    conn.commit()
    conn.close()
    code, out = _run("db", "verify")
    assert code == cli.EXIT_ERROR and "FAIL" in out and "FoldError" in out


def test_verify_catches_a_bad_snapshot(sqlite_campaign: Path) -> None:
    store = SqliteStore(sqlite_campaign)
    campaign_id = store.campaign_ids()[-1]
    state = load_state(store, campaign_id)
    store.save_snapshot(state.model_copy(update={"tick": 99}))
    store.close()
    code, out = _run("db", "verify")
    assert code == cli.EXIT_ERROR and "differs from the replay" in out


def test_verify_needs_a_campaign(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REVISIONIST_DB", str(tmp_path / "empty.db"))
    code, out = _run("db", "verify")
    assert code == cli.EXIT_ERROR and "no campaigns" in out


def test_migrate_needs_the_admin_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REVISIONIST_ADMIN_DSN", raising=False)
    code, out = _run("db", "migrate")
    assert code == cli.EXIT_ERROR and "REVISIONIST_ADMIN_DSN" in out


def test_migrate_reports_up_to_date(pg_db: PgDb, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVISIONIST_ADMIN_DSN", pg_db.admin_dsn)
    assert "up to date" in _ok("db", "migrate", "--app-role", pg_db.app_role)


def test_postgres_briefings_default_beside_the_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REVISIONIST_BRIEFINGS_DIR", raising=False)
    assert cmp.briefings_dir("postgresql://h/db") == Path("revisionist-briefings")
    assert cmp.briefings_dir("/srv/game.db") == Path("/srv/game-briefings")
    monkeypatch.setenv("REVISIONIST_BRIEFINGS_DIR", "/srv/out")
    assert cmp.briefings_dir("postgresql://h/db") == Path("/srv/out")
