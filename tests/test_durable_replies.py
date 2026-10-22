"""Playtest #20: a slow or lost reply is also DMed, so the host can read it later.

``/establish`` takes minutes and its ephemeral followup dies with the client or the
15-minute token. The DM is the durable copy, read from ``Game.discord.messages``.
"""

import asyncio
import copy
import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from revisionist.engine.discord import bot
from revisionist.engine.discord.handlers import (
    DRAFT_REFUSED,
    NOT_LINKED,
    BotConfig,
    Handlers,
)
from tests.pg import PgDb
from tests.test_discord_bot import GUILD, HOST, STRANGER, FakeFollowup, FakeInteraction, Game
from tests.test_scheduler import Clock
from tests.test_session_zero import FLORENCE, GM, WASHINGTON, draft

HOST_DM = f"dm-{HOST}"


@pytest.fixture
def bare(pg_db: PgDb, tmp_path: Path) -> Iterator[Game]:
    """No live game, so ``/establish`` runs its draft."""
    yield Game(pg_db, GM(draft("florence-1478.yaml")), tmp_path / "briefings")


def _host_dms(game: Game) -> list[str]:
    return [m.content for m in game.discord.messages if m.channel_id == HOST_DM]


def _bad_washington() -> dict[str, Any]:
    bad = draft("washington-1962.yaml")
    bad["roster"] = bad["roster"][:3]
    return bad


def _invoke(
    client: bot.RevisionistBot, name: str, user: str, followup: Any, **options: str
) -> None:
    found = client.tree.get_command(name, guild=client.guild)
    assert isinstance(found, discord.app_commands.Command)
    callback: Any = found.callback
    asyncio.run(callback(FakeInteraction(user, followup=followup), **options))


class BrokenFollowup(FakeFollowup):
    async def send(self, content: str, *, ephemeral: bool = False) -> None:
        raise discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"),
            {"code": 10015, "message": "Unknown Webhook"},
        )


def test_a_refused_draft_is_dmed_to_the_host(pg_db: PgDb, tmp_path: Path) -> None:
    bad = _bad_washington()
    game = Game(pg_db, GM(bad, copy.deepcopy(bad)), tmp_path / "briefings")
    game.establish(HOST, WASHINGTON)

    assert any(DRAFT_REFUSED in text for text in _host_dms(game))
    status = game.command("status", HOST)
    assert status.startswith("Last /establish: Washington")
    assert "refused" in status and DRAFT_REFUSED in status


def test_a_compiled_draft_is_dmed_and_status_is_unchanged_for_a_stranger(
    bare: Game,
) -> None:
    bare.establish(HOST, FLORENCE)

    assert any("Session Zero is open at" in text for text in _host_dms(bare))
    assert bare.command("status", STRANGER) == NOT_LINKED


def test_a_slow_command_is_also_dmed(bare: Game, monkeypatch: pytest.MonkeyPatch) -> None:
    bare.establish(HOST, FLORENCE)
    monkeypatch.setattr(bot, "LONG_REPLY_S", -1.0)

    reply = bare.command("status", HOST)

    assert reply.startswith("Last /establish: Florence, April 1478, opened")
    assert any(text.startswith("In case you missed it:\n") for text in _host_dms(bare))


def test_a_fast_command_is_not_dmed(bare: Game) -> None:
    bare.establish(HOST, FLORENCE)
    before = len(_host_dms(bare))

    bare.command("status", HOST)

    assert len(_host_dms(bare)) == before
    assert not any("In case you missed it" in text for text in _host_dms(bare))


def test_a_failed_followup_does_not_stop_the_host_dm(pg_db: PgDb, tmp_path: Path) -> None:
    game = Game(pg_db, GM(draft("florence-1478.yaml")), tmp_path / "briefings")

    _invoke(game.bot, "establish", HOST, BrokenFollowup(), place=FLORENCE)

    assert any("Session Zero is open at" in text for text in _host_dms(game))


def test_no_rest_means_no_dm_and_no_error(pg_db: PgDb, tmp_path: Path) -> None:
    h = Handlers(
        BotConfig(pg_db.app_dsn, GUILD, HOST),
        transport_factory=lambda: GM(draft("florence-1478.yaml")),
        clock=Clock(),
        briefings=tmp_path,
        deliverer_factory=None,
        rest=None,
    )
    client = bot.RevisionistBot(h, int(GUILD))
    followup = FakeFollowup()

    _invoke(client, "establish", HOST, followup, place=FLORENCE)

    assert followup.sent[0][0].startswith("Drafting")
    assert any("Session Zero is open at" in content for content, _ in followup.sent)
    assert h.notify(HOST, "text", "key") is False


def test_a_closed_dm_returns_false_without_raising(bare: Game) -> None:
    bare.discord.closed_dms.add(HOST)

    assert bare.handlers.notify(HOST, "text", "key") is False
    reply = bare.establish(HOST, FLORENCE)

    assert reply.startswith("Drafting")
    assert _host_dms(bare) == []


def test_the_refused_draft_log_names_a_compile_problem(
    pg_db: PgDb, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = _bad_washington()
    game = Game(pg_db, GM(bad, copy.deepcopy(bad)), tmp_path / "briefings")
    caplog.set_level(logging.WARNING)

    game.establish(HOST, WASHINGTON)

    [record] = [r for r in caplog.records if "did not compile" in r.getMessage()]
    assert "roster: 3 characters, not 8-12" in record.getMessage()
