from revisionist import cli
from revisionist.core import events as ev
from tests.test_seal_pg import *  # noqa: F403
from tests.test_seal_pg import ORDER_MARK, _events_of, runner


def test_reason_alone_does_not_break_glass(sealed_game) -> None:  # type: ignore[no-untyped-def]
    [filed] = _events_of(ev.IntentFiled)
    intent_id = filed.payload.intent.intent_id  # type: ignore[union-attr]
    out = runner.invoke(cli.app, ["why", intent_id, "--reason", "just curious"])
    assert ORDER_MARK not in out.output
    assert _events_of(ev.AdminRead) == []
