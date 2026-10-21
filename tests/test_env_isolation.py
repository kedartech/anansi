"""A developer's shell must not change what the suite sees.

A sourced ``revisionist.env`` sets ``REVISIONIST_DB``, the Discord variables and
``REVISIONIST_BRIEFINGS_DIR``. ``tests/conftest.py`` clears them for every test;
this runs one CLI test in a child pytest with those variables set and checks
it still passes.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CALLER_ENV = {
    "REVISIONIST_DB": "postgresql://nobody@127.0.0.1:1/none",
    "REVISIONIST_DISCORD_TOKEN": "bogus",
    "REVISIONIST_DISCORD_GUILD_ID": "1",
    "REVISIONIST_DISCORD_HOST_ID": "1",
    "REVISIONIST_BRIEFINGS_DIR": "/nonexistent/briefings",
}


def test_cli_tick_passes_with_a_sourced_revisionist_env() -> None:
    env = {**os.environ, **CALLER_ENV}
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_cli.py::test_tick_runs_deputies_then_resolves",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
