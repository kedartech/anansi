"""One tick end to end: AI deputy turns, ``run_tick``, then the leak scan.

``wyrd tick`` and the scheduler (Layer 8) share this path, so a scheduled tick
is exactly the tick a host would run by hand.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from wyrd.core.canary import Leak
from wyrd.core.ids import CampaignId
from wyrd.core.report import leak_report
from wyrd.core.resolution import TickStage
from wyrd.engine import campaign as cmp
from wyrd.engine.agent import Agent
from wyrd.engine.llm import ClaudeNarrator, ClaudeResolver, Transport, TransportError
from wyrd.engine.providers import KeywordSafety, ScriptedResolver, TemplateNarrator
from wyrd.engine.store import EventStore, load_state
from wyrd.engine.tick import Providers, TickResult, file_intent, run_tick


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
) -> TickRun:
    """Run (or resume) one tick. Transport errors from the tick itself propagate."""
    state = load_state(store, campaign_id)
    deliverer = cmp.FileDeliverer(
        briefings, {s.seat_id: s.name for s in state.seats.values()}, state.tick
    )
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
    return TickRun(result, deliverer.directory, found)


def faction_turns(
    store: EventStore,
    campaign_id: CampaignId,
    transport: Transport,
    warn: Callable[[str], None],
) -> None:
    """Each AI faction's deputy files up to three intents from its own view."""
    for seat in cmp.faction_seats(load_state(store, campaign_id)):
        agent = Agent.for_seat(load_state(store, campaign_id), seat, transport)
        try:
            intents = agent.deputy_turn()
        except TransportError as exc:
            warn(f"  {seat}: deputy call failed, standing still ({exc})")
            intents = []
        for intent in intents:
            file_intent(store, campaign_id, intent, safety=KeywordSafety(), versions=cmp.versions())
        cmp.log_cost(store, campaign_id, "deputy", agent.spent, seat)


__all__ = ["TickRun", "faction_turns", "play_tick"]
