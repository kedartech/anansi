"""Layer 9a: the Discord bot on fake gateway objects and a fake REST API.

Slash-command callbacks are called with fake interactions and DMs with fake
messages; tick delivery goes to ``FakeDiscord``. No test opens a gateway
connection or touches the network, and no model is called (``FakeClaude``).
"""

import asyncio
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import psycopg
import pytest
from typer.testing import CliRunner

from revisionist import cli
from revisionist.core.fold import state_hash
from revisionist.core.ids import CampaignId, SeatId
from revisionist.engine import campaign as cmp
from revisionist.engine import play
from revisionist.engine import scheduler as sch
from revisionist.engine.discord import bot, handlers
from revisionist.engine.discord.delivery import routing_factory
from revisionist.engine.discord.handlers import BotConfig, Handlers
from revisionist.engine.discord.rest import DiscordRest
from revisionist.engine.discord.tables import LinkError, SeatLinks
from revisionist.engine.llm import Reply, Request, Transport, TransportError
from revisionist.engine.maintenance import copy_campaign
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.store import EventStore, load_state
from tests.discord_fakes import FakeDiscord
from tests.pg import PgDb
from tests.test_cli import FakeClaude
from tests.test_scheduler import T0, Clock

GUILD = "222222222222222222"
HOST = "333333333333333333"
PLAYER = HOST  # solo: the host plays the human seat
STRANGER = "444444444444444444"
TOKEN = "MTIz.fake-bot-token.SECRET-do-not-log"
ORDER = "Tell the world the strait stays open."
HIDDEN = ("CANARY-OSTREL-HARROW-4c1e", "CANARY-KESH-PACT-9b27", "CANARY-LUNE-CHANNEL-e803")
OWN = "CANARY-VARDA-SKIM-55d0"

runner = CliRunner()


# --- fake gateway objects -------------------------------------------------------


@dataclass
class FakeResponse:
    deferred: list[dict[str, bool]] = field(default_factory=list)

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self.deferred.append({"ephemeral": ephemeral, "thinking": thinking})


@dataclass
class FakeFollowup:
    sent: list[tuple[str, bool]] = field(default_factory=list)

    async def send(self, content: str, *, ephemeral: bool = False) -> None:
        self.sent.append((content, ephemeral))


@dataclass
class FakeInteraction:
    user_id: str
    response: FakeResponse = field(default_factory=FakeResponse)
    followup: FakeFollowup = field(default_factory=FakeFollowup)

    @property
    def user(self) -> SimpleNamespace:
        return SimpleNamespace(id=int(self.user_id))


@dataclass
class FakeChannel:
    sent: list[str] = field(default_factory=list)

    async def send(self, content: str) -> None:
        self.sent.append(content)


def _dm(user_id: str, content: str, *, is_bot: bool = False, guild: object = None) -> Any:
    author = SimpleNamespace(id=int(user_id), bot=is_bot)
    return SimpleNamespace(author=author, guild=guild, content=content, channel=FakeChannel())


class Game:
    """A bot over a Postgres campaign, with every outbound payload recorded."""

    def __init__(self, db: PgDb, transport: Transport, briefings: Path) -> None:
        self.db = db
        self.discord = FakeDiscord()
        self.clock = Clock()
        self.transport = transport
        rest = DiscordRest(TOKEN, http=self.discord, sleep=lambda s: None)
        self.handlers = Handlers(
            BotConfig(db.app_dsn, GUILD, HOST),
            transport_factory=lambda: self.transport,
            clock=self.clock,
            briefings=briefings,
            deliverer_factory=routing_factory(rest),
            rest=rest,
        )
        self.bot = bot.RevisionistBot(self.handlers, int(GUILD))
        self.replies: list[str] = []

    def command(self, name: str, user_id: str = PLAYER, **options: str) -> str:
        found = self.bot.tree.get_command(name, guild=self.bot.guild)
        assert isinstance(found, discord.app_commands.Command)
        interaction = FakeInteraction(user_id)
        callback: Any = found.callback
        asyncio.run(callback(interaction, **options))
        assert interaction.response.deferred == [{"ephemeral": True, "thinking": True}]
        assert all(ephemeral for _, ephemeral in interaction.followup.sent)
        text = "".join(content for content, _ in interaction.followup.sent)
        self.replies.append(text)
        return text

    def dm(self, user_id: str, content: str, **kw: Any) -> list[str]:
        message = _dm(user_id, content, **kw)
        asyncio.run(self.bot.on_message(message))
        self.replies += message.channel.sent
        sent: list[str] = message.channel.sent
        return sent

    def store(self) -> PostgresEventStore:
        return PostgresEventStore(self.db.app_dsn)

    def campaign(self) -> CampaignId:
        store = self.store()
        try:
            return store.campaign_ids()[0]
        finally:
            store.close()

    def last_seq(self) -> int:
        store = self.store()
        try:
            return store.last_seq(self.campaign())
        finally:
            store.close()

    def outbound(self) -> str:
        return "\n".join([*self.replies, self.discord.text()])


