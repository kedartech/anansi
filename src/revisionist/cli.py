"""Command-line entry point (build plan Layer 7).

The solo slice is played from here: ``revisionist new salt-strait``, then
``revisionist briefing``, ``revisionist order "..."``, ``revisionist tick``, repeat. The
campaign lives in ``$REVISIONIST_DB`` (default ``./revisionist.db``, or a ``postgresql://``
URL); every command works on the newest campaign there. ``revisionist db`` holds the
Postgres maintenance commands and ``revisionist scheduler`` the unattended ticks
(Layer 8).
"""

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from revisionist import __version__
from revisionist.core.canary import HumanLeak, scan_humans, surfaces_from_log
from revisionist.core.fold import replay_to, state_hash
from revisionist.core.ids import CampaignId, SeatId
from revisionist.core.ontology import Controller
from revisionist.core.report import (
    cost_report,
    leak_report,
    render_cost,
    render_leaks,
    render_why,
    why,
)
from revisionist.core.state import State
from revisionist.core.visibility import Visibility
from revisionist.core.world_compile import WorldError
from revisionist.engine import campaign as cmp
from revisionist.engine import play
from revisionist.engine import scheduler as sch
from revisionist.engine.llm import Transport, TransportError
from revisionist.engine.maintenance import copy_campaign, verify_campaign
from revisionist.engine.runner import DelivererFactory, play_tick
from revisionist.engine.store import EventStore, StoreError, load_state

if TYPE_CHECKING:
    from revisionist.engine.pg_store import PostgresEventStore

app = typer.Typer(no_args_is_help=True, add_completion=False)

transport_factory: Callable[[], Transport] = cmp.live_transport
"""Swapped for a fake or recorded transport in tests
(``revisionist.cli.transport_factory = ...``)."""

clock: Callable[[], datetime] = sch.utc_now
"""The scheduler's clock. Tests step a fake one; nothing sleeps."""

Offline = Annotated[
    bool, typer.Option("--offline", help="No model calls: fixture resolver and narrator.")
]

EXIT_ERROR = 1
EXIT_REFUSED = 2
EXIT_LEAK = 3


def _fail(message: str, code: int = EXIT_ERROR) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


def _campaign(store: EventStore) -> CampaignId:
    try:
        return cmp.current_campaign(store)
    except LookupError as exc:
        raise _fail(str(exc)) from exc


def _me(state: State, seat: str | None = None) -> SeatId:
    """``seat`` when given (by ID or name, and it must be human), else the one human seat."""
    try:
        if seat is None:
            return cmp.human_seat(state)
        found = state.seats[cmp.resolve_seat(state, seat)]
    except LookupError as exc:
        raise _fail(str(exc)) from exc
    if found.controller is not Controller.HUMAN:
        raise _fail(f"{found.name} is not a human seat", EXIT_REFUSED)
    return found.seat_id


PlayerSeat = Annotated[
    str | None,
    typer.Option("--seat", help="Your seat, by ID or name, when the campaign has several humans"),
]


def _guard(store: EventStore, campaign_id: CampaignId, seat: SeatId) -> None:
    """Refuse to read or act for a seat someone else plays from Discord (Layer 9b).

    The host runs this CLI and may hold a seat too; another player's briefings,
    orders and status are not the host's to see. Break-glass reads come later.
    """
    if not cmp.is_postgres(cmp.db_target()):
        return
    from revisionist.engine.discord.tables import SeatLinks
    from revisionist.engine.pg_store import PostgresEventStore

    assert isinstance(store, PostgresEventStore)
    user = SeatLinks(store.conn).users(campaign_id).get(seat)
    if user is not None and user != os.environ.get("REVISIONIST_DISCORD_HOST_ID"):
        raise _fail("that seat is played from Discord by someone else", EXIT_REFUSED)


@app.callback()
def main() -> None:
    """Revisionist game engine."""


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(__version__)


