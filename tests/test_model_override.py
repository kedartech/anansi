"""Per-call model override: ``REVISIONIST_MODEL_<CALL>`` picks the model for the Claude CLI only."""

import json
import logging
import subprocess
from typing import Any

import pytest

from revisionist.engine import llm
from revisionist.engine.llm import ClaudeCli, Request


def _run(monkeypatch: pytest.MonkeyPatch, request: Request) -> tuple[list[str], dict[str, Any]]:
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen["argv"] = argv
        seen.update(kw)
        out = json.dumps({"structured_output": {"text": "ok"}, "usage": {}, "modelUsage": {}})
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ClaudeCli().call(request)
    return seen["argv"], seen


def _model_arg(argv: list[str]) -> str:
    return argv[argv.index("--model") + 1]


def _request(call_class: str, model: str) -> Request:
    return Request(call_class, model, "SYSTEM", "PROMPT", {"type": "object"})


def test_override_replaces_the_model_for_its_call_class_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REVISIONIST_MODEL_RESOLVER", "claude-haiku-5-5")

    argv, _ = _run(monkeypatch, _request("resolver", "claude-sonnet-5-5"))
    assert _model_arg(argv) == "claude-haiku-5-5"

    argv, _ = _run(monkeypatch, _request("narrator", "claude-sonnet-5-5"))
    assert _model_arg(argv) == "claude-sonnet-5-5"

    monkeypatch.setenv("REVISIONIST_MODEL_NARRATOR", "claude-sonnet-5-5")
    argv, _ = _run(monkeypatch, _request("narrator", "claude-haiku-5-5"))
    assert _model_arg(argv) == "claude-sonnet-5-5"


def test_call_class_with_underscores_maps_to_its_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVISIONIST_MODEL_SESSION_ZERO", "claude-haiku-5-5")
    argv, _ = _run(monkeypatch, _request("session_zero", "claude-sonnet-5-5"))
    assert _model_arg(argv) == "claude-haiku-5-5"


def test_a_value_that_is_not_a_claude_model_is_ignored_and_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("REVISIONIST_MODEL_RESOLVER", "gpt-4")

    with caplog.at_level(logging.WARNING, logger=llm.__name__):
        argv, _ = _run(monkeypatch, _request("resolver", "claude-sonnet-5-5"))

    assert _model_arg(argv) == "claude-sonnet-5-5"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "REVISIONIST_MODEL_RESOLVER" in warnings[0].getMessage()
    assert "gpt-4" not in caplog.text


def test_the_child_env_still_has_no_revisionist_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVISIONIST_MODEL_RESOLVER", "claude-haiku-5-5")
    monkeypatch.setenv("REVISIONIST_DB", "postgresql://app:pw@h/db")

    _, seen = _run(monkeypatch, _request("resolver", "claude-sonnet-5-5"))

    assert not any(k.startswith("REVISIONIST_") for k in seen["env"])


def test_unset_override_leaves_the_request_model(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("REVISIONIST_MODEL_RESOLVER", raising=False)

    with caplog.at_level(logging.WARNING, logger=llm.__name__):
        argv, _ = _run(monkeypatch, _request("resolver", "claude-sonnet-5-5"))

    assert argv[argv.index("--model") : argv.index("--model") + 2] == [
        "--model",
        "claude-sonnet-5-5",
    ]
    assert not caplog.records