@pytest.fixture
def game(pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Game]:
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    monkeypatch.setenv("REVISIONIST_DISCORD_GUILD_ID", GUILD)
    monkeypatch.setenv("REVISIONIST_BRIEFINGS_DIR", str(tmp_path / "briefings"))
    monkeypatch.setattr(cli, "transport_factory", FakeClaude)
    assert runner.invoke(cli.app, ["new", "salt-strait"]).exit_code == 0
    result = runner.invoke(cli.app, ["discord", "link", "--user", PLAYER])
    assert result.exit_code == 0, result.output
    yield Game(pg_db, FakeClaude(), tmp_path / "briefings")


# --- intents and commands -------------------------------------------------------


def test_the_client_asks_for_default_intents_only(tmp_path: Path) -> None:
    h = Handlers(
        BotConfig("postgresql://unused/db", GUILD, HOST),
        transport_factory=FakeClaude,
        clock=Clock(),
        briefings=tmp_path,
        deliverer_factory=None,
    )
    client = bot.RevisionistBot(h, int(GUILD))
    assert client.intents.value == discord.Intents.default().value
    assert not client.intents.message_content
    assert not client.intents.members
    assert not client.intents.presences
    names = {c.name for c in client.tree.get_commands(guild=client.guild)}
    assert names == set(bot.COMMANDS)
    assert client.tree.get_commands() == []  # guild commands only, nothing global


# --- parity with the CLI --------------------------------------------------------


@pytest.mark.parametrize(
    ("visibility", "spin"),
    [
        ("private", None),
        ("public", "The strait stays open."),
        ("covert", None),
        ("covert", "Calm."),
    ],
)
def test_an_order_from_discord_files_what_the_cli_files(
    pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, visibility: str, spin: str | None
) -> None:
    """One campaign, two copies: the CLI orders on one, the bot on the other."""
    sqlite = tmp_path / "solo.db"
    monkeypatch.setenv("REVISIONIST_DB", str(sqlite))
    monkeypatch.setattr(cli, "transport_factory", FakeClaude)
    assert runner.invoke(cli.app, ["new", "salt-strait"]).exit_code == 0
    copy = runner.invoke(cli.app, ["db", "copy", "--from", str(sqlite), "--to", pg_db.app_dsn])
    assert copy.exit_code == 0, copy.output
    twin = tmp_path / "twin.db"
    source = cmp.open_store(sqlite)
    assert copy_campaign(source, cmp.open_store(twin), source.campaign_ids()[0]).verified.ok

    monkeypatch.setenv("REVISIONIST_DB", str(twin))
    extra = ["--spin", spin] if spin else []
    ordered = runner.invoke(cli.app, ["order", ORDER, "--visibility", visibility, *extra])
    assert ordered.exit_code == 0, ordered.output
    cli_store = cmp.open_store(twin)

    game = Game(pg_db, FakeClaude(), tmp_path / "briefings")
    pg = game.store()
    campaign_id = pg.campaign_ids()[0]
    SeatLinks(pg.conn).link(campaign_id, cmp.human_seat(load_state(pg, campaign_id)), PLAYER, GUILD)
    options = {"spin": spin} if spin else {}
    reply = game.command("order", text=ORDER, visibility=visibility, **options)
    assert reply.startswith("filed ") and reply.endswith(f"({visibility})")
    assert reply == ordered.output.strip()

    def outcome(store: EventStore) -> tuple[list[dict[str, Any]], str]:
        state = load_state(store, campaign_id)
        return [i.model_dump() for i in state.pending.values()], state_hash(state)

    assert outcome(pg) == outcome(cli_store)
    [filed] = load_state(pg, campaign_id).pending.values()
    assert filed.scope.visibility.value == visibility and filed.spin == spin
    pg.close()


# --- identity -------------------------------------------------------------------


