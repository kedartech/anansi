"""One tick end to end: AI deputy turns, ``run_tick``, then the leak scan.

``revisionist tick`` and the scheduler (Layer 8) share this path, so a scheduled tick
is exactly the tick a host would run by hand.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from revisionist.core.canary import Leak
from revisionist.core.ids import CampaignId, SeatId
from revisionist.core.ontology import Phase
from revisionist.core.project import project
from revisionist.core.report import leak_report
from revisionist.core.resolution import TickStage
from revisionist.engine import campaign as cmp
from revisionist.engine.agent import Agent, held_means
from revisionist.engine.llm import ClaudeNarrator, ClaudeResolver, Transport, TransportError
from revisionist.engine.prose import ClaudeProseWriter, ProseWriter, TemplateProseWriter
from revisionist.engine.providers import KeywordSafety, ScriptedResolver, TemplateNarrator
from revisionist.engine.realism_tick import run_realism_tick
from revisionist.engine.store import EventStore, load_state
from revisionist.engine.tick import (
    Deliverer,
    NotInPlayError,
    Providers,
    TickResult,
    file_intent,
    run_tick,
)


@runtime_checkable
class Publishes(Protocol):
    """A deliverer that also posts public records (statements) after a clean tick."""

    def publish(self) -> list[str]: ...


DelivererFactory = Callable[[EventStore, CampaignId, Path], Deliverer]
"""Builds the tick's deliverer from the store, the campaign and the briefings directory."""


def file_deliverer(store: EventStore, campaign_id: CampaignId, briefings: Path) -> Deliverer:
    """The default: every briefing to a file under ``briefings``."""
    state = load_state(store, campaign_id)
    names: dict[SeatId, str] = {s.seat_id: s.name for s in state.seats.values()}
    return cmp.FileDeliverer(briefings, names, state.tick)


@dataclass(frozen=True)
class TickRun:
    result: TickResult
    briefings: Path
    leaks: list[Leak]


def play_tick(
    store: EventStore,
    campaign_id: CampaignId,
    *,
    offline: bool,
    transport_factory: Callable[[], Transport],
    briefings: Path,
    warn: Callable[[str], None],
    stop_after: TickStage | None = None,
    deliverer_factory: DelivererFactory | None = None,
) -> TickRun:
    """Run (or resume) one tick. Transport errors from the tick itself propagate."""
    state = load_state(store, campaign_id)
    if state.phase is not Phase.PLAY:
        raise NotInPlayError(state.phase)  # before any faction spends a model call
    if state.ruleset == "realism":  # code decides outcomes; a model only writes the pages
        writer: ProseWriter = (
            TemplateProseWriter() if offline else ClaudeProseWriter(transport_factory())
        )
        done = run_realism_tick(
            store, campaign_id, versions=cmp.versions(), stop_after=stop_after, prose=writer
        )
        for dropped in done.prose_dropped:
            warn(f"  no prose for {dropped}")
        found = leak_report(load_state(store, campaign_id), store.read(campaign_id))
        return TickRun(done, briefings, found)
    deliverer = (deliverer_factory or file_deliverer)(store, campaign_id, briefings)
    if offline:
        providers = Providers(ScriptedResolver(), TemplateNarrator(), deliverer, KeywordSafety())
    else:
        transport = transport_factory()
        if not state.frozen:  # a tick left mid-flight resumes as it was frozen
            faction_turns(store, campaign_id, transport, warn)
        providers = Providers(
            ClaudeResolver(transport), ClaudeNarrator(transport), deliverer, KeywordSafety()
        )
    result = run_tick(store, campaign_id, providers, versions=cmp.versions(), stop_after=stop_after)
    found = leak_report(load_state(store, campaign_id), store.read(campaign_id))
    if not found and result.stopped_after is None and isinstance(deliverer, Publishes):
        deliverer.publish()
    return TickRun(result, briefings, found)


def faction_turns(
    store: EventStore,
    campaign_id: CampaignId,
    transport: Transport,
    warn: Callable[[str], None],
) -> None:
    """Each AI faction's deputy files up to three intents from its own view.

    A faction with no held means has nothing to move, so its deputy is not
    asked (no model call, no cost).
    """
    state = load_state(store, campaign_id)
    for seat in cmp.faction_seats(state):
        view = project(state, seat)
        agent = Agent.for_seat(state, seat, transport)
        try:
            if not held_means(view):
                warn(f"  {seat}: standing still (no means to move)")
                continue
            intents = agent.deputy_turn()
        except TransportError as exc:
            warn(f"  {seat}: deputy call failed, standing still ({exc})")
            intents = []
        for intent in intents:
            file_intent(store, campaign_id, intent, safety=KeywordSafety(), versions=cmp.versions())
        cmp.log_cost(store, campaign_id, "deputy", agent.spent, seat)


__all__ = [
    "DelivererFactory",
    "Publishes",
    "TickRun",
    "faction_turns",
    "file_deliverer",
    "play_tick",
]
