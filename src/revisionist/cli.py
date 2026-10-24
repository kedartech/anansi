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
from revisionist.core.ids import CampaignId, EntityId, EventId, OrderId, SeatId
from revisionist.core.ontology import Controller, EntityKind, Persona, Phase
from revisionist.core.realism.check import gap_report, gaps, report
from revisionist.core.realism.pipeline import StepAction
from revisionist.core.report import leak_report, render_leaks
from revisionist.core.sealing import needs_admin_read
from revisionist.core.settings import Betrayal, Inertia, Lethality, Plausibility, Settings
from revisionist.core.state import State
from revisionist.core.visibility import Visibility
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import WorldError, compile_world, resolve_keys
from revisionist.engine import aide as aides
from revisionist.engine import campaign as cmp
from revisionist.engine import counsel, host_ops, play, realism_tick, season, session_zero
from revisionist.engine import scheduler as sch
from revisionist.engine.ids import new_id
from revisionist.engine.llm import Transport, TransportError
from revisionist.engine.maintenance import copy_campaign
from revisionist.engine.pitch import PendingCard, PitchError, card_lines, end_from, strikes_from
from revisionist.engine.runner import DelivererFactory, Publishes, file_deliverer, play_tick
from revisionist.engine.store import EventStore, Sealer, StoreError, load_state
from revisionist.engine.tick import NotInPlayError

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
    """The newest campaign; on Postgres the live one, never a retired one."""
    try:
        newest = cmp.current_campaign(store)
        if not hasattr(store, "conn"):
            return newest
        pg: PostgresEventStore = store  # type: ignore[assignment]
        now = clock()
        live = sch.live_campaigns(pg, now)
        if live:
            return live[-1]
        table = sch.register(pg, now)
        kept = [c for c in pg.campaign_ids() if table.get(c).retired_at is None]
        if not kept:
            raise LookupError("every campaign is retired: start one with `revisionist new <world>`")
        return kept[-1]
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
    from revisionist.engine.pg_store import PostgresEventStore

    assert isinstance(store, PostgresEventStore)
    user = host_ops.held_seats(store.conn, campaign_id).get(seat)
    if user is not None and user != os.environ.get("REVISIONIST_DISCORD_HOST_ID"):
        raise _fail("that seat is played from Discord or the web by someone else", EXIT_REFUSED)


@app.callback()
def main() -> None:
    """Revisionist game engine."""


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(__version__)


def _person_key(decl: WorldFile, who: str) -> str:
    """The world-file key of the person ``who`` names, by key or by name."""
    people = {d.key: d.name for d in decl.entities if d.kind is EntityKind.PERSON}
    if who in people:
        return who
    named = [k for k, name in people.items() if name.casefold() == who.casefold()]
    if len(named) != 1:
        raise _fail(f"no person {who!r} in {decl.name}", EXIT_REFUSED)
    return named[0]


@app.command()
def new(
    world: Annotated[str, typer.Argument(help="World name or path to a YAML file")],
    seat: Annotated[
        str | None,
        typer.Option("--seat", help="Realism worlds: the person you play, by key or name"),
    ] = None,
) -> None:
    """Compile a world bible and start a campaign from it."""
    try:
        decl = cmp.load_world(cmp.world_path(world))
    except FileNotFoundError as exc:
        raise _fail(str(exc)) from exc
    except ValueError as exc:
        raise _fail(f"world file does not parse:\n{exc}") from exc
    realism = decl.ruleset == "realism"
    if realism and seat is None:
        raise _fail("a realism world needs --seat <person>: who you play", EXIT_REFUSED)
    if seat is not None and not realism:
        raise _fail("--seat is only for realism worlds; a legacy world seats you", EXIT_REFUSED)
    key = _person_key(decl, seat) if seat is not None else None
    store = cmp.open_store()
    try:
        campaign_id = cmp.new_campaign(store, decl)
    except WorldError as exc:
        raise _fail("world has problems:\n" + "\n".join(exc.problems)) from exc
    if key is not None:
        entity = EntityId(resolve_keys(decl, campaign_id)[key])
        try:
            name = realism_tick.seat_player(store, campaign_id, entity)
        except session_zero.StepRefusedError as exc:
            raise _fail(str(exc), EXIT_REFUSED) from exc
        typer.echo(f"campaign {campaign_id}: {decl.name}")
        typer.echo(f"you are {name}")
        return
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


