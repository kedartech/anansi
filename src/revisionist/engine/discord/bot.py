"""The gateway bot: slash commands and DMs in, ``Handlers`` replies out.

Default intents only. Orders come through slash commands and DMs, and Discord
delivers DM text without the Message Content intent. Every interaction is
deferred at once (Discord wants a first answer within 3 seconds) and answered
ephemerally by followup once the handler, on a worker thread, is done. The
bot has no tools, no memory and no model call of its own.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Literal

import discord
from discord import app_commands

from revisionist.engine.discord.handlers import BROKEN, NOT_YET, Handlers
from revisionist.engine.discord.rest import split_message

log = logging.getLogger("revisionist.discord")

COMMANDS = (
    "order",
    "briefing",
    "dispatch",
    "status",
    "join",
    "establish",
    "advance",
    "pause",
    "resume",
    "bug",
    "report",
    "key",
)


async def _run(work: Callable[[], str]) -> str:
    try:
        return await asyncio.to_thread(work)
    except Exception:  # Handlers catch their own; this is the last net
        log.exception("handler crashed")
        return BROKEN


async def answer(interaction: discord.Interaction, work: Callable[[], str]) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    for part in split_message(await _run(work)) or ["(nothing to show)"]:
        await interaction.followup.send(part, ephemeral=True)


def register(
    tree: app_commands.CommandTree[discord.Client], h: Handlers, guild: discord.Object
) -> None:
    @tree.command(name="order", description="Give your agent an order", guild=guild)
    @app_commands.describe(
        text="What you want done, in your words",
        visibility="Who may learn of this order (default: private)",
        spin="A public line on the order, published when the tick freezes",
    )
    async def order(
        interaction: discord.Interaction,
        text: app_commands.Range[str, 1, 4000],
        visibility: Literal["private", "public", "covert"] = "private",
        spin: app_commands.Range[str, 1, 280] | None = None,
    ) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.order(user, text, visibility, spin))

    @tree.command(name="briefing", description="Your agent's briefing", guild=guild)
    async def briefing(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.briefing(user))

    @tree.command(name="dispatch", description="Your latest tick briefing", guild=guild)
    async def dispatch(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.dispatch(user))

    @tree.command(
        name="join", description="Take your seat with the host's invite code", guild=guild
    )
    @app_commands.describe(code="The code the host gave you")
    async def join(interaction: discord.Interaction, code: app_commands.Range[str, 1, 64]) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.join(user, code))

    @tree.command(
        name="status", description="Where things stand, as your seat sees it", guild=guild
    )
    async def status(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.status(user))

    @tree.command(name="establish", description="Host: lock a point in history", guild=guild)
    @app_commands.describe(place='Place and date, e.g. "Florence, April 1478"')
    async def establish(
        interaction: discord.Interaction, place: app_commands.Range[str, 1, 80]
    ) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.establish(user, place))

    @tree.command(name="advance", description="Host: run the next tick now", guild=guild)
    async def advance(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.advance(user))

    @tree.command(name="pause", description="Host: stop ticking", guild=guild)
    async def pause(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.pause(user))

    @tree.command(name="resume", description="Host: tick again", guild=guild)
    async def resume(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.resume(user))

    @tree.command(name="bug", description="Report a bug, pinned to this moment", guild=guild)
    @app_commands.describe(notes="What went wrong")
    async def bug(
        interaction: discord.Interaction, notes: app_commands.Range[str, 1, 4000]
    ) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.bug(user, notes))

    @tree.command(name="report", description="Not available yet", guild=guild)
    async def report(interaction: discord.Interaction) -> None:
        await answer(interaction, lambda: NOT_YET)

    @tree.command(name="key", description="Not available yet", guild=guild)
    async def key(interaction: discord.Interaction) -> None:
        await answer(interaction, lambda: NOT_YET)


class RevisionistBot(discord.Client):
    def __init__(self, handlers: Handlers, guild_id: int) -> None:
        super().__init__(
            intents=discord.Intents.default(), allowed_mentions=discord.AllowedMentions.none()
        )
        self.handlers = handlers
        self.guild = discord.Object(id=guild_id)
        self.tree: app_commands.CommandTree[discord.Client] = app_commands.CommandTree(self)
        register(self.tree, handlers, self.guild)

    async def setup_hook(self) -> None:
        await self.tree.sync(guild=self.guild)

    async def on_message(self, message: discord.Message) -> None:
        """A plain DM to the bot is an order. Guild messages and bots are ignored."""
        if message.guild is not None or message.author.bot or not message.content.strip():
            return
        if self.user is not None and message.author.id == self.user.id:
            return
        user, text = str(message.author.id), message.content
        for part in split_message(await _run(lambda: self.handlers.order(user, text))):
            await message.channel.send(part)


def run(handlers: Handlers, token: str, guild_id: int) -> None:
    """Connect and serve until stopped. Blocks."""
    RevisionistBot(handlers, guild_id).run(token)


__all__ = ["COMMANDS", "RevisionistBot", "answer", "register", "run"]
