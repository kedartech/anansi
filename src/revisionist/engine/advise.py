"""Agent chat (playtest #17, decision D5): a player asks their agent for advice.

The agent reads the seat's ``View`` only and cannot file an order. Advice is not
an intent, so nothing here touches the order log.
"""

from revisionist.core.ids import CampaignId, SeatId
from revisionist.core.project import project
from revisionist.engine.agent import AGENT_MODEL
from revisionist.engine.campaign import log_cost
from revisionist.engine.llm import Request, Transport, prompt_text
from revisionist.engine.store import EventStore, load_state

_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "maxLength": 800}},
    "required": ["answer"],
}


def advise(
    store: EventStore, campaign_id: CampaignId, seat: SeatId, question: str, transport: Transport
) -> str:
    """The agent's answer to ``question``, from what ``seat`` knows. Never files an intent."""
    if not question.strip():
        return "Ask your agent a question."
    view = project(load_state(store, campaign_id), seat)
    reply = transport.call(
        Request(
            "advise",
            AGENT_MODEL,
            prompt_text("advise"),
            "Question: "
            + question.strip()[:500]
            + "\n\nView:\n"
            + view.model_dump_json(exclude={"own_pending", "roster"}, exclude_none=True),
            _SCHEMA,
            max_usd=0.10,
        )
    )
    log_cost(store, campaign_id, "advise", [reply.cost("advise")], seat)
    output = reply.output if isinstance(reply.output, dict) else {}
    text = str(output.get("answer") or output.get("text") or "").strip()
    return text[:800] or "Your agent has nothing to add."
