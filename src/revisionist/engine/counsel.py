"""Counsel (realism, Phase 4): a seat consults a person on its card and hears them out.

The counsel model speaks as that person from a ``PersonView`` (their face, their
voice and the pages they hold as of today, from ``core.realism.person``), never from
``State``. Counsel changes nothing in the world, costs no in-world days [proposed]
and never files an order: the reply lands as a ``counsel_held`` record private to
the seat, sealed with the seat's key, and the call's cost as ``cost_logged``.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from revisionist.core import events as ev
from revisionist.core._base import Model
from revisionist.core.events import CallCost, Event
from revisionist.core.fold import replay
from revisionist.core.ids import CampaignId, EntityId, EventId, SeatId
from revisionist.core.realism.card import ChannelKind
from revisionist.core.realism.person import PersonView, project_person
from revisionist.core.visibility import private_to
from revisionist.engine import campaign as cmp
from revisionist.engine.ids import new_id
from revisionist.engine.llm import Request, Transport, prompt_text, prompt_version
from revisionist.engine.store import EventStore, load_state

COUNSEL_MODEL = "claude-sonnet-5-5"
"""[proposed] ``REVISIONIST_MODEL_COUNSEL`` overrides it per call."""
QUESTION_MAX = 1000


class Asker(Model):
    """Who puts the question: the seat's person as anyone sees them."""

    name: str
    office: str = ""


class Spoken(Model):
    text: str
    cost: CallCost | None = None


class Counsellor(Protocol):
    """A person's view and a question in, their words out. Never takes a ``State``."""

    def speak(
        self, person: PersonView, asker: Asker, channel: ChannelKind, question: str
    ) -> Spoken: ...


COUNSEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


def counsel_prompt(person: PersonView, asker: Asker, channel: ChannelKind, question: str) -> str:
    """The person first (stable across questions, so a provider cache can reuse it), then
    their papers, then the question."""
    if not isinstance(person, PersonView):
        raise TypeError(f"counsel takes a PersonView, not {type(person).__name__}")
    office = person.face.office if person.face is not None else ""
    head = f"## You are\n{person.name}" + (f", {office}" if office else "")
    voice = f"\nVoice: {person.voice}" if person.voice else ""
    papers = [
        {
            "kind": d.kind.value,
            "title": d.title,
            "issued": d.issued.isoformat(),
            "channel": d.channel.value,
            "lines": [
                {
                    "label": x.label,
                    "value": "not stated" if x.value is None else x.value,
                    **({"unit": x.unit} if x.unit else {}),
                }
                for x in d.body
            ],
            **({"text": d.prose} if d.prose else {}),
        }
        for d in person.documents
    ]
    who = asker.name + (f", {asker.office}" if asker.office else "")
    return (
        f"{head}{voice}\n\n## Today\n{person.today.isoformat()}\n\n"
        f"## Your papers\n{json.dumps(papers, ensure_ascii=False)}\n\n"
        f"## {who} asks you, by {channel.value.replace('_', ' ')}\n{question}"
    )


@dataclass
class ClaudeCounsellor:
    transport: Transport
    model: str = COUNSEL_MODEL
    max_usd: float = 0.25
    requests: list[Request] = field(default_factory=list)

    def speak(
        self, person: PersonView, asker: Asker, channel: ChannelKind, question: str
    ) -> Spoken:
        req = Request(
            call_class="counsel",
            model=self.model,
            system=prompt_text("counsel"),
            prompt=counsel_prompt(person, asker, channel, question),
            schema=COUNSEL_SCHEMA,
            max_usd=self.max_usd,
        )
        self.requests.append(req)
        reply = self.transport.call(req)
        text = reply.output.get("text", "") if isinstance(reply.output, dict) else ""
        return Spoken(text=text.strip(), cost=reply.cost("counsel"))


class CounselRefusedError(ValueError):
    pass


class Counsel(Model):
    person: str
    channel: ChannelKind
    text: str
    event_id: EventId


def consult(
    store: EventStore,
    campaign_id: CampaignId,
    seat: SeatId,
    person: EntityId,
    question: str,
    counsellor: Counsellor,
) -> Counsel:
    """Put ``question`` to ``person`` over the seat's channel to them. Raises
    ``CounselRefusedError`` for a person not on the seat's card or an empty question;
    ``TransportError`` propagates and nothing is written."""
    state = load_state(store, campaign_id)
    if state.ruleset != "realism":
        raise CounselRefusedError("counsel is for realism campaigns")
    question = question.strip()
    if not question or len(question) > QUESTION_MAX:
        raise CounselRefusedError(f"ask a question of 1 to {QUESTION_MAX} characters")
    me = state.seats[seat]
    card = state.cards.get(me.embodies)
    contact = next((c for c in card.contacts if c.person == person), None) if card else None
    if card is None or contact is None:
        names = ", ".join(state.entities[c.person].name for c in card.contacts) if card else ""
        raise CounselRefusedError(
            f"{state.entities[person].name if person in state.entities else person} is not "
            f"within your reach" + (f" (you can consult: {names})" if names else "")
        )
    asker = Asker(name=state.entities[me.embodies].name, office=card.office)
    spoken = counsellor.speak(project_person(state, person), asker, contact.channel, question)
    if not spoken.text:
        raise CounselRefusedError(f"{state.entities[person].name} had nothing to say")
    text = f"You asked: {question}\n\n{spoken.text}"[:8000]
    versions = cmp.versions()
    versions = versions.model_copy(
        update={"prompt": f"{versions.prompt},counsel={prompt_version('counsel')}"}
    )
    held = Event(
        event_id=EventId(new_id()),
        campaign_id=campaign_id,
        seq=state.last_seq + 1,
        tick=state.tick,
        actor=seat,
        scope=private_to(seat),
        payload=ev.CounselHeld(seat_id=seat, person=person, channel=contact.channel, text=text),
        versions=versions,
    )
    batch = [held]
    if spoken.cost is not None:
        batch.append(
            Event(
                event_id=EventId(new_id()),
                campaign_id=campaign_id,
                seq=state.last_seq + 2,
                tick=state.tick,
                actor=seat,
                scope=private_to(seat),
                payload=ev.CostLogged(seat_id=seat, purpose="counsel", costs=(spoken.cost,)),
                versions=versions,
            )
        )
    replay(batch, initial=state)
    store.append_all(batch)
    return Counsel(
        person=state.entities[person].name,
        channel=contact.channel,
        text=text,
        event_id=held.event_id,
    )


def counsel_records(events: Sequence[Event], seat: SeatId) -> list[ev.CounselHeld]:
    """The seat's counsel, oldest first: its in-tray of conversations."""
    return [
        e.payload
        for e in events
        if isinstance(e.payload, ev.CounselHeld) and e.payload.seat_id == seat
    ]


__all__ = [
    "COUNSEL_MODEL",
    "Asker",
    "ClaudeCounsellor",
    "Counsel",
    "CounselRefusedError",
    "Counsellor",
    "Spoken",
    "consult",
    "counsel_prompt",
    "counsel_records",
]