@app.command()
def new(world: Annotated[str, typer.Argument(help="World name or path to a YAML file")]) -> None:
    """Compile a world bible and start a campaign from it."""
    try:
        decl = cmp.load_world(cmp.world_path(world))
    except FileNotFoundError as exc:
        raise _fail(str(exc)) from exc
    except ValueError as exc:
        raise _fail(f"world file does not parse:\n{exc}") from exc
    store = cmp.open_store()
    try:
        campaign_id = cmp.new_campaign(store, decl)
    except WorldError as exc:
        raise _fail("world has problems:\n" + "\n".join(exc.problems)) from exc
    state = load_state(store, campaign_id)
    humans = cmp.human_seats(state)
    if not humans:
        raise _fail("this campaign has no human seat")
    typer.echo(f"campaign {campaign_id}: {decl.name}")
    factions = len(cmp.faction_seats(state))
    if len(humans) == 1:
        typer.echo(f"you are {state.seats[humans[0]].name}; {factions} AI factions")
    else:
        names = ", ".join(f"{state.seats[s].name} ({s})" for s in humans)
        typer.echo(f"human seats: {names}; {factions} AI factions")


@app.command()
def status(seat: PlayerSeat = None) -> None:
    """Where the campaign stands, as your seat sees it."""
    store = cmp.open_store()
    state = load_state(store, _campaign(store))
    me = _me(state, seat)
    _guard(store, state.campaign_id, me)
    for line in play.status_lines(state, me, store.read(state.campaign_id)):
        typer.echo(line)


@app.command()
def briefing(seat: PlayerSeat = None) -> None:
    """Your agent's briefing: what happened, a dilemma and three moves."""
    store = cmp.open_store()
    campaign_id = _campaign(store)
    me = _me(load_state(store, campaign_id), seat)
    _guard(store, campaign_id, me)
    try:
        brief = play.get_briefing(store, campaign_id, me, transport_factory())
    except TransportError as exc:
        raise _fail(f"model call failed: {exc}") from exc
    if brief.withheld:
        raise _fail(play.WITHHELD, EXIT_LEAK)
    for line in brief.lines:
        typer.echo(line)


class OrderVisibility(StrEnum):
    PRIVATE = "private"
    PUBLIC = "public"
    COVERT = "covert"


@app.command()
def order(
    text: Annotated[str, typer.Argument(help="What you want done, in your words")],
    seat: PlayerSeat = None,
    visibility: Annotated[
        OrderVisibility, typer.Option("--visibility", help="Who may learn of this order")
    ] = OrderVisibility.PRIVATE,
    spin: Annotated[
        str | None, typer.Option("--spin", help="A public line on the order, at most 280 chars")
    ] = None,
) -> None:
    """Give your agent an order. It files an intent or asks you a question."""
    store = cmp.open_store()
    campaign_id = _campaign(store)
    me = _me(load_state(store, campaign_id), seat)
    _guard(store, campaign_id, me)
    try:
        result = play.give_order(
            store,
            campaign_id,
            me,
            text,
            transport_factory(),
            visibility=Visibility(visibility.value),
            spin=spin,
        )
    except TransportError as exc:
        raise _fail(f"model call failed: {exc}") from exc
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    if result.refused:
        raise _fail(result.line, EXIT_REFUSED)
    typer.echo(result.line)


def _deliverers() -> DelivererFactory | None:
    """Discord DMs for bound seats when a bot token is set on Postgres; else files."""
    if not cmp.is_postgres(cmp.db_target()):
        return None
    from revisionist.engine.discord.delivery import factory_from_env

    return factory_from_env()


