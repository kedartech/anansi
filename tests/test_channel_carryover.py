"""The public channel carries over to the next game, and ``/channel`` checks it can post.

Playtest #3: each ``/establish`` used to start with no public channel, so ``#chronicle``
stayed silent until the host ran ``/channel`` again. The opening line now goes to the
channel the last game's news went to, and ``/channel`` warns when the bot cannot post there.
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import discord

from revisionist.engine.discord.handlers import DRAFTING
from revisionist.engine.discord.tables import Channels
from tests.pg import PgDb
from tests.test_discord_bot import HOST, FakeInteraction, Game
from tests.test_session_zero import FLORENCE, GM, WASHINGTON, draft

CHANNEL = "777777777777777777"
CANT_POST = "I can't post there yet: give me View Channel and Send Messages in this channel."


@dataclass
class WithPermissions(FakeInteraction):
    app_permissions: discord.Permissions = field(default_factory=discord.Permissions.all)


def test_the_public_channel_carries_over_to_the_next_game(pg_db: PgDb, tmp_path: Path) -> None:
    gm = GM(draft("florence-1478.yaml"), draft("washington-1962.yaml"))
    game = Game(pg_db, gm, tmp_path)
    game.establish(HOST, FLORENCE)
    store = game.store()
    try:
        first = next(iter(store.campaign_ids()))
    finally:
        store.close()
    assert game.command("channel", HOST).startswith("Public statements and the press")
    game.command("retire", HOST, campaign=first)
    public = list(game.public)

    reply = game.establish(HOST, WASHINGTON)

    assert reply.startswith(
        DRAFTING.format(place=WASHINGTON) + "Session Zero is open at Washington"
    )
    assert reply.endswith(f"News will post in <#{CHANNEL}>.")
    assert game.public == public  # the opening went out over REST, not as a follow-up
    posted = [m.channel_id for m in game.discord.messages if "open at Washington" in m.content]
    assert posted == [CHANNEL, f"dm-{HOST}"]
    store = game.store()
    try:
        second = next(c for c in store.campaign_ids() if c != first)
        assert Channels(store.conn).public(second) == CHANNEL
    finally:
        store.close()


def test_without_a_channel_the_host_is_asked_for_one(pg_db: PgDb, tmp_path: Path) -> None:
    game = Game(pg_db, GM(draft("florence-1478.yaml")), tmp_path)
    reply = game.establish(HOST, FLORENCE)
    assert reply.endswith("\nRun /channel in the channel where news should post.")
    assert not any(m.channel_id == CHANNEL for m in game.discord.messages)


def test_channel_warns_when_the_bot_cannot_post_there(pg_db: PgDb, tmp_path: Path) -> None:
    game = Game(pg_db, GM(draft("florence-1478.yaml")), tmp_path)
    game.establish(HOST, FLORENCE)

    warned = game.handlers.channel(HOST, CHANNEL, True, can_post=False)
    assert warned.startswith("Public statements and the press")
    assert warned.splitlines()[-1] == CANT_POST

    assert CANT_POST not in game.handlers.channel(HOST, CHANNEL, True)


def test_the_channel_command_checks_the_bot_can_post(pg_db: PgDb, tmp_path: Path) -> None:
    game = Game(pg_db, GM(draft("florence-1478.yaml")), tmp_path)
    game.establish(HOST, FLORENCE)
    found = game.bot.tree.get_command("channel", guild=game.bot.guild)
    assert isinstance(found, discord.app_commands.Command)
    callback: Any = found.callback

    blind = WithPermissions(
        HOST, app_permissions=discord.Permissions(view_channel=True, send_messages=False)
    )
    asyncio.run(callback(blind))
    assert CANT_POST in "".join(c for c, ephemeral in blind.followup.sent if ephemeral)

    able = WithPermissions(
        HOST, app_permissions=discord.Permissions(view_channel=True, send_messages=True)
    )
    asyncio.run(callback(able))
    assert CANT_POST not in "".join(c for c, ephemeral in able.followup.sent if ephemeral)