world_app = typer.Typer(no_args_is_help=True, help="Check a world file before it is played.")
app.add_typer(world_app, name="world")


@world_app.command("check")
def world_check(
    world: Annotated[str, typer.Argument(help="World name or path to a YAML file")],
    gaps_only: Annotated[bool, typer.Option("--gaps-only", help="Print only the gap list")] = False,
) -> None:
    """Validate a world file as `new` would, and list what is still unsourced."""
    try:
        decl = cmp.load_world(cmp.world_path(world))
    except FileNotFoundError as exc:
        raise _fail(str(exc)) from exc
    except ValueError as exc:
        raise _fail(f"world file does not parse:\n{exc}") from exc
    problems: list[str] = []
    try:
        compile_world(decl, CampaignId(new_id()), cmp.versions())
    except WorldError as exc:
        problems = list(exc.problems)
    found = gaps(decl)
    if gaps_only:
        typer.echo(gap_report(found))
        if problems:
            raise _fail("world has problems:\n" + "\n".join(problems))
        return
    typer.echo(report(decl, problems, found))
    if problems:
        raise typer.Exit(EXIT_ERROR)


establish_app = typer.Typer(no_args_is_help=True, help="Host: set up a new game from a pitch.")
app.add_typer(establish_app, name="establish")


def _card_file() -> Path:
    """The setting card waiting for confirm. It sits beside the briefings, so it
    follows the campaign's database rather than the working directory."""
    return cmp.briefings_dir().parent / "revisionist-card.json"


@establish_app.command("pitch")
def establish_pitch(
    text: Annotated[str, typer.Argument(help='A pitch, e.g. "WWII" or "Florence, April 1478"')],
    effort: Annotated[
        int | None,
        typer.Option(min=1, max=4, help="Model effort for the pitch, 1 (low) to 4 (max)"),
    ] = None,
) -> None:
    """Read a pitch into a setting card: 2-3 start points, each with the history it would follow."""
    try:
        card, costs = session_zero.propose(text, transport_factory(), effort)
    except PitchError as exc:
        raise _fail(exc.reason, EXIT_REFUSED) from exc
    except TransportError as exc:
        raise _fail(f"the GM could not be reached: {exc}") from exc
    pending = PendingCard(pitch=text, card=card, costs=tuple(costs))
    _card_file().write_text(pending.model_dump_json())
    for line in card_lines(card):
        typer.echo(line)
    typer.echo(
        "Confirm with: revisionist establish confirm <n> "
        "[--strike 2,4] [--end YYYY-MM-DD] [--days N]"
    )