@app.command()
def tick(offline: Offline = False) -> None:
    """AI factions take their turns, then the world resolves one tick."""
    store = cmp.open_store()
    run = play_tick(
        store,
        _campaign(store),
        offline=offline,
        transport_factory=transport_factory,
        briefings=cmp.briefings_dir(),
        warn=lambda message: typer.echo(message, err=True),
        deliverer_factory=_deliverers(),
    )
    result = run.result
    word = "quiet" if result.quiet else "resolved"
    typer.echo(
        f"tick {result.tick} {word}: {result.applied} applied, {len(result.rejected)} rejected, "
        f"{len(result.delivered)} briefings in {run.briefings}"
    )
    if result.quiet:
        typer.echo(f"  reason: {result.quiet_reason}")
    if run.leaks:
        raise _fail(render_leaks(run.leaks), EXIT_LEAK)


@app.command(name="why")
def why_cmd(target: Annotated[str, typer.Argument(help="An event ID or an intent ID")]) -> None:
    """Trace an intent through the log: the order, the roll, the deltas, the narration."""
    store = cmp.open_store()
    typer.echo(render_why(why(list(store.read(_campaign(store))), target)))


@app.command(name="replay")
def replay_cmd(
    to: Annotated[int, typer.Option("--to", help="Replay to the end of this tick")],
) -> None:
    """Rebuild the state at a past tick from the log and print its hash."""
    store = cmp.open_store()
    state = replay_to(list(store.read(_campaign(store))), to)
    typer.echo(
        f"tick {state.tick}  seq {state.last_seq}  hash {state_hash(state)}  "
        f"assets {len(state.assets)}  facts {len(state.facts)}  pending {len(state.pending)}"
    )


@app.command()
def cost() -> None:
    """Every model call this campaign made, per tick and in total."""
    store = cmp.open_store()
    typer.echo(render_cost(cost_report(store.read(_campaign(store)))))


@app.command()
def leaks(
    humans: Annotated[
        bool,
        typer.Option(
            "--humans", help="Also check each person across all their seats (Postgres, Discord)"
        ),
    ] = False,
) -> None:
    """Scan every narration for canaries its reader must not see. Exit 3 on any hit."""
    store = cmp.open_store()
    campaign_id = _campaign(store)
    state = load_state(store, campaign_id)
    found = leak_report(state, store.read(campaign_id))
    typer.echo(render_leaks(found))
    human_leaks: list[HumanLeak] = []
    if humans:
        holdings = _holdings(store, state)
        seen = surfaces_from_log(store.read(campaign_id), holdings)
        human_leaks = scan_humans(state, holdings, seen)
        for leak in human_leaks:
            typer.echo(f"LEAK human={leak.human} fact={leak.fact_id} in {leak.surface}")
        if not human_leaks:
            typer.echo(f"0 leaks across {len(holdings)} people")
    if found or human_leaks:
        raise typer.Exit(EXIT_LEAK)


def _holdings(store: EventStore, state: State) -> dict[str, frozenset[SeatId]]:
    """Who holds which seats: Discord users from their links, and the host, who
    can read every human seat nobody else holds through this CLI."""
    if not cmp.is_postgres(cmp.db_target()):
        raise _fail("--humans needs the Postgres store and Discord seat links")
    from revisionist.engine.discord.tables import SeatLinks
    from revisionist.engine.pg_store import PostgresEventStore

    assert isinstance(store, PostgresEventStore)
    links = SeatLinks(store.conn).users(state.campaign_id)
    host = os.environ.get("REVISIONIST_DISCORD_HOST_ID") or "host"
    held: dict[str, set[SeatId]] = {host: set()}
    for seat, user in links.items():
        held.setdefault(user, set()).add(seat)
    held[host] |= {s for s in cmp.human_seats(state) if s not in links}
    return {human: frozenset(seats) for human, seats in held.items()}


# --- revisionist db ------------------------------------------------------------------

db_app = typer.Typer(no_args_is_help=True, help="Postgres migrations, copy and verify.")
app.add_typer(db_app, name="db")

CampaignOpt = Annotated[
    str | None, typer.Option("--campaign", help="One campaign ID (default: every campaign)")
]


