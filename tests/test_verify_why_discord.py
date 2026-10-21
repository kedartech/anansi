"""V2 probes: /host why on a sealed campaign must announce the admin read publicly."""

from types import SimpleNamespace
from typing import Any

import pytest

from revisionist.core import events as ev
from revisionist.engine.discord import handlers
from revisionist.engine.discord.handlers import Handlers
from revisionist.engine.discord.tables import Channels
from revisionist.engine.pg_store import PostgresEventStore
from tests.test_discord_bot import GUILD, HOST, Game
from tests.test_discord_host import salt  # noqa: F401


class AlwaysSealed:
    def sealed(self, campaign_id: Any) -> bool:
        return True

    def seal(self, events: Any) -> Any:
        return list(events)

    def unseal(self, event: Any) -> Any:
        return event


def test_why_with_reason_logs_and_announces(salt: Game, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    real_open = Handlers._open

    def sealed_open(self: Handlers) -> PostgresEventStore:
        store = real_open(self)
        store.sealer = AlwaysSealed()  # type: ignore[assignment]
        return store

    monkeypatch.setattr(Handlers, "_open", sealed_open)
    store = salt.store()
    events = list(store.read(salt.campaign()))
    store.close()
    private = next(e for e in events if e.scope.visibility.value != "public")
    store = salt.store()
    Channels(store.conn).set_public(salt.campaign(), "555", GUILD)
    store.close()
    posts_before = len(salt.discord.text())
    out = salt.handlers.host_why(HOST, str(private.event_id), "probe reason")
    store = salt.store()
    logged = [e for e in store.read(salt.campaign()) if isinstance(e.payload, ev.AdminRead)]
    store.close()
    assert logged, out
    assert len(salt.discord.text()) > posts_before, "admin read logged but not announced"
    _ = (SimpleNamespace, handlers)
