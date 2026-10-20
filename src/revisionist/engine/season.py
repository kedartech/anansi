"""The end of a season (Layer 10 item 1e; README §15).

The host ends the season between ticks. The full reveal is ``reveal(Full)``
applied as ordinary knowledge grants (decision 4), then ``season_ended`` names
the winners and makes every goal public. Timed seasons come with Layer 10
item 6; until then the host decides when.
"""

from revisionist.core import events as ev
from revisionist.core.dashboard import season_report
from revisionist.core.events import Event
from revisionist.core.fold import replay
from revisionist.core.goals import season_winners
from revisionist.core.ids import CampaignId, EventId, derived_id
from revisionist.core.ontology import Phase
from revisionist.core.referee import Full, reveal
from revisionist.core.visibility import PUBLIC
from revisionist.engine import campaign as cmp
from revisionist.engine.store import EventStore, load_state


class SeasonError(ValueError):
    """The season cannot end now. The message is safe to show the host."""


def end_season(store: EventStore, campaign_id: CampaignId) -> list[str]:
    """Reveal everything, decide the winners, and return the season report."""
    state = load_state(store, campaign_id)
    if state.phase is not Phase.PLAY:
        raise SeasonError(f"The season can only end in play, not in {state.phase.value}.")
    if state.frozen:
        raise SeasonError("A tick is in flight; let it finish first.")
    grants = reveal(state, Full(), state.tick)
    payloads: list[ev.Payload] = [ev.DeltaApplied(intent_id=None, delta=g) for g in grants]
    payloads.append(
        ev.SeasonEnded(winners=tuple(sorted(season_winners(state))), revealed=len(grants))
    )
    store.append_all(
        [
            Event(
                event_id=EventId(derived_id(campaign_id, "season-end", i)),
                campaign_id=campaign_id,
                seq=state.last_seq + 1 + i,
                tick=state.tick,
                scope=PUBLIC,
                payload=p,
                versions=cmp.versions(),
            )
            for i, p in enumerate(payloads)
        ]
    )
    return season_report(state, len(grants))


def report(store: EventStore, campaign_id: CampaignId) -> list[str]:
    """The season report again, rebuilt from the log as it stood before the reveal."""
    events = list(store.read(campaign_id))
    ended = next((e for e in events if isinstance(e.payload, ev.SeasonEnded)), None)
    if ended is None:
        raise SeasonError("The season has not ended.")
    assert isinstance(ended.payload, ev.SeasonEnded)
    before = replay(events[: ended.seq - ended.payload.revealed])
    return season_report(before, ended.payload.revealed)


__all__ = ["SeasonError", "end_season", "report"]