@db_app.command("migrate")
def db_migrate(
    app_role: Annotated[
        str, typer.Option("--app-role", envvar="REVISIONIST_APP_ROLE", help="Role the runtime uses")
    ] = "revisionist_app",
) -> None:
    """Apply pending migrations as the admin in ``$REVISIONIST_ADMIN_DSN``."""
    admin = os.environ.get("REVISIONIST_ADMIN_DSN")
    if not admin:
        raise _fail("REVISIONIST_ADMIN_DSN is not set")
    from revisionist.engine.pg_store import migrate

    applied = migrate(admin, app_role)
    typer.echo(f"applied {', '.join(applied)}" if applied else "up to date")


@db_app.command("copy")
def db_copy(
    source: Annotated[str, typer.Option("--from", help="SQLite file to copy from")],
    target: Annotated[str, typer.Option("--to", help="postgresql:// URL to copy into")],
    campaign: CampaignOpt = None,
) -> None:
    """Copy campaigns event for event, with the latest snapshot, then verify the copy."""
    if not cmp.is_postgres(source) and not os.path.isfile(source):
        raise _fail(f"no database at {source}")
    src, dst = cmp.open_store(source), cmp.open_store(target)
    failed = False
    for campaign_id in [CampaignId(campaign)] if campaign else src.campaign_ids():
        try:
            done = copy_campaign(src, dst, campaign_id)
        except StoreError as exc:
            typer.echo(f"FAIL {campaign_id}: {exc}", err=True)
            failed = True
            continue
        v = done.verified
        if not v.ok:
            typer.echo(f"FAIL {campaign_id}: {v.problem}", err=True)
            failed = True
            continue
        typer.echo(
            f"copied {campaign_id}: {done.copied} events ({done.already} already there), "
            f"tick {v.tick}, hash {v.state_hash}"
        )
    if failed:
        raise typer.Exit(EXIT_ERROR)


@db_app.command("verify")
def db_verify(campaign: CampaignOpt = None) -> None:
    """Replay every campaign from event 0 and check each hash and the latest snapshot."""
    store = cmp.open_store()
    ids = [CampaignId(campaign)] if campaign else store.campaign_ids()
    if not ids:
        raise _fail("no campaigns to verify")
    failed = False
    for campaign_id in ids:
        v = verify_campaign(store, campaign_id)
        if v.ok:
            sealed = f", sealed, {v.erased} erased" if v.sealed else ""
            typer.echo(
                f"ok {campaign_id}: {v.events} events, tick {v.tick}, hash {v.state_hash}{sealed}"
            )
        else:
            typer.echo(f"FAIL {campaign_id}: {v.problem}", err=True)
            failed = True
    if failed:
        raise typer.Exit(EXIT_ERROR)


# --- revisionist admin ------------------------------------------------------------

admin_app = typer.Typer(no_args_is_help=True, help="Host tools: keys, erasure, audited reads.")
app.add_typer(admin_app, name="admin")


@admin_app.command("new-master-key")
def admin_new_master_key(
    path: Annotated[Path, typer.Argument(help="Where to write it; must not exist")],
) -> None:
    """Write a fresh master key (mode 0600) for sealing campaigns. Back it up off the box:
    without it, every sealed campaign is unreadable."""
    try:
        from revisionist.engine.seal import new_master_key
    except ImportError as exc:
        raise _fail("sealing needs the seal extra: install '.[postgres,seal]'") from exc
    try:
        new_master_key(path)
    except FileExistsError as exc:
        raise _fail(f"{path} exists; a master key is never overwritten") from exc
    typer.echo(f"wrote {path}; set REVISIONIST_MASTER_KEY_FILE={path}")


# --- revisionist scheduler -------------------------------------------------------------

scheduler_app = typer.Typer(
    no_args_is_help=True, help="Unattended ticks on Postgres, run by a systemd timer."
)
app.add_typer(scheduler_app, name="scheduler")


