"""Agent chat (playtest #17, decision D5): a player asks their agent for advice.

The agent reads the seat's ``View`` only and cannot file an order. Unit U10 builds it.
"""

from revisionist.core.ids import CampaignId, SeatId
from revisionist.engine.llm import Transport
from revisionist.engine.store import EventStore


def advise(
    store: EventStore, campaign_id: CampaignId, seat: SeatId, question: str, transport: Transport
) -> str:
    """The agent's answer to ``question``, from what ``seat`` knows. Never files an intent."""
    raise NotImplementedError


__all__ = ["advise"]
