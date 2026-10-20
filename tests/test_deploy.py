"""Layer 8: deployment artifacts under ``deploy/``.

The units are parsed, not run: nothing here needs systemd. The backup and
restore-drill scripts run for real against a scratch Postgres, with a local
directory standing in for the off-box target.
"""

import configparser
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.pg import PgDb
from tests.test_cli import FakeClaude
from wyrd import cli

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
UNITS = DEPLOY / "systemd"
BACKUP = DEPLOY / "backup"

runner = CliRunner()


def _unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str  # type: ignore[assignment,method-assign]  # keys are case-sensitive
    parser.read_string((UNITS / name).read_text())
    return parser


def _argv(exec_start: str) -> list[str]:
    return shlex.split(exec_start.replace("%h", "/home/u"))


def test_scheduler_service_runs_wyrd_scheduler_run() -> None:
    unit = _unit("wyrd-scheduler.service")
    service = unit["Service"]
    assert service["Type"] == "oneshot"
    assert service["EnvironmentFile"].startswith("%h/")
    argv = _argv(service["ExecStart"])
    assert Path(argv[0]).name == "wyrd" and argv[1:] == ["scheduler", "run"]
    assert runner.invoke(cli.app, [*argv[1:], "--help"]).exit_code == 0
    assert service["NoNewPrivileges"] == "yes"


def test_discord_service_runs_the_bot_with_the_scheduler_hardening() -> None:
    service = _unit("wyrd-discord.service")["Service"]
    scheduler = _unit("wyrd-scheduler.service")["Service"]
    argv = _argv(service["ExecStart"])
    assert Path(argv[0]).name == "wyrd" and argv[1:] == ["discord", "run"]
    assert runner.invoke(cli.app, [*argv[1:], "--help"]).exit_code == 0
    assert service["Type"] == "simple" and service["Restart"] == "on-failure"
    assert service["EnvironmentFile"] == scheduler["EnvironmentFile"]
    hardening = (
        "UMask",
        "NoNewPrivileges",
        "LockPersonality",
        "RestrictRealtime",
        "RestrictSUIDSGID",
        "SystemCallArchitectures",
    )
    assert {k: service[k] for k in hardening} == {k: scheduler[k] for k in hardening}
    assert _unit("wyrd-discord.service")["Install"]["WantedBy"] == "default.target"


def test_scheduler_timer_polls_every_ten_minutes() -> None:
    timer = _unit("wyrd-scheduler.timer")["Timer"]
    assert timer["OnCalendar"] == "*:0/10"
    assert timer["Persistent"] == "true"
    assert timer["RandomizedDelaySec"]
    assert timer["Unit"] == "wyrd-scheduler.service"


def test_backup_units_run_the_backup_script_nightly() -> None:
    argv = _argv(_unit("wyrd-backup.service")["Service"]["ExecStart"])
    assert Path(argv[0]).name == "wyrd-backup" and argv[1:] == []
    timer = _unit("wyrd-backup.timer")["Timer"]
    assert timer["OnCalendar"] == "*-*-* 03:30:00" and timer["Persistent"] == "true"


def test_console_script_is_wyrd() -> None:
    import tomllib

    project = tomllib.loads((DEPLOY.parent / "pyproject.toml").read_text())
    assert project["project"]["scripts"] == {"wyrd": "wyrd.cli:app"}


@pytest.mark.parametrize("script", ["wyrd-backup.sh", "wyrd-restore-drill.sh"])
def test_scripts_are_executable_bash(script: str) -> None:
    path = BACKUP / script
    assert os.access(path, os.X_OK)
    assert subprocess.run(["bash", "-n", str(path)], check=False).returncode == 0


def _tools() -> None:
    missing = [t for t in ("pg_dump", "pg_restore", "psql", "rsync") if shutil.which(t) is None]
    if missing:
        if os.environ.get("WYRD_REQUIRE_PG") == "1":
            pytest.fail(f"WYRD_REQUIRE_PG=1 but {missing} are not installed")
        pytest.skip(f"{missing} not installed")


def _script(name: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("WYRD_")}
    return subprocess.run(
        [str(BACKUP / name)], env=base | env, capture_output=True, text=True, check=False
    )


def test_backup_refuses_to_run_without_a_destination(tmp_path: Path) -> None:
    done = _script("wyrd-backup.sh", {"WYRD_ADMIN_DSN": "postgresql://x/y"})
    assert done.returncode != 0 and "WYRD_BACKUP_DEST is not set" in done.stderr


def test_restore_drill_fails_without_a_dump(tmp_path: Path) -> None:
    env = {"WYRD_ADMIN_DSN": "postgresql://x/y", "WYRD_BACKUP_DIR": str(tmp_path)}
    done = _script("wyrd-restore-drill.sh", env)
    assert done.returncode != 0 and "no dumps" in done.stderr


def test_backup_then_restore_drill(
    pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _tools()
    monkeypatch.setenv("WYRD_DB", pg_db.app_dsn)
    monkeypatch.setenv("WYRD_BRIEFINGS_DIR", str(tmp_path / "briefings"))
    monkeypatch.setattr(cli, "transport_factory", FakeClaude)
    assert runner.invoke(cli.app, ["new", "salt-strait"]).exit_code == 0
    for _ in range(2):
        assert runner.invoke(cli.app, ["tick", "--offline"]).exit_code == 0

    env = {
        "WYRD_ADMIN_DSN": pg_db.admin_dsn,
        "WYRD_BACKUP_DIR": str(tmp_path / "dumps"),
        "WYRD_BACKUP_DEST": str(tmp_path / "offbox"),
        "WYRD_BIN": str(Path(sys.executable).parent / "wyrd"),
    }
    (tmp_path / "offbox").mkdir()
    backup = _script("wyrd-backup.sh", env)
    assert backup.returncode == 0, backup.stderr
    dumps = sorted((tmp_path / "dumps").glob("wyrd-*.dump"))
    assert len(dumps) == 1 and (tmp_path / "offbox" / dumps[0].name).is_file()

    drill = _script("wyrd-restore-drill.sh", env)
    assert drill.returncode == 0, drill.stderr
    assert "ok " in drill.stdout and "tick 2" in drill.stdout
    assert "restore drill passed" in drill.stdout
    with_drill_dbs = subprocess.run(
        ["psql", "--no-psqlrc", "-tAc", "SELECT datname FROM pg_database", pg_db.admin_dsn],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "wyrd_restore_drill" not in with_drill_dbs.stdout  # the scratch copy is gone

    (tmp_path / "dumps" / "wyrd-99991231T000000Z.dump").write_bytes(b"not a dump")
    broken = _script("wyrd-restore-drill.sh", env)
    assert broken.returncode != 0 and "restore drill passed" not in broken.stdout
    after = subprocess.run(
        ["psql", "--no-psqlrc", "-tAc", "SELECT datname FROM pg_database", pg_db.admin_dsn],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "wyrd_restore_drill" not in after.stdout