def _pg_store(
    what: str = "the scheduler",
    hint: str = "; SQLite campaigns tick by hand with `revisionist tick`",
) -> "PostgresEventStore":
    target = cmp.db_target()
    if not cmp.is_postgres(target):
        raise _fail(f"{what} needs REVISIONIST_DB to be a postgresql:// URL{hint}")
    from revisionist.engine.pg_store import PostgresEventStore

    store = cmp.open_store(target)
    assert isinstance(store, PostgresEventStore)
    return store


def _scheduled(store: "PostgresEventStore", campaign: str | None) -> list[sch.Schedule]:
    table = sch.register(store, clock())
    ids = [CampaignId(campaign)] if campaign else store.campaign_ids()
    try:
        return [table.get(c) for c in ids]
    except LookupError as exc:
        raise _fail(str(exc)) from exc


@scheduler_app.command("run")
def scheduler_run(campaign: CampaignOpt = None, offline: Offline = False) -> None:
    """Tick every due campaign once. Exit 3 on a leak, 1 when a campaign was paused."""
    store = _pg_store()
    try:
        outcomes = sch.run(
            store,
            clock,
            offline=offline,
            transport_factory=transport_factory,
            briefings=cmp.briefings_dir(),
            warn=lambda message: typer.echo(message, err=True),
            campaign=CampaignId(campaign) if campaign else None,
            deliverer_factory=_deliverers(),
        )
    finally:
        store.close()
    for o in outcomes:
        if o.action is not sch.Action.WAITING:
            typer.echo(f"{o.campaign_id} {o.action}: {o.detail}".rstrip(": "))
    if any(o.action is sch.Action.LEAK for o in outcomes):
        raise typer.Exit(EXIT_LEAK)
    if any(o.newly_paused for o in outcomes):
        raise typer.Exit(EXIT_ERROR)


@scheduler_app.command("status")
def scheduler_status(campaign: CampaignOpt = None) -> None:
    """When each campaign ticks next, and how its last tick went."""
    store = _pg_store()
    try:
        for row in _scheduled(store, campaign):
            state = "paused: " + row.paused_reason if row.paused else "active"
            if row.advance_requested:
                state += ", advance requested"
            typer.echo(
                f"{row.campaign_id}  next {row.next_due_at.astimezone(UTC):%Y-%m-%d %H:%MZ}"
                f"  {state}  failures {row.consecutive_failures}  last {row.last_status or '-'}"
            )
    finally:
        store.close()


def _set(
    campaign: str | None, verb: str, change: Callable[[sch.ScheduleTable, CampaignId], None]
) -> None:
    store = _pg_store()
    try:
        table = sch.ScheduleTable(store.conn)
        for row in _scheduled(store, campaign):
            change(table, row.campaign_id)
            typer.echo(f"{row.campaign_id} {verb}")
    finally:
        store.close()


@scheduler_app.command("advance")
def scheduler_advance(campaign: CampaignOpt = None) -> None:
    """Tick on the next scheduler run instead of waiting for the timer."""
    _set(campaign, "advance requested", sch.ScheduleTable.advance)


@scheduler_app.command("pause")
def scheduler_pause(
    campaign: CampaignOpt = None,
    reason: Annotated[str, typer.Option("--reason")] = "paused by host",
) -> None:
    """Stop ticking until resumed."""
    _set(campaign, "paused", lambda table, campaign_id: table.pause(campaign_id, reason))


@scheduler_app.command("resume")
def scheduler_resume(campaign: CampaignOpt = None) -> None:
    """Tick again; clears the failure count."""
    _set(campaign, "resumed", sch.ScheduleTable.resume)


# --- revisionist discord ---------------------------------------------------------------

discord_app = typer.Typer(no_args_is_help=True, help="Play from Discord (Postgres only).")
app.add_typer(discord_app, name="discord")

SeatOpt = Annotated[
    str | None,
    typer.Option("--seat", help="Seat ID or name (default: the campaign's one human seat)"),
]
OneCampaignOpt = Annotated[
    str | None, typer.Option("--campaign", help="Campaign ID (default: the newest)")
]