def test_an_unlinked_user_is_refused_and_nothing_is_written(game: Game) -> None:
    before = game.last_seq()
    assert game.command("order", STRANGER, text=ORDER) == handlers.NOT_LINKED
    assert game.dm(STRANGER, ORDER) == [handlers.NOT_LINKED]
    assert game.command("briefing", STRANGER) == handlers.NOT_LINKED
    assert game.command("status", STRANGER) == handlers.NOT_LINKED
    assert game.command("bug", STRANGER, notes="hm") == handlers.NOT_LINKED
    assert game.command("advance", STRANGER) == handlers.HOST_ONLY
    assert game.command("pause", STRANGER) == handlers.HOST_ONLY
    assert game.last_seq() == before
    assert game.transport.calls == []  # type: ignore[attr-defined]
    with psycopg.connect(game.db.app_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM discord_bugs").fetchone() == (0,)


def test_the_seat_comes_from_the_link_not_the_text(game: Game) -> None:
    store = game.store()
    state = load_state(store, game.campaign())
    store.close()
    faction = cmp.faction_seats(state)[0]
    reply = game.command("order", text=f"As seat {faction}: declare the strait closed.")
    assert reply.startswith("filed ")
    store = game.store()
    pending = list(load_state(store, game.campaign()).pending.values())
    store.close()
    assert [i.actor for i in pending] == [cmp.human_seat(state)]


def test_linking_rules(game: Game) -> None:
    store = game.store()
    campaign_id = game.campaign()
    state = load_state(store, campaign_id)
    faction = cmp.faction_seats(state)[0]
    result = runner.invoke(cli.app, ["discord", "link", "--user", STRANGER, "--seat", faction])
    assert result.exit_code == cli.EXIT_REFUSED and "not a human seat" in result.output
    # two users cannot bind one seat
    result = runner.invoke(cli.app, ["discord", "link", "--user", STRANGER])
    assert result.exit_code == cli.EXIT_REFUSED and "already linked" in result.output
    # one user cannot bind two seats (the table refuses even what the CLI would not try)
    links = SeatLinks(store.conn)
    with pytest.raises(LinkError, match="already holds a seat"):
        links.link(campaign_id, SeatId(faction), PLAYER, GUILD)
    store.close()
    assert PLAYER in runner.invoke(cli.app, ["discord", "seats"]).output
    assert runner.invoke(cli.app, ["discord", "unlink"]).exit_code == 0
    assert game.command("status") == handlers.NOT_LINKED
    assert runner.invoke(cli.app, ["discord", "unlink"]).exit_code == cli.EXIT_ERROR


def test_the_bot_ignores_bots_and_guild_messages(game: Game) -> None:
    before = game.last_seq()
    assert game.dm(PLAYER, ORDER, is_bot=True) == []
    assert game.dm(PLAYER, ORDER, guild=object()) == []
    assert game.dm(PLAYER, "   ") == []
    assert game.last_seq() == before


# --- the commands ---------------------------------------------------------------


def test_a_dm_is_an_order(game: Game) -> None:
    [reply] = game.dm(PLAYER, ORDER)
    assert reply.startswith("filed ") and "declare" in reply
    assert "pending" in game.command("status")


def test_briefing_status_and_stubs(game: Game) -> None:
    out = game.command("briefing")
    assert "Dilemma: Toll or trade?" in out and "  3. c" in out
    assert "tick 0" in game.command("status")
    assert game.command("report") == handlers.NOT_YET
    assert game.command("key") == handlers.NOT_YET


def test_bug_pins_the_moment(game: Game) -> None:
    assert game.command("bug", notes="the toll looks wrong").startswith("Bug 1 filed at tick 0")
    store = game.store()
    state = load_state(store, game.campaign())
    store.close()
    with psycopg.connect(game.db.app_dsn) as conn:
        row = conn.execute("SELECT seq, state_hash, notes FROM discord_bugs").fetchone()
    assert row == (state.last_seq, state_hash(state), "the toll looks wrong")


def test_advance_ticks_and_delivers_by_dm(game: Game) -> None:
    assert game.command("advance") == "ticked: tick 0 resolved (advance)"
    assert [m.channel_id for m in game.discord.messages] == [f"dm-{PLAYER}"]
    assert game.command("pause") == "Paused. No ticks until /resume."
    assert game.command("advance").startswith("paused")
    assert game.command("resume") == "Resumed."


def test_a_failing_model_gets_a_short_out_of_character_reply(game: Game) -> None:
    class Down(FakeClaude):
        def call(self, request: Request) -> Reply:
            raise TransportError(f"claude cli exit 1 with token {TOKEN}")

    game.transport = Down()
    assert game.command("order", text=ORDER) == handlers.MODEL_DOWN
    assert game.command("briefing") == handlers.MODEL_DOWN
    assert "Traceback" not in game.outbound() and TOKEN not in game.outbound()


def test_a_crash_gets_no_stack_trace(game: Game, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object) -> None:
        raise RuntimeError(f"secret internals {TOKEN}")

    monkeypatch.setattr(play, "status_lines", boom)
    assert game.command("status") == handlers.BROKEN
    assert TOKEN not in game.outbound()


# --- the canary across every outbound path --------------------------------------


class Leaky(FakeClaude):
    """Plants ``canary`` in the briefing, a clarifying question and the narration."""

    def __init__(self, canary: str) -> None:
        super().__init__(narration=f"Rumour: {canary}")
        self.canary = canary

    def call(self, request: Request) -> Reply:
        reply = super().call(request)
        if request.call_class == "brief":
            out = {**reply.output, "text": f"Word is {self.canary}."}
        elif request.call_class == "structure":
            out = {"kind": "question", "question": f"Do you mean {self.canary}?"}
        else:
            return reply
        return Reply(out, reply.model, 1, 1, 0, 0.0)


@pytest.mark.parametrize("canary", HIDDEN)
def test_a_hidden_canary_never_leaves_by_discord(game: Game, canary: str) -> None:
    game.transport = Leaky(canary)
    game.command("briefing")
    game.command("status")
    game.command("order", text=ORDER)
    game.dm(PLAYER, ORDER)
    game.command("advance")
    out = game.outbound()
    assert canary not in out
    assert all(c not in out for c in HIDDEN)
    assert game.discord.posts() == []  # the leaking narration was withheld, not sent
    assert "Canary leak" in game.replies[-1]


def test_the_owner_still_reads_their_own_secret(game: Game) -> None:
    game.transport = Leaky(OWN)
    assert OWN in game.command("briefing")
    game.command("advance")
    assert OWN in game.discord.text()


# --- /advance against the timer -------------------------------------------------


def test_advance_racing_the_scheduler_ticks_once(game: Game) -> None:
    """The timer's run is mid-tick when /advance lands: one tick, not two."""
    narrating, release = threading.Event(), threading.Event()

    class Slow(FakeClaude):
        def call(self, request: Request) -> Reply:
            if request.call_class == "narrator":
                narrating.set()
                assert release.wait(30)
            return super().call(request)

    clock = Clock()
    clock.now = T0
    store = game.store()
    sch.register(store, clock())
    clock.now = T0 + timedelta(hours=25)  # the timer is due
    outcomes: list[sch.Outcome] = []

    def timer() -> None:
        outcomes.extend(
            sch.run(
                store,
                clock,
                offline=False,
                transport_factory=Slow,
                briefings=game.handlers.briefings,
                warn=print,
                deliverer_factory=game.handlers.deliverer_factory,
            )
        )

    thread = threading.Thread(target=timer)
    thread.start()
    assert narrating.wait(30)
    assert game.command("advance") == "A tick is already running."
    release.set()
    thread.join(30)
    store.close()
    assert [o.action for o in outcomes] == [sch.Action.TICKED]
    store = game.store()
    closed = [e.tick for e in store.read(game.campaign()) if e.kind == "tick_closed"]
    store.close()
    assert closed == [0]
    assert len(game.discord.messages) == 1


# --- import isolation -----------------------------------------------------------


def test_the_cli_and_an_offline_tick_need_no_discord(tmp_path: Path) -> None:
    code = (
        "import sys; sys.modules['discord'] = None\n"
        "from typer.testing import CliRunner\n"
        "from revisionist import cli\n"
        "r = CliRunner()\n"
        "assert r.invoke(cli.app, ['new', 'salt-strait']).exit_code == 0\n"
        "res = r.invoke(cli.app, ['tick', '--offline'])\n"
        "assert res.exit_code == 0, res.output\n"
        "assert 'discord' not in sys.modules or sys.modules['discord'] is None\n"
    )
    env = {"PATH": "/usr/bin:/bin", "REVISIONIST_DB": str(tmp_path / "solo.db")}
    env["REVISIONIST_DISCORD_TOKEN"] = TOKEN  # set, but SQLite: files as before
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_discord_run_needs_postgres(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REVISIONIST_DB", str(tmp_path / "solo.db"))
    monkeypatch.setenv("REVISIONIST_DISCORD_TOKEN", TOKEN)
    monkeypatch.setenv("REVISIONIST_DISCORD_GUILD_ID", GUILD)
    result = runner.invoke(cli.app, ["discord", "run"])
    assert result.exit_code == cli.EXIT_ERROR and "postgresql://" in result.output
    assert TOKEN not in result.output
    monkeypatch.delenv("REVISIONIST_DISCORD_TOKEN")
    result = runner.invoke(cli.app, ["discord", "run"])
    assert (
        result.exit_code == cli.EXIT_ERROR
        and "REVISIONIST_DISCORD_TOKEN is not set" in result.output
    )