@establish_app.command("confirm")
def establish_confirm(
    choice: Annotated[int, typer.Argument(help="The start point's number on the card")],
    strike: Annotated[
        str | None, typer.Option(help="History items to leave out, by number, e.g. 2,4")
    ] = None,
    end: Annotated[str | None, typer.Option(help="In-world end date, YYYY-MM-DD")] = None,
    days: Annotated[
        int | None, typer.Option(min=1, max=365, help="Real-life length of the game, in days")
    ] = None,
    plausibility: Annotated[Plausibility, typer.Option()] = Plausibility.PLAUSIBLE,
    inertia: Annotated[Inertia, typer.Option()] = Inertia.TENDENCIES,
    lethality: Annotated[Lethality, typer.Option()] = Lethality.HARD,
    betrayal: Annotated[Betrayal, typer.Option()] = Betrayal.CUTTHROAT,
    tone: Annotated[str, typer.Option(help="grounded, satirical, pulp, grim...")] = "grounded",
    tick_hours: Annotated[int, typer.Option(min=1, max=168)] = 24,
    effort: Annotated[
        int | None,
        typer.Option(min=1, max=4, help="Model effort for the world draft, 1 (low) to 4 (max)"),
    ] = None,
) -> None:
    """Open the game from the waiting setting card at start point N."""
    path = _card_file()
    if not path.exists():
        raise _fail("No setting card waiting: run revisionist establish pitch first.", EXIT_REFUSED)
    pending = PendingCard.model_validate_json(path.read_text())
    try:
        strikes = strikes_from(strike or "")
        end_date = end_from(end) if end else None
    except PitchError as exc:
        raise _fail(exc.reason, EXIT_REFUSED) from exc
    settings = Settings(
        plausibility=plausibility,
        inertia=inertia,
        lethality=lethality,
        betrayal=betrayal,
        tone=tone,
        tick_hours=tick_hours,
    )
    store = cmp.open_store()
    try:
        campaign_id = session_zero.confirm(
            store,
            pending.card,
            choice,
            settings,
            transport_factory(),
            strikes=strikes,
            end_date=end_date,
            real_days=days,
            effort=effort,
            progress=typer.echo,
            card_costs=pending.costs,
        )
    except session_zero.StepRefusedError as exc:
        raise _fail(str(exc), EXIT_REFUSED) from exc
    except session_zero.SessionZeroError as exc:
        raise _fail(str(exc)) from exc
    except TransportError as exc:
        raise _fail(f"the GM could not be reached: {exc}") from exc
    path.unlink()
    state = load_state(store, campaign_id)
    start = pending.card.start_points[choice - 1]
    typer.echo(f"campaign {campaign_id}: Session Zero at {start.label}")
    typer.echo(f"{len(state.roster)} characters to choose from:")
    for ch in sorted(state.roster.values(), key=lambda c: state.entities[c.entity_id].name):
        label = " (composite)" if ch.composite else ""
        typer.echo(f"  {state.entities[ch.entity_id].name}{label}: {ch.pitch}")


@app.command()
def dashboard(seat: PlayerSeat = None) -> None:
    """Your standing, means, goal progress and what you have learned of rivals' aims."""
    store = cmp.open_store()
    state = load_state(store, _campaign(store))
    me = _me(state, seat)
    _guard(store, state.campaign_id, me)
    for line in play.dashboard_lines(state, me):
        typer.echo(line)


season_app = typer.Typer(no_args_is_help=True, help="Host: end the season and read its report.")
app.add_typer(season_app, name="season")


@season_app.command("end")
def season_end() -> None:
    """Host: the full reveal and the winners. Ticks stop for good."""
    store = cmp.open_store()
    campaign_id = _campaign(store)
    try:
        lines = season.end_season(store, campaign_id)
    except season.SeasonError as exc:
        raise _fail(str(exc), EXIT_REFUSED) from exc
    deliverer = (_deliverers() or file_deliverer)(store, campaign_id, cmp.briefings_dir())
    if isinstance(deliverer, Publishes):
        deliverer.publish()
    for line in lines:
        typer.echo(line)


@season_app.command("report")
def season_report_cmd() -> None:
    """The season report again, once it has ended."""
    store = cmp.open_store()
    try:
        lines = host_ops.season_report_lines(store, _campaign(store))
    except season.SeasonError as exc:
        raise _fail(str(exc), EXIT_REFUSED) from exc
    for line in lines:
        typer.echo(line)


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