_DISCORD = ("Discord play", "")


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise _fail(f"{name} is not set")
    return value


def _bound_seat(
    store: "PostgresEventStore", campaign: str | None, seat: str | None
) -> tuple[CampaignId, SeatId]:
    campaign_id = CampaignId(campaign) if campaign else _campaign(store)
    try:
        state = load_state(store, campaign_id)
    except LookupError as exc:
        raise _fail(str(exc)) from exc
    if seat is None:
        return campaign_id, _me(state)
    try:
        found = state.seats[cmp.resolve_seat(state, seat)]
    except LookupError as exc:
        raise _fail(str(exc)) from exc
    if found.controller is not Controller.HUMAN:
        raise _fail(
            f"{found.name} is not a human seat; only human seats play from Discord", EXIT_REFUSED
        )
    return campaign_id, found.seat_id


@discord_app.command("link")
def discord_link(
    user: Annotated[str, typer.Option("--user", help="The player's Discord user ID")],
    campaign: OneCampaignOpt = None,
    seat: SeatOpt = None,
) -> None:
    """Bind a human seat to a Discord user in the guild ``$REVISIONIST_DISCORD_GUILD_ID``."""
    from revisionist.engine.discord.tables import LinkError, SeatLinks

    if not user.isdigit():
        raise _fail("--user is a Discord user ID (digits)")
    guild = _env("REVISIONIST_DISCORD_GUILD_ID")
    store = _pg_store(*_DISCORD)
    try:
        campaign_id, seat_id = _bound_seat(store, campaign, seat)
        SeatLinks(store.conn).link(campaign_id, seat_id, user, guild)
    except LinkError as exc:
        raise _fail(f"refused: {exc}", EXIT_REFUSED) from exc
    finally:
        store.close()
    typer.echo(f"linked {seat_id} in {campaign_id} to Discord user {user}")


@discord_app.command("unlink")
def discord_unlink(campaign: OneCampaignOpt = None, seat: SeatOpt = None) -> None:
    """Remove a seat's Discord binding."""
    from revisionist.engine.discord.tables import SeatLinks

    store = _pg_store(*_DISCORD)
    try:
        campaign_id, seat_id = _bound_seat(store, campaign, seat)
        removed = SeatLinks(store.conn).unlink(campaign_id, seat_id)
    finally:
        store.close()
    if not removed:
        raise _fail(f"{seat_id} in {campaign_id} was not linked")
    typer.echo(f"unlinked {seat_id} in {campaign_id}")


@discord_app.command("invite")
def discord_invite(
    campaign: OneCampaignOpt = None,
    seat: SeatOpt = None,
    hours: Annotated[int, typer.Option("--hours", min=1, max=720)] = 72,
) -> None:
    """Issue a single-use code a player redeems with ``/join`` to take a human seat."""
    from revisionist.engine.discord.tables import Invites, SeatLinks

    store = _pg_store(*_DISCORD)
    try:
        campaign_id, seat_id = _bound_seat(store, campaign, seat)
        if seat_id in SeatLinks(store.conn).users(campaign_id):
            raise _fail(f"{seat_id} is already linked; unlink it first", EXIT_REFUSED)
        now = clock()
        code = Invites(store.conn).issue(campaign_id, seat_id, now, hours)
        name = load_state(store, campaign_id).seats[seat_id].name
    finally:
        store.close()
    expires = (now + timedelta(hours=hours)).astimezone(UTC)
    typer.echo(
        f"invite for {name} ({seat_id}) in {campaign_id}, expires {expires:%Y-%m-%d %H:%MZ}:"
    )
    typer.echo(f"  {code}")
    typer.echo(
        "Send it to the player privately. They run /join with it in the server; it works once."
    )


