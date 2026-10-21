"""The gateway bot: slash commands and DMs in, ``Handlers`` replies out.

Default intents only. Orders come through slash commands and DMs, and Discord
delivers DM text without the Message Content intent. Every interaction is
deferred at once (Discord wants a first answer within 3 seconds) and answered
ephemerally by followup once the handler, on a worker thread, is done. The
table-wide lines of a ``Said`` reply follow, posted to the channel. The bot has
no tools, no memory and no model call of its own.
"""

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from typing import Literal

import discord
from discord import app_commands

from revisionist.core.ontology import Persona
from revisionist.engine.discord.handlers import BROKEN, DRAFTING, NOT_YET, Fenced, Handlers, Said
from revisionist.engine.discord.rest import PART_LIMIT, split_message

log = logging.getLogger("revisionist.discord")
LONG_REPLY_S = 60.0
"""A reply slower than this is also DMed, so the host can read it after the token expires."""

COMMANDS = (
    "order",
    "briefing",
    "dispatch",
    "status",
    "join",
    "establish",
    "retire",
    "invite",
    "channel",
    "character",
    "goal",
    "history",
    "agent",
    "begin",
    "dashboard",
    "season-end",
    "advance",
    "pause",
    "resume",
    "host",
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


async def answer(
    interaction: discord.Interaction,
    work: Callable[[], str],
    *,
    notify: Callable[[str], object] | None = None,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    started = time.monotonic()
    text = await _run(work)
    took = time.monotonic() - started
    delivered = await _reply(interaction, text)
    if notify is not None and (not delivered or took > LONG_REPLY_S):
        await asyncio.to_thread(notify, "In case you missed it:\n" + str(text))


async def _send(interaction: discord.Interaction, text: str, *, ephemeral: bool) -> bool:
    try:
        await interaction.followup.send(text, ephemeral=ephemeral)
    except discord.HTTPException as exc:
        log.warning("follow-up not delivered: HTTP %s, code %s", exc.status, exc.code)
        return False
    return True


async def _reply(interaction: discord.Interaction, text: str) -> bool:
    if isinstance(text, Fenced):
        parts = [f"```\n{p}\n```" for p in split_message(text, PART_LIMIT - 8)]
    else:
        parts = split_message(text)
    for part in parts or ["(nothing to show)"]:
        if not await _send(interaction, part, ephemeral=True):
            return False
    if isinstance(text, Said) and text.public:
        for part in split_message(text.public):
            if not await _send(interaction, part, ephemeral=False):
                break
    return True


def register(
    tree: app_commands.CommandTree[discord.Client], h: Handlers, guild: discord.Object
) -> None:
    def dm(user: str) -> Callable[[str], object]:
        return lambda text: h.notify(user, text, f"reply:{user}:{secrets.token_hex(6)}")

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
        await answer(interaction, lambda: h.order(user, text, visibility, spin), notify=dm(user))

    @tree.command(name="briefing", description="Your agent's briefing", guild=guild)
    async def briefing(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.briefing(user), notify=dm(user))

    @tree.command(name="dispatch", description="Your latest tick briefing", guild=guild)
    async def dispatch(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.dispatch(user), notify=dm(user))

    @tree.command(
        name="join", description="Take your seat with the host's invite code", guild=guild
    )
    @app_commands.describe(code="The code the host gave you")
    async def join(interaction: discord.Interaction, code: app_commands.Range[str, 1, 64]) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.join(user, code), notify=dm(user))

    @tree.command(
        name="status", description="Where things stand, as your seat sees it", guild=guild
    )
    async def status(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.status(user), notify=dm(user))

    @tree.command(name="establish", description="Host: lock a point in history", guild=guild)
    @app_commands.describe(place='Place and date, e.g. "Florence, April 1478"')
    async def establish(
        interaction: discord.Interaction, place: app_commands.Range[str, 1, 80]
    ) -> None:
        user = str(interaction.user.id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        refused = await _run(lambda: h.establish_refusal(user))
        if refused:
            await _reply(interaction, refused)
            return
        # The draft is one long model call; say so before it starts.
        await _send(interaction, DRAFTING.format(place=place.strip()), ephemeral=True)
        result = await _run(lambda: h.establish(user, place))
        await _reply(interaction, result)
        await asyncio.to_thread(
            h.notify,
            user,
            f"Your /establish for {place.strip()}:\n{result!s}",
            f"establish:{user}:{secrets.token_hex(6)}",
        )

    @tree.command(name="retire", description="Host: set a game aside, no reveal", guild=guild)
    @app_commands.describe(campaign="The game's ID or name; leave empty to list live games")
    async def retire(
        interaction: discord.Interaction, campaign: app_commands.Range[str, 1, 80] | None = None
    ) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.retire(user, campaign or ""), notify=dm(user))

    @tree.command(
        name="invite", description="Host: a one-time /join code for a player", guild=guild
    )
    @app_commands.describe(seat="Only in play: the seat to invite into, by name")
    async def invite(
        interaction: discord.Interaction, seat: app_commands.Range[str, 1, 80] | None = None
    ) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.invite(user, seat or ""), notify=dm(user))

    @tree.command(name="channel", description="Host: post public news in this channel", guild=guild)
    async def channel(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        where, in_guild = str(interaction.channel_id), interaction.guild_id is not None
        perms = getattr(interaction, "app_permissions", None)
        can_post = perms is None or (perms.view_channel and perms.send_messages)
        await answer(
            interaction, lambda: h.channel(user, where, in_guild, can_post), notify=dm(user)
        )

    @tree.command(name="character", description="Session Zero: choose who you play", guild=guild)
    @app_commands.describe(
        action="roster, search for a fit, claim from the roster, or pick anyone from the record",
        text="What you want to play (search) or a name (claim, pick)",
    )
    async def character(
        interaction: discord.Interaction,
        action: Literal["roster", "search", "claim", "pick"],
        text: app_commands.Range[str, 1, 400] | None = None,
    ) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.character(user, action, text or ""), notify=dm(user))

    @tree.command(name="goal", description="Session Zero: choose your secret goal", guild=guild)
    @app_commands.describe(
        action="as history went, as it could have gone, your own, or confirm a label (H1, C2)",
        text="Your own goal, or the label to confirm",
    )
    async def goal(
        interaction: discord.Interaction,
        action: Literal["history", "could_have", "own", "confirm"],
        text: app_commands.Range[str, 1, 280] | None = None,
    ) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.goal(user, action, text or ""), notify=dm(user))

    @tree.command(
        name="history", description="Session Zero: your brief from the record", guild=guild
    )
    async def history(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.history(user), notify=dm(user))

    @tree.command(name="agent", description="Session Zero: design your agent", guild=guild)
    async def agent(
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 60],
        role: app_commands.Range[str, 1, 60],
        voice: app_commands.Range[str, 1, 200] | None = None,
        backstory: app_commands.Range[str, 1, 400] | None = None,
        relationship: app_commands.Range[str, 1, 200] | None = None,
    ) -> None:
        user = str(interaction.user.id)
        persona = Persona(
            name=name,
            role=role,
            voice=voice or "",
            backstory=backstory or "",
            relationship=relationship or "",
        )
        await answer(interaction, lambda: h.agent(user, persona), notify=dm(user))

    @tree.command(name="begin", description="Host: close Session Zero and start play", guild=guild)
    async def begin(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.begin(user), notify=dm(user))

    @tree.command(name="dashboard", description="Your standing, means and goal", guild=guild)
    async def dashboard(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.dashboard(user), notify=dm(user))

    @tree.command(name="season-end", description="Host: end the season, reveal all", guild=guild)
    async def season_end(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.season_end(user), notify=dm(user))

    @tree.command(name="advance", description="Host: run the next tick now", guild=guild)
    async def advance(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.advance(user), notify=dm(user))

    @tree.command(name="pause", description="Host: stop ticking", guild=guild)
    async def pause(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.pause(user), notify=dm(user))

    @tree.command(name="resume", description="Host: tick again", guild=guild)
    async def resume(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.resume(user), notify=dm(user))

    host = app_commands.Group(name="host", description="Host tools")

    @host.command(name="seats", description="Every seat bound to a Discord user")
    async def host_seats(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.host_seats(user), notify=dm(user))

    @host.command(name="invites", description="Every invite issued, and whether it was used")
    async def host_invites(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.host_invites(user), notify=dm(user))

    @host.command(name="link", description="Bind a Discord user to a human seat")
    @app_commands.describe(user="The player's Discord account", seat="The seat, by name")
    async def host_link(
        interaction: discord.Interaction,
        user: discord.User,
        seat: app_commands.Range[str, 1, 80],
    ) -> None:
        caller = str(interaction.user.id)
        await answer(
            interaction, lambda: h.host_link(caller, str(user.id), seat), notify=dm(caller)
        )

    @host.command(name="unlink", description="Remove a seat's Discord binding")
    @app_commands.describe(seat="The seat, by name")
    async def host_unlink(
        interaction: discord.Interaction, seat: app_commands.Range[str, 1, 80]
    ) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.host_unlink(user, seat), notify=dm(user))

    @host.command(name="flags", description="Goals the balance check flagged")
    async def host_flags(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.host_flags(user), notify=dm(user))

    @host.command(name="approve", description="Let one flagged goal stand")
    @app_commands.describe(seat="The seat, by name")
    async def host_approve(
        interaction: discord.Interaction, seat: app_commands.Range[str, 1, 80]
    ) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.host_approve(user, seat), notify=dm(user))

    @host.command(name="schedule", description="When the live game ticks next")
    async def host_schedule(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.host_schedule(user), notify=dm(user))

    @host.command(name="cost", description="Model spend per tick and in total")
    async def host_cost(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.host_cost(user), notify=dm(user))

    @host.command(name="leaks", description="Scan for canaries any person could read")
    async def host_leaks(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.host_leaks(user), notify=dm(user))

    @host.command(name="why", description="Trace an event or intent through the log")
    @app_commands.describe(
        target="An event ID or an intent ID",
        reason="Why you read private records of a sealed game",
    )
    async def host_why(
        interaction: discord.Interaction,
        target: app_commands.Range[str, 1, 120],
        reason: app_commands.Range[str, 1, 200] | None = None,
    ) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.host_why(user, target, reason or ""), notify=dm(user))

    @host.command(name="replay", description="Rebuild the state at a past tick and hash it")
    @app_commands.describe(tick="The tick to rebuild, from 0")
    async def host_replay(
        interaction: discord.Interaction, tick: app_commands.Range[int, 0]
    ) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.host_replay(user, tick), notify=dm(user))

    @host.command(name="verify", description="Check the log replays to its recorded state")
    async def host_verify(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.host_verify(user), notify=dm(user))

    tree.add_command(host, guild=guild)

    @tree.command(name="bug", description="Report a bug, pinned to this moment", guild=guild)
    @app_commands.describe(notes="What went wrong")
    async def bug(
        interaction: discord.Interaction, notes: app_commands.Range[str, 1, 4000]
    ) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.bug(user, notes), notify=dm(user))

    @tree.command(name="report", description="Your season report, once it ends", guild=guild)
    async def report(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: h.report(user), notify=dm(user))

    @tree.command(name="key", description="Not available yet", guild=guild)
    async def key(interaction: discord.Interaction) -> None:
        user = str(interaction.user.id)
        await answer(interaction, lambda: NOT_YET, notify=dm(user))


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
