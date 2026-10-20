"""The core package must stay pure (spec §3 #34, build plan Layer 0).

Any module under ``revisionist.core`` that imports an I/O, network, clock, model
or unseeded-randomness module fails this test.
"""

import ast
from pathlib import Path

import revisionist.core

FORBIDDEN_ROOTS = {
    "anthropic",
    "asyncio",
    "discord",
    "httpx",
    "os",
    "psycopg",
    "random",
    "requests",
    "secrets",
    "socket",
    "sqlite3",
    "subprocess",
    "sys",
    "threading",
    "time",
    "urllib",
    "revisionist.engine",
    "revisionist.cli",
}

CORE_DIR = Path(revisionist.core.__file__).parent


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _is_forbidden(name: str) -> bool:
    return any(name == root or name.startswith(root + ".") for root in FORBIDDEN_ROOTS)


def test_core_has_no_forbidden_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for path in sorted(CORE_DIR.rglob("*.py")):
        bad = {name for name in _imports(path) if _is_forbidden(name)}
        if bad:
            offenders[str(path.relative_to(CORE_DIR))] = bad
    assert not offenders, f"impure imports in revisionist.core: {offenders}"


def test_purity_gate_catches_a_violation(tmp_path: Path) -> None:
    sample = tmp_path / "bad.py"
    sample.write_text("import os\nfrom anthropic import Anthropic\n")
    assert {name for name in _imports(sample) if _is_forbidden(name)} == {"os", "anthropic"}
