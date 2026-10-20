"""Command-line entry point (build plan Layer 7).

The solo slice is played from here: ``wyrd new salt-strait``, then
``wyrd briefing``, ``wyrd order "..."``, ``wyrd tick``, repeat. The
campaign lives in ``$WYRD_DB`` (default ``./wyrd.db``); every command works
on the newest campaign in that file.
"""

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
from wyrd.engine.llm import ClaudeNarrator, ClaudeResolver, Transport, TransportError
from wyrd.engine.providers import KeywordSafety, ScriptedResolver, TemplateNarrator
from wyrd.engine.store import SqliteStore, load_state
from wyrd.engine.tick import Providers, file_intent, run_tick

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


def _campaign(store: SqliteStore) -> CampaignId:
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
    campaign_id = _campaign(store)
    state = load_state(store, campaign_id)
    deliverer = cmp.FileDeliverer(
        cmp.briefings_dir(cmp.db_path()),
        {s.seat_id: s.name for s in state.seats.values()},
        state.tick,
    )
    if offline:
        providers = Providers(ScriptedResolver(), TemplateNarrator(), deliverer, KeywordSafety())
    else:
        transport = transport_factory()
        if not state.frozen:  # a tick left mid-flight resumes as it was frozen
            _faction_turns(store, campaign_id, transport)
        providers = Providers(
            ClaudeResolver(transport), ClaudeNarrator(transport), deliverer, KeywordSafety()
        )
    result = run_tick(store, campaign_id, providers, versions=cmp.versions())
    word = "quiet" if result.quiet else "resolved"
    typer.echo(
        f"tick {result.tick} {word}: {result.applied} applied, {len(result.rejected)} rejected, "
        f"{len(result.delivered)} briefings in {deliverer.directory}"
    )
    if result.quiet:
        typer.echo(f"  reason: {result.quiet_reason}")
    found = leak_report(load_state(store, campaign_id), store.read(campaign_id))
    if found:
        raise _fail(render_leaks(found), EXIT_LEAK)


def _faction_turns(store: SqliteStore, campaign_id: CampaignId, transport: Transport) -> None:
    """Each AI faction's deputy files up to three intents from its own view."""
    for seat in cmp.faction_seats(load_state(store, campaign_id)):
        agent = Agent.for_seat(load_state(store, campaign_id), seat, transport)
        try:
            intents = agent.deputy_turn()
        except TransportError as exc:
            typer.echo(f"  {seat}: deputy call failed, standing still ({exc})", err=True)
            intents = []
        for intent in intents:
            file_intent(store, campaign_id, intent, safety=KeywordSafety(), versions=cmp.versions())
        cmp.log_cost(store, campaign_id, "deputy", agent.spent, seat)


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


__all__ = ["EXIT_ERROR", "EXIT_LEAK", "EXIT_REFUSED", "app", "transport_factory"]