@discord_app.command("invites")
def discord_invites(campaign: OneCampaignOpt = None) -> None:
    """Every invite issued for a campaign and whether it was used."""
    from revisionist.engine.discord.tables import Invites

    store = _pg_store(*_DISCORD)
    try:
        campaign_id = CampaignId(campaign) if campaign else _campaign(store)
        invites = Invites(store.conn).all(campaign_id)
    finally:
        store.close()
    if not invites:
        typer.echo("no invites")
    now = clock()
    for i in invites:
        if i.used_at is not None:
            state = f"used by {i.used_by} {i.used_at.astimezone(UTC):%Y-%m-%d %H:%MZ}"
        elif i.expires_at <= now:
            state = "expired"
        else:
            state = f"open until {i.expires_at.astimezone(UTC):%Y-%m-%d %H:%MZ}"
        typer.echo(f"{i.seat_id}  issued {i.issued_at.astimezone(UTC):%Y-%m-%d %H:%MZ}  {state}")


@discord_app.command("channel")
def discord_channel(
    purpose: Annotated[str, typer.Argument(help="Only 'public' for now")],
    channel_id: Annotated[str, typer.Argument(help="The Discord channel ID (digits)")],
    campaign: OneCampaignOpt = None,
) -> None:
    """Set the campaign's public channel: player statements and the press are posted there."""
    from revisionist.engine.discord.tables import Channels

    if purpose != "public":
        raise _fail("the only channel purpose is 'public'")
    if not channel_id.isdigit():
        raise _fail("the channel ID is digits (Developer Mode, right click, Copy ID)")
    guild = _env("REVISIONIST_DISCORD_GUILD_ID")
    store = _pg_store(*_DISCORD)
    try:
        campaign_id = CampaignId(campaign) if campaign else _campaign(store)
        Channels(store.conn).set_public(campaign_id, channel_id, guild)
    finally:
        store.close()
    typer.echo(f"public channel for {campaign_id} is {channel_id}")


@discord_app.command("seats")
def discord_seats() -> None:
    """Every Discord binding."""
    from revisionist.engine.discord.tables import SeatLinks

    store = _pg_store(*_DISCORD)
    try:
        links = SeatLinks(store.conn).all()
    finally:
        store.close()
    if not links:
        typer.echo("no seats linked")
    for link in links:
        typer.echo(
            f"{link.campaign_id}  {link.seat_id}  user {link.discord_user_id}"
            f"  guild {link.guild_id}  since {link.linked_at.astimezone(UTC):%Y-%m-%d %H:%MZ}"
        )


@discord_app.command("run")
def discord_run() -> None:
    """The gateway bot, in the foreground. Needs ``$REVISIONIST_DISCORD_TOKEN`` and the guild ID."""
    token = _env("REVISIONIST_DISCORD_TOKEN")
    guild = _env("REVISIONIST_DISCORD_GUILD_ID")
    if not guild.isdigit():
        raise _fail("REVISIONIST_DISCORD_GUILD_ID is a Discord server ID (digits)")
    _pg_store(*_DISCORD).close()
    try:
        from revisionist.engine.discord import bot
    except ImportError as exc:
        raise _fail(
            "the Discord bot needs the discord extra: install '.[postgres,discord]'"
        ) from exc
    from revisionist.engine.discord.delivery import routing_factory
    from revisionist.engine.discord.handlers import BotConfig, Handlers
    from revisionist.engine.discord.rest import DiscordRest

    host = os.environ.get("REVISIONIST_DISCORD_HOST_ID") or None
    if host is None:
        typer.echo(
            "REVISIONIST_DISCORD_HOST_ID is not set: /advance, /pause and /resume are refused",
            err=True,
        )
    rest = DiscordRest(token)
    handlers = Handlers(
        BotConfig(cmp.db_target(), guild, host),
        transport_factory=lambda: transport_factory(),
        clock=lambda: clock(),
        briefings=cmp.briefings_dir(),
        deliverer_factory=routing_factory(rest),
        rest=rest,
    )
    bot.run(handlers, token, int(guild))


__all__ = ["EXIT_ERROR", "EXIT_LEAK", "EXIT_REFUSED", "app", "transport_factory"]