def _pairs(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        name, eq, value = item.partition("=")
        if not eq or not name:
            raise _fail(f"expected key=value, got {item!r}", EXIT_REFUSED)
        out[name] = value
    return out


Pairs = Annotated[list[str] | None, typer.Argument(help="key=value, one per parameter")]


@app.command()
def lever(name: str, params: Pairs = None, seat: PlayerSeat = None) -> None:
    """Realism: file an order by pulling one of your levers."""
    store = cmp.open_store()
    campaign_id = _campaign(store)
    me = _me(load_state(store, campaign_id), seat)
    _guard(store, campaign_id, me)
    try:
        order = realism_tick.file_order(store, campaign_id, me, name, _pairs(params or []))
    except (ValueError, KeyError) as exc:
        raise _fail(str(exc), EXIT_REFUSED) from exc
    state = load_state(store, campaign_id)
    first = state.pipelines[order.pipeline].steps[0]
    who = state.entities[first.person].name
    typer.echo(f"order {order.order_id}: {order.lever} filed {order.filed}")
    typer.echo(f"first step: {who} ({first.kind.value})")


@app.command()
def step(order_id: str, action: str, params: Pairs = None, seat: PlayerSeat = None) -> None:
    """Realism: act at the step of your order that waits on you."""
    store = cmp.open_store()
    campaign_id = _campaign(store)
    me = _me(load_state(store, campaign_id), seat)
    _guard(store, campaign_id, me)
    try:
        taken = StepAction(action)
    except ValueError as exc:
        names = ", ".join(a.value for a in StepAction)
        raise _fail(f"{action!r} is not an action (one of {names})", EXIT_REFUSED) from exc
    try:
        order = realism_tick.take_step(
            store, campaign_id, OrderId(order_id), taken, _pairs(params or [])
        )
    except (ValueError, KeyError) as exc:
        raise _fail(str(exc), EXIT_REFUSED) from exc
    typer.echo(
        f"order {order.order_id}: {taken.value}; now {order.status.value}, step {order.step}"
    )


def _person(state: State, me: SeatId, ref: str) -> EntityId:
    """A person on the seat's card by entity ID, full name or any one word of the name."""
    card = state.cards.get(state.seats[me].embodies)
    reach = [c.person for c in card.contacts] if card is not None else []
    if ref in reach:
        return EntityId(ref)
    want = ref.casefold()
    found = [
        p
        for p in reach
        if state.entities[p].name.casefold() == want
        or want in state.entities[p].name.casefold().replace(".", "").split()
    ]
    if len(found) != 1:
        names = ", ".join(state.entities[p].name for p in reach) or "nobody"
        raise _fail(
            f"no one called {ref!r} within your reach (you can consult: {names})", EXIT_REFUSED
        )
    return found[0]


@app.command()
def consult(
    person: Annotated[str, typer.Argument(help="Someone on your card: a name or a surname")],
    question: Annotated[str, typer.Argument(help="What you ask them")],
    seat: PlayerSeat = None,
) -> None:
    """Realism: ask a person on your card for counsel. Changes nothing in the world."""
    store = cmp.open_store()
    campaign_id = _campaign(store)
    state = load_state(store, campaign_id)
    me = _me(state, seat)
    _guard(store, campaign_id, me)
    try:
        held = counsel.consult(
            store,
            campaign_id,
            me,
            _person(state, me, person),
            question,
            counsel.ClaudeCounsellor(transport_factory()),
        )
    except counsel.CounselRefusedError as exc:
        raise _fail(str(exc), EXIT_REFUSED) from exc
    except TransportError as exc:
        raise _fail(f"{person} could not be reached: {exc}") from exc
    typer.echo(f"{held.person}, {held.channel.value.replace('_', ' ')}:")
    typer.echo(held.text)
    found = leak_report(load_state(store, campaign_id), store.read(campaign_id))
    if found:
        raise _fail(render_leaks(found), EXIT_LEAK)


@app.command()
def aide(seat: PlayerSeat = None, offline: Offline = False) -> None:
    """Realism: your aide's brief: unread papers, decisions due, draft orders (never filed)."""
    store = cmp.open_store()
    campaign_id = _campaign(store)
    me = _me(load_state(store, campaign_id), seat)
    _guard(store, campaign_id, me)
    writer: aides.AideWriter = (
        aides.TemplateAide() if offline else aides.ClaudeAide(transport_factory())
    )
    try:
        out = aides.aide_for(store, campaign_id, me, writer)
    except ValueError as exc:
        raise _fail(str(exc), EXIT_REFUSED) from exc
    except TransportError as exc:
        raise _fail(f"your aide could not be reached: {exc}") from exc
    typer.echo(out.summary)
    typer.echo("")
    typer.echo("decisions due:" if out.decisions else "decisions due: none")
    for d in out.decisions:
        typer.echo(f"  {d.order_id} {d.lever} (step {d.step}): {', '.join(d.allowed)}")
    typer.echo("drafts (not filed):" if out.drafts else "drafts: none")
    for draft in out.drafts:
        typer.echo(f"  {draft.command()}")


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
    try:
        run = play_tick(
            store,
            _campaign(store),
            offline=offline,
            transport_factory=transport_factory,
            briefings=cmp.briefings_dir(),
            warn=lambda message: typer.echo(message, err=True),
            deliverer_factory=_deliverers(),
        )
    except NotInPlayError as exc:
        raise _fail(str(exc), EXIT_REFUSED) from exc
    result = run.result
    if isinstance(result, realism_tick.RealismTickResult):
        now = load_state(store, _campaign(store)).in_world_date
        typer.echo(realism_tick.wake_line(result, now))
        if run.leaks:
            raise _fail(render_leaks(run.leaks), EXIT_LEAK)
        return
    word = "quiet" if result.quiet else "resolved"
    typer.echo(
        f"tick {result.tick} {word}: {result.applied} applied, {len(result.rejected)} rejected, "
        f"{len(result.delivered)} briefings in {run.briefings}"
    )
    if result.quiet:
        typer.echo(f"  reason: {result.quiet_reason}")
    if run.leaks:
        raise _fail(render_leaks(run.leaks), EXIT_LEAK)


BreakGlass = Annotated[
    bool,
    typer.Option(
        "--break-glass", help="Read private records of a sealed campaign; logged and announced"
    ),
]
Reason = Annotated[str | None, typer.Option("--reason", help="Why; kept in the log")]


@app.command(name="why")
def why_cmd(
    target: Annotated[str, typer.Argument(help="An event ID or an intent ID")],
    break_glass: BreakGlass = False,
    reason: Reason = None,
) -> None:
    """Trace an intent through the log: the order, the roll, the deltas, the narration.

    On a sealed campaign, private records stay hidden unless you break glass,
    which first logs an admin read the players are told about.
    """
    store = cmp.open_store()
    try:
        lines = host_ops.why_lines(store, _campaign(store), target, reason, break_glass=break_glass)
    except host_ops.HostOpError as exc:
        raise _fail("--break-glass needs --reason") from exc
    for line in lines:
        typer.echo(line)


def _admin_read(
    store: EventStore, campaign_id: CampaignId, events: list[EventId], reason: str | None
) -> None:
    if not reason or not reason.strip():
        raise _fail("--break-glass needs --reason")
    cmp.log_admin_read(store, campaign_id, events, reason.strip())


@app.command(name="replay")
def replay_cmd(
    to: Annotated[int, typer.Option("--to", help="Replay to the end of this tick")],
) -> None:
    """Rebuild the state at a past tick from the log and print its hash."""
    store = cmp.open_store()
    typer.echo(host_ops.replay_line(store, _campaign(store), to))


@app.command()
def cost() -> None:
    """Every model call this campaign made, per tick and in total."""
    store = cmp.open_store()
    typer.echo(host_ops.cost_text(store, _campaign(store)))


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
    holdings = _holdings(store, load_state(store, campaign_id)) if humans else None
    lines, leaked = host_ops.leak_lines(store, campaign_id, holdings)
    for line in lines:
        typer.echo(line)
    if leaked:
        raise typer.Exit(EXIT_LEAK)


def _holdings(store: EventStore, state: State) -> dict[str, frozenset[SeatId]]:
    if not cmp.is_postgres(cmp.db_target()):
        raise _fail("--humans needs the Postgres store and Discord seat links")
    from revisionist.engine.pg_store import PostgresEventStore

    assert isinstance(store, PostgresEventStore)
    return host_ops.holdings(store.conn, state, os.environ.get("REVISIONIST_DISCORD_HOST_ID"))


# --- revisionist db ------------------------------------------------------------------

# --- Session Zero: character, goal, brief, agent (Layer 10) ----------------------

character_app = typer.Typer(no_args_is_help=True, help="Session Zero: choose your character.")
app.add_typer(character_app, name="character")
goal_app = typer.Typer(no_args_is_help=True, help="Session Zero: choose your secret goal.")
app.add_typer(goal_app, name="goal")


def _zero(work: Callable[[EventStore, CampaignId], list[str] | str]) -> None:
    store = cmp.open_store()
    try:
        out = work(store, _campaign(store))
    except session_zero.StepRefusedError as exc:
        raise _fail(str(exc), EXIT_REFUSED) from exc
    except TransportError as exc:
        raise _fail(f"your agent could not be reached: {exc}") from exc
    for line in [out] if isinstance(out, str) else out:
        typer.echo(line)


def _new_seat(store: EventStore, campaign_id: CampaignId, new: bool) -> SeatId:
    """A seat for someone about to claim a character. The CLI plays one human unless
    ``--new`` asks for another."""
    humans = cmp.human_seats(load_state(store, campaign_id))
    if humans and not new:
        name = load_state(store, campaign_id).seats[humans[0]].name
        raise _fail(f"you already play {name}; pass --new for another player", EXIT_REFUSED)
    return SeatId(new_id())


NewPlayer = Annotated[bool, typer.Option("--new", help="Claim for another player")]


@character_app.command("roster")
def character_roster() -> None:
    """The characters on offer, and who is taken."""
    _zero(lambda store, c: session_zero.roster_lines(load_state(store, c)))


@character_app.command("search")
def character_search(
    description: Annotated[str, typer.Argument(help="What you want to play")],
) -> None:
    """Characters on the roster that fit what you describe."""

    def work(store: EventStore, c: CampaignId) -> list[str]:
        found = session_zero.search(store, c, None, description, transport_factory())
        return found or ["no one on the roster fits; try other words, or pick your own"]

    _zero(work)


@character_app.command("claim")
def character_claim(
    name: Annotated[str, typer.Argument(help="A name from the roster")], new: NewPlayer = False
) -> None:
    """Take a character from the roster."""

    def work(store: EventStore, c: CampaignId) -> str:
        who = session_zero.claim(store, c, _new_seat(store, c, new), name)
        return f"you play {who}"

    _zero(work)


@character_app.command("pick")
def character_pick(
    name: Annotated[str, typer.Argument(help="Any real person from this moment")],
    new: NewPlayer = False,
) -> None:
    """Play someone the roster leaves out, if the record places them at the start."""

    def work(store: EventStore, c: CampaignId) -> str:
        seat = _new_seat(store, c, new)
        return f"you play {session_zero.pick(store, c, seat, name, transport_factory())}"

    _zero(work)


@goal_app.command("suggest")
def goal_suggest(
    route: Annotated[
        str, typer.Argument(help="history (as it went) or could_have (as it could have gone)")
    ],
    seat: PlayerSeat = None,
) -> None:
    """Your agent offers up to three goals on a route."""
    if route not in ("history", "could_have"):
        raise _fail("route is history or could_have; write your own with `goal own`")
    chosen: session_zero.Route = "history" if route == "history" else "could_have"

    def work(store: EventStore, c: CampaignId) -> list[str]:
        me = _me(load_state(store, c), seat)
        return session_zero.propose_goals(store, c, me, chosen, transport_factory())

    _zero(work)


@goal_app.command("own")
def goal_own(
    text: Annotated[str, typer.Argument(help="Your goal, in your words")], seat: PlayerSeat = None
) -> None:
    """Write your own goal; your agent turns it into one the engine can check."""

    def work(store: EventStore, c: CampaignId) -> list[str]:
        me = _me(load_state(store, c), seat)
        return session_zero.propose_goals(store, c, me, "own", transport_factory(), text)

    _zero(work)


@goal_app.command("confirm")
def goal_confirm(
    label: Annotated[str, typer.Argument(help="The label of an offered goal, e.g. H1")],
    seat: PlayerSeat = None,
) -> None:
    """Fix one offered goal for the season."""
    _zero(
        lambda store, c: session_zero.confirm_goal(store, c, _me(load_state(store, c), seat), label)
    )


@goal_app.command("flags")
def goal_flags() -> None:
    """Host: goals the balance check flagged. Scores only; never the goal."""
    _zero(host_ops.goal_flag_lines)


@goal_app.command("approve")
def goal_approve(seat: Annotated[str, typer.Argument(help="The seat, by ID or name")]) -> None:
    """Host: let one flagged goal stand."""

    def work(store: EventStore, c: CampaignId) -> str:
        try:
            return host_ops.approve_goal(store, c, seat)
        except host_ops.HostOpError as exc:
            raise _fail(str(exc), EXIT_REFUSED) from exc

    _zero(work)


@app.command("history")
def history_cmd(seat: PlayerSeat = None) -> None:
    """Session Zero: your tick-0 brief, from the record."""

    def work(store: EventStore, c: CampaignId) -> str:
        me = _me(load_state(store, c), seat)
        return session_zero.history_brief(store, c, me, transport_factory())

    _zero(work)


@app.command("agent")
def agent_cmd(
    name: Annotated[str, typer.Option("--name")],
    role: Annotated[str, typer.Option("--role", help="chief of staff, handler, spouse...")],
    voice: Annotated[str, typer.Option("--voice")] = "",
    backstory: Annotated[str, typer.Option("--backstory")] = "",
    relationship: Annotated[str, typer.Option("--relationship")] = "",
    seat: PlayerSeat = None,
) -> None:
    """Session Zero: design your agent's persona."""
    persona = Persona(
        name=name, role=role, voice=voice, backstory=backstory, relationship=relationship
    )
    _zero(
        lambda store, c: session_zero.design_agent(
            store, c, _me(load_state(store, c), seat), persona
        )
    )


@app.command("begin")
def begin_cmd() -> None:
    """Host: close Session Zero and start play."""
    _zero(session_zero.begin)


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
    lines, ok = host_ops.verify_lines(store, ids)
    for line in lines:
        typer.echo(line, err=line.startswith("FAIL "))
    if not ok:
        raise typer.Exit(EXIT_ERROR)


# --- revisionist admin ------------------------------------------------------------

admin_app = typer.Typer(no_args_is_help=True, help="Host tools: keys, erasure, audited reads.")
app.add_typer(admin_app, name="admin")


@admin_app.command("show")
def admin_show(
    event_id: Annotated[str, typer.Argument(help="The event to read")],
    reason: Reason = None,
) -> None:
    """Print one event in full. A private one in a sealed campaign is an admin read:
    it is logged, with your reason, before anything is shown."""
    store = cmp.open_store()
    campaign_id = _campaign(store)
    found = next((e for e in store.read(campaign_id) if e.event_id == event_id), None)
    if found is None:
        raise _fail(f"no event {event_id} in {campaign_id}")
    sealer: Sealer | None = getattr(store, "sealer", None)
    if sealer is not None and sealer.sealed(campaign_id) and needs_admin_read(found):
        _admin_read(store, campaign_id, [found.event_id], reason)
    typer.echo(found.model_dump_json(indent=2, exclude={"payload_enc"}))


@admin_app.command("erase")
def admin_erase(
    seat: Annotated[str, typer.Option("--seat", help="Seat ID or name")],
    reason: Annotated[str, typer.Option("--reason", help="Why; kept with the destroyed key")],
    campaign: Annotated[
        str | None, typer.Option("--campaign", help="Campaign ID (default: the newest)")
    ] = None,
) -> None:
    """Erase a seat's own words in a sealed campaign by destroying its key.

    Its orders, its agent's questions, its private briefings and its bug notes
    become unreadable; the game's consequences of its play stay, and replay
    still works. Needs ``$REVISIONIST_ADMIN_DSN``: the app role cannot destroy keys.
    """
    import psycopg

    from revisionist.engine.seal import destroy_key

    admin = _env("REVISIONIST_ADMIN_DSN")
    store = cmp.open_store()
    campaign_id = CampaignId(campaign) if campaign else _campaign(store)
    try:
        seat_id = cmp.resolve_seat(load_state(store, campaign_id), seat)
    except LookupError as exc:
        raise _fail(str(exc)) from exc
    with psycopg.connect(admin, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT key_id FROM seal_keys"
            " WHERE campaign_id = %s AND holder = %s AND destroyed_at IS NULL",
            (campaign_id, seat_id),
        ).fetchall()
        if not rows:
            raise _fail(f"{seat_id} has no live key in {campaign_id}: nothing sealed to erase")
        for (key_id,) in rows:
            destroy_key(conn, key_id, reason)
    typer.echo(
        f"erased the words of {seat_id} in {campaign_id}. Restart the bot so it forgets the key."
    )


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
        sch.register(store, clock())
        lines = host_ops.schedule_lines(store, [CampaignId(campaign)] if campaign else None)
    except LookupError as exc:
        raise _fail(str(exc)) from exc
    finally:
        store.close()
    for line in lines:
        typer.echo(line)


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
    from revisionist.engine.discord.tables import Invites

    store = _pg_store(*_DISCORD)
    try:
        current = CampaignId(campaign) if campaign else _campaign(store)
        if seat is None and load_state(store, current).phase is Phase.SESSION_ZERO:
            # The player's seat appears when they claim a character.
            campaign_id, seat_id, name = current, SeatId(new_id()), "a new player"
        else:
            campaign_id, seat_id = _bound_seat(store, campaign, seat)
            name = load_state(store, campaign_id).seats[seat_id].name
        if seat_id in host_ops.held_seats(store.conn, campaign_id):
            raise _fail(f"{seat_id} is already linked; unlink it first", EXIT_REFUSED)
        now = clock()
        code = Invites(store.conn).issue(campaign_id, seat_id, now, hours)
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
    store = _pg_store(*_DISCORD)
    try:
        campaign_id = CampaignId(campaign) if campaign else _campaign(store)
        lines = host_ops.invite_lines(store.conn, campaign_id, clock())
    finally:
        store.close()
    for line in lines:
        typer.echo(line)


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
    store = _pg_store(*_DISCORD)
    try:
        lines = host_ops.seat_lines(store.conn)
    finally:
        store.close()
    for line in lines:
        typer.echo(line)


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


web_app = typer.Typer(no_args_is_help=True, help="Play from the web client (Postgres only).")
app.add_typer(web_app, name="web")

WEB_STATIC = Path(__file__).resolve().parents[2] / "web"
"""The client in the repo's ``web/`` folder, served as static files."""


@web_app.command("serve")
def web_serve(
    host: Annotated[str, typer.Option("--host", help="Address to bind; keep it private")] = (
        "127.0.0.1"
    ),
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8740,
    static: Annotated[
        Path | None, typer.Option("--static", help="The client folder (default: the repo's web/)")
    ] = None,
) -> None:
    """The web client and its JSON API, in the foreground. Plain HTTP: bind it to
    a private address such as the tailnet, never a public one."""
    import logging

    _pg_store("the web client", "").close()
    from revisionist.engine.web.server import App, serve

    root = static or WEB_STATIC
    if not (root / "index.html").is_file():
        raise _fail(f"no web client at {root} (index.html missing)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app_ = App(
        cmp.db_target(),
        root,
        transport_factory=lambda: transport_factory(),
        clock=lambda: clock(),
        briefings=cmp.briefings_dir(),
    )
    typer.echo(f"web client on http://{host}:{port}/", err=True)
    serve(app_, host, port)


__all__ = ["EXIT_ERROR", "EXIT_LEAK", "EXIT_REFUSED", "app", "transport_factory"]
