"""Layer 9b: sealed campaigns on Postgres, end to end.

With ``$REVISIONIST_MASTER_KEY_FILE`` set, a new campaign is sealed: its rows
and its dump are ciphertext, the CLI and the bot read it as before, the app
role cannot destroy a key, and the restore drill passes only with the key.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import errors
from typer.testing import CliRunner

from revisionist import cli
from revisionist.core.ids import CampaignId, SeatId
from revisionist.engine import campaign as cmp
from revisionist.engine.discord.tables import SeatLinks
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.seal import KeyringSealer, PgKeyring, load_master_key, new_master_key
from revisionist.engine.store import StoreError, load_state
from tests.pg import PgDb
from tests.test_cli import FakeClaude
from tests.test_deploy import _script, _tools
from tests.test_discord_bot import GUILD, PLAYER, Game

runner = CliRunner()

ORDER_MARK = "ORDER-PLAINTEXT-MARK-31f2"
CANARIES = (
    "CANARY-OSTREL-HARROW-4c1e",
    "CANARY-KESH-PACT-9b27",
    "CANARY-LUNE-CHANNEL-e803",
    "CANARY-VARDA-SKIM-55d0",
)


@pytest.fixture
def key(tmp_path: Path) -> Path:
    path = tmp_path / "master.key"
    new_master_key(path)
    return path


@pytest.fixture
def sealed_game(
    pg_db: PgDb, key: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[PgDb]:
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    monkeypatch.setenv("REVISIONIST_MASTER_KEY_FILE", str(key))
    monkeypatch.setenv("REVISIONIST_BRIEFINGS_DIR", str(tmp_path / "briefings"))
    monkeypatch.setattr(cli, "transport_factory", FakeClaude)
    assert runner.invoke(cli.app, ["new", "salt-strait"]).exit_code == 0
    assert runner.invoke(cli.app, ["order", f"Declare it. {ORDER_MARK}"]).exit_code == 0
    for _ in range(2):
        assert runner.invoke(cli.app, ["tick", "--offline"]).exit_code == 0
    yield pg_db


def _raw(db: PgDb) -> str:
    with psycopg.connect(db.admin_dsn) as conn:
        rows = conn.execute(
            "SELECT kind || ' ' || body FROM events UNION ALL SELECT body FROM snapshots"
        ).fetchall()
    return "\n".join(r[0] for r in rows)


def test_a_new_campaign_is_ciphertext_at_rest(sealed_game: PgDb) -> None:
    raw = _raw(sealed_game)
    for secret in (*CANARIES, ORDER_MARK, "order_received", "First Minister"):
        assert secret not in raw
    with psycopg.connect(sealed_game.admin_dsn) as conn:
        kinds = {r[0] for r in conn.execute("SELECT DISTINCT kind FROM events")}
        holders = [r[0] for r in conn.execute("SELECT holder FROM seal_keys ORDER BY holder")]
    assert kinds == {"sealed"}
    assert "campaign" in holders and len(holders) >= 2  # the campaign and Varda, who spoke
    # and every reader still sees the game
    status = runner.invoke(cli.app, ["status"])
    assert status.exit_code == 0 and "tick 2" in status.output
    verify = runner.invoke(cli.app, ["db", "verify"])
    assert verify.exit_code == 0 and "sealed, 0 erased" in verify.output
    assert "0 leaks" in runner.invoke(cli.app, ["leaks"]).output


def test_without_the_key_a_sealed_campaign_is_refused(
    sealed_game: PgDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REVISIONIST_MASTER_KEY_FILE")
    status = runner.invoke(cli.app, ["status"])
    assert status.exit_code != 0 and isinstance(status.exception, StoreError)
    assert "REVISIONIST_MASTER_KEY_FILE" in str(status.exception)
    verify = runner.invoke(cli.app, ["db", "verify"])
    assert verify.exit_code == cli.EXIT_ERROR and "REVISIONIST_MASTER_KEY_FILE" in verify.output


def test_a_key_file_others_can_read_is_refused(sealed_game: PgDb, key: Path) -> None:
    key.chmod(0o640)
    with pytest.raises(StoreError, match="chmod 600"):
        cmp.open_store()


def test_the_app_role_cannot_destroy_a_key(sealed_game: PgDb) -> None:
    with psycopg.connect(sealed_game.app_dsn, autocommit=True) as conn:
        for statement in ("UPDATE seal_keys SET wrapped = NULL", "DELETE FROM seal_keys"):
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(statement.encode())


def test_two_processes_agree_on_a_seat_key(pg_db: PgDb, key: Path) -> None:
    master = load_master_key(key)
    campaign, seat = CampaignId("C" * 26), SeatId("S" * 26)
    with (
        psycopg.connect(pg_db.app_dsn, autocommit=True) as a,
        psycopg.connect(pg_db.app_dsn, autocommit=True) as b,
    ):
        first, second = PgKeyring(a, master), PgKeyring(b, master)
        first.create_campaign_key(campaign)
        made = first.seat_key(campaign, seat)
        assert second._create(campaign, seat) == made  # lost the race: uses the winner's key
        assert second.seat_key(campaign, seat) == made
        assert second.key(made[0]) == made[1]
        wrong = PgKeyring(a, b"\0" * 32)
        with pytest.raises(StoreError, match="authenticate"):
            wrong.campaign_key(campaign)


def test_a_fresh_copy_into_a_sealing_store_is_sealed(
    pg_db: PgDb, key: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sqlite = tmp_path / "solo.db"
    monkeypatch.setenv("REVISIONIST_DB", str(sqlite))
    monkeypatch.setattr(cli, "transport_factory", FakeClaude)
    assert runner.invoke(cli.app, ["new", "salt-strait"]).exit_code == 0
    assert runner.invoke(cli.app, ["tick", "--offline"]).exit_code == 0
    monkeypatch.setenv("REVISIONIST_MASTER_KEY_FILE", str(key))
    copied = runner.invoke(cli.app, ["db", "copy", "--from", str(sqlite), "--to", pg_db.app_dsn])
    assert copied.exit_code == 0, copied.output
    assert not any(c in _raw(pg_db) for c in CANARIES)
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    assert "sealed, 0 erased" in runner.invoke(cli.app, ["db", "verify"]).output


def test_bug_notes_are_sealed_with_the_seat_key(sealed_game: PgDb, tmp_path: Path) -> None:
    game = Game(sealed_game, FakeClaude(), tmp_path / "briefings")
    store = cmp.open_store()
    assert isinstance(store, PostgresEventStore)
    campaign_id = store.campaign_ids()[0]
    SeatLinks(store.conn).link(
        campaign_id, cmp.human_seat(load_state(store, campaign_id)), PLAYER, GUILD
    )
    assert game.command("bug", PLAYER, notes="the harbour vanished").startswith("Bug ")
    with psycopg.connect(sealed_game.admin_dsn) as conn:
        [(notes,)] = conn.execute("SELECT notes FROM discord_bugs").fetchall()
    assert notes.startswith("sealed:") and "harbour" not in notes
    assert isinstance(store.sealer, KeyringSealer)
    store.close()


def test_the_dump_is_ciphertext_and_the_drill_needs_the_key(
    sealed_game: PgDb, key: Path, tmp_path: Path
) -> None:
    _tools()
    env = {
        "REVISIONIST_ADMIN_DSN": sealed_game.admin_dsn,
        "REVISIONIST_BACKUP_DIR": str(tmp_path / "dumps"),
        "REVISIONIST_BACKUP_DEST": str(tmp_path / "offbox"),
        "REVISIONIST_BIN": str(Path(sys.executable).parent / "revisionist"),
    }
    (tmp_path / "offbox").mkdir()
    assert _script("revisionist-backup.sh", env).returncode == 0
    [dump] = (tmp_path / "dumps").glob("revisionist-*.dump")
    sql = subprocess.run(
        ["pg_restore", "--file=-", str(dump)], capture_output=True, text=True, check=True
    ).stdout
    assert "seal_keys" in sql  # the wrapped keys travel with the dump
    for secret in (*CANARIES, ORDER_MARK):
        assert secret not in sql

    without = _script("revisionist-restore-drill.sh", env)
    assert without.returncode != 0 and "restore drill passed" not in without.stdout
    drill = _script("revisionist-restore-drill.sh", env | {"REVISIONIST_MASTER_KEY_FILE": str(key)})
    assert drill.returncode == 0, drill.stderr
    assert "sealed, 0 erased" in drill.stdout and "restore drill passed" in drill.stdout
    assert os.stat(key).st_mode & 0o077 == 0
