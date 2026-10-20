"""Command-line entry point (build plan Layer 7).

The solo slice is played from here: ``wyrd new salt-strait``, then
``wyrd briefing``, ``wyrd order "..."``, ``wyrd tick``, repeat. The
campaign lives in ``$WYRD_DB`` (default ``./wyrd.db``, or a ``postgresql://``
URL); every command works on the newest campaign there. ``wyrd db`` holds the
Postgres maintenance commands (Layer 8).
"""

import os
from collections.abc import Callable
from typing import Annotated

import typer

from wyrd import __version__
from wyrd.core.canary import scan_deliveries
from wyrd.core.fold import replay_to, state_hash
from wyrd.core.ids import CampaignId, SeatId
from wyrd.core.intent import Intent
from wyrd.core.project import project
from wyrd.core.report import cost_report, leak_report, render_cost, render_leaks, render_why, why
from wyrd.core.state import State
from wyrd.core.world_compile import WorldError
from wyrd.engine import campaign as cmp
from wyrd.engine.agent import Agent, receive_order
from wyrd.engine.llm import Transport, TransportError
from wyrd.engine.maintenance import copy_campaign, verify_campaign
from wyrd.engine.runner import play_tick
from wyrd.engine.store import EventStore, StoreError, load_state

app = typer.Typer(no_args_is_help=True, add_completion=False)

transport_factory: Callable[[], Transport] = cmp.live_transport
"""Swapped for a fake or recorded transport in tests (``wyrd.cli.transport_factory = ...``)."""

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


def _me(state: State) -> SeatId:
    try:
        return cmp.human_seat(state)
    except LookupError as exc:
        raise _fail(str(exc)) from exc


def _name(state: State, object_id: str) -> str:
    for table in (state.entities, state.assets, state.clocks, state.meters, state.seats):
        found = table.get(object_id)  # type: ignore[call-overload]  # IDs are str NewTypes
        if found is not None:
            return str(found.name)
    return object_id


@app.callback()
def main() -> None:
    """Wyrd Protocol game engine."""


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
    me = _me(state)
    typer.echo(f"campaign {campaign_id}: {decl.name}")
    typer.echo(f"you are {state.seats[me].name}; {len(cmp.faction_seats(state))} AI factions")


@app.command()
def status() -> None:
    """Where the campaign stands, as your seat sees it."""
    store = cmp.open_store()
    state = load_state(store, _campaign(store))
    me = _me(state)
    view = project(state, me)
    typer.echo(f"tick {view.tick}  seat {view.seats[me].name}  slots {view.seats[me].slots}")
    for intent in view.own_pending:
        targets = ", ".join(_name(state, t) for t in intent.targets) or "-"
        typer.echo(f"  pending {intent.intent_id} {intent.verb.value} -> {targets}")
    for clock in sorted(view.clocks.values(), key=lambda c: c.name):
        if clock.status.value == "open":
            typer.echo(f"  clock {clock.name}: {clock.segments_filled}/{clock.segments_total}")
    for meter in sorted(view.meters.values(), key=lambda m: (m.owner, m.name)):
        typer.echo(f"  {_name(state, meter.owner)} {meter.name}: {meter.value}")


@app.command()
def briefing() -> None:
    """Your agent's briefing: what happened, a dilemma and three moves."""
    store = cmp.open_store()
    campaign_id = _campaign(store)
    state = load_state(store, campaign_id)
    me = _me(state)
    try:
        brief = Agent.for_seat(state, me, transport_factory()).brief()
    except TransportError as exc:
        raise _fail(f"model call failed: {exc}") from exc
    if brief.cost is not None:
        cmp.log_cost(store, campaign_id, "briefing", [brief.cost], me)
    moves = brief.moves or ()
    text = "\n".join([brief.text, brief.dilemma or "", *moves])
    if scan_deliveries(state, [(me, text)]):
        raise _fail("briefing withheld: it named something your seat cannot know", EXIT_LEAK)
    typer.echo(brief.text)
    if brief.dilemma:
        typer.echo(f"\nDilemma: {brief.dilemma}")
    for n, move in enumerate(moves, start=1):
        typer.echo(f"  {n}. {move}")


@app.command()
def order(text: Annotated[str, typer.Argument(help="What you want done, in your words")]) -> None:
    """Give your agent an order. It files an intent or asks you a question."""
    store = cmp.open_store()
    campaign_id = _campaign(store)
    state = load_state(store, campaign_id)
    me = _me(state)
    try:
        result = receive_order(store, state, me, text, transport_factory(), versions=cmp.versions())
    except TransportError as exc:
        raise _fail(f"model call failed: {exc}") from exc
    if result.cost is not None:
        cmp.log_cost(store, campaign_id, "structure", [result.cost], me)
    outcome = result.outcome
    if not isinstance(outcome, Intent):
        typer.echo(f"question: {outcome.question}")
        return
    if result.rejection is not None:
        raise _fail(
            f"refused: {result.rejection.reason.value}: {result.rejection.detail}", EXIT_REFUSED
        )
    targets = ", ".join(_name(state, t) for t in outcome.targets) or "-"
    typer.echo(f"filed {outcome.intent_id}: {outcome.verb.value} -> {targets}")


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
def leaks() -> None:
    """Scan every narration for canaries its reader must not see. Exit 3 on any hit."""
    store = cmp.open_store()
    campaign_id = _campaign(store)
    found = leak_report(load_state(store, campaign_id), store.read(campaign_id))
    typer.echo(render_leaks(found))
    if found:
        raise typer.Exit(EXIT_LEAK)


# --- wyrd db ------------------------------------------------------------------

db_app = typer.Typer(no_args_is_help=True, help="Postgres migrations, copy and verify.")
app.add_typer(db_app, name="db")

CampaignOpt = Annotated[
    str | None, typer.Option("--campaign", help="One campaign ID (default: every campaign)")
]


@db_app.command("migrate")
def db_migrate(
    app_role: Annotated[
        str, typer.Option("--app-role", envvar="WYRD_APP_ROLE", help="Role the runtime uses")
    ] = "wyrd_app",
) -> None:
    """Apply pending migrations as the admin in ``$WYRD_ADMIN_DSN``."""
    admin = os.environ.get("WYRD_ADMIN_DSN")
    if not admin:
        raise _fail("WYRD_ADMIN_DSN is not set")
    from wyrd.engine.pg_store import migrate

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
            typer.echo(f"ok {campaign_id}: {v.events} events, tick {v.tick}, hash {v.state_hash}")
        else:
            typer.echo(f"FAIL {campaign_id}: {v.problem}", err=True)
            failed = True
    if failed:
        raise typer.Exit(EXIT_ERROR)


__all__ = ["EXIT_ERROR", "EXIT_LEAK", "EXIT_REFUSED", "app", "transport_factory"]
