"""Directives (realism, Phase 4): the seat's own words, read by the aide as one order.

The player writes an instruction to a contact over a channel. The aide (the aide's
model, call class ``aide``) reads it from the seat's ``View`` as one lever on the
desk with every parameter filled, or says why it cannot be done. Code checks the
reading before the player sees it and again when they sign: the lever is on the
card, its parameters fit, the person is a contact and the channel reaches them.
The model never files. Signing lands a ``directive_given`` record private to the
seat, sealed with the seat's key, and the ``order_drafted``, in one batch.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import Field

from revisionist.core import events as ev
from revisionist.core._base import Model
from revisionist.core.events import CallCost, Event
from revisionist.core.fold import replay
from revisionist.core.ids import CampaignId, EntityId, EventId, SeatId
from revisionist.core.project import project
from revisionist.core.realism.card import ChannelKind, Contact, ParamKind, ParamValue, words
from revisionist.core.realism.pipeline import Order
from revisionist.core.state import State, View
from revisionist.core.visibility import PUBLIC, private_to
from revisionist.engine import campaign as cmp
from revisionist.engine.aide import AIDE_MODEL, Draft, valid
from revisionist.engine.ids import new_id
from revisionist.engine.llm import Request, Transport, TransportError, prompt_text, prompt_version
from revisionist.engine.realism_tick import draft_order
from revisionist.engine.store import EventStore, load_state

TEXT_MAX = 1000
UNREADABLE = "Your aide could not make that into an order you can sign."
UNREACHABLE = "Your aide cannot be reached just now. The standard forms are below."


class DirectiveRefusedError(ValueError):
    pass


def channels(contact: Contact) -> tuple[ChannelKind, ...]:
    """How a directive can reach a contact: their own channel, and a memo to anyone
    reached in person or by telephone [proposed]."""
    if contact.channel in (ChannelKind.IN_PERSON, ChannelKind.TELEPHONE):
        return (contact.channel, ChannelKind.LETTER)
    return (contact.channel,)


class Reading(Model):
    """What the aide made of the words: a lever and its parameters, or a reason."""

    lever: str = ""
    params: dict[str, ParamValue] = Field(default_factory=dict)
    reason: str = ""
    cost: CallCost | None = None


class Proposal(Model):
    """The order the player is asked to sign, or why there is none."""

    person: EntityId
    channel: ChannelKind
    text: str
    lever: str = ""
    params: dict[str, ParamValue] = Field(default_factory=dict)
    reason: str = ""


class DirectiveReader(Protocol):
    """The seat's view and the words in, a reading out. Never takes a ``State``."""

    def read(self, view: View, person: str, channel: ChannelKind, text: str) -> Reading: ...


DIRECTIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lever": {"type": "string"},
        "params": {"type": "object", "additionalProperties": {"type": ["string", "integer"]}},
        "reason": {"type": "string"},
    },
    "required": ["lever", "params", "reason"],
    "additionalProperties": False,
}


def directive_prompt(view: View, person: str, channel: ChannelKind, text: str) -> str:
    """The desk first (stable between directives), then who it is to, then the words."""
    if not isinstance(view, View):
        raise TypeError(f"the aide reads a View, not {type(view).__name__}")
    desk = view.desk
    levers = [
        {
            "lever": x.name,
            "label": x.label,
            "summary": x.summary,
            "params": [
                {
                    "name": p.name,
                    "kind": p.kind.value,
                    **({"label": p.label} if p.label else {}),
                    **({"lo": p.lo, "hi": p.hi} if p.kind is ParamKind.INT else {}),
                    **(
                        {
                            "choices": [
                                {"value": c, "means": label}
                                for c, label in zip(
                                    p.choices, p.choice_labels or p.choices, strict=True
                                )
                            ]
                        }
                        if p.choices
                        else {}
                    ),
                }
                for p in x.params
            ],
        }
        for x in (desk.levers if desk is not None else ())
    ]
    principal = view.entities[view.me.embodies].name
    office = desk.office if desk is not None else ""
    return (
        f"## Your principal\n{principal}" + (f", {office}" if office else "") + "\n\n"
        f"## Their instruments\n{json.dumps(levers, ensure_ascii=False)}\n\n"
        f"## Today\n{view.in_world_date.isoformat() if view.in_world_date else ''}\n\n"
        f"## To {person}, by {words(channel.value).lower()}\n{text}"
    )


@dataclass
class ClaudeDirectiveReader:
    transport: Transport
    model: str = AIDE_MODEL
    max_usd: float = 0.10
    requests: list[Request] = field(default_factory=list)

    def read(self, view: View, person: str, channel: ChannelKind, text: str) -> Reading:
        req = Request(
            call_class="aide",
            model=self.model,
            system=prompt_text("directive"),
            prompt=directive_prompt(view, person, channel, text),
            schema=DIRECTIVE_SCHEMA,
            max_usd=self.max_usd,
        )
        self.requests.append(req)
        reply = self.transport.call(req)
        out = reply.output if isinstance(reply.output, dict) else {}
        params = out.get("params")
        return Reading(
            lever=str(out.get("lever", "")).strip(),
            params={
                str(k): v
                for k, v in (params if isinstance(params, dict) else {}).items()
                if isinstance(v, int | str) and not isinstance(v, bool)
            },
            reason=str(out.get("reason", "")).strip(),
            cost=reply.cost("aide"),
        )


def _addressed(
    state: State, seat: SeatId, person: EntityId, channel: ChannelKind, text: str
) -> str:
    """Check the address and the words; returns the words trimmed. Raises
    ``DirectiveRefusedError``."""
    if state.ruleset != "realism":
        raise DirectiveRefusedError("directives are for realism campaigns")
    text = text.strip()
    if not text or len(text) > TEXT_MAX:
        raise DirectiveRefusedError(f"Write an instruction of 1 to {TEXT_MAX} characters.")
    card = state.cards.get(state.seats[seat].embodies)
    contact = next((c for c in card.contacts if c.person == person), None) if card else None
    if contact is None:
        raise DirectiveRefusedError("That person is not within your reach.")
    if channel not in channels(contact):
        name = state.entities[person].name
        raise DirectiveRefusedError(f"You cannot reach {name} that way.")
    return text


def _reason(reading: Reading) -> str:
    """The aide's own reason when it is plain words, else the stock one."""
    said = reading.reason
    plain = said and len(said) <= 300 and "_" not in said and not any(c.isdigit() for c in said)
    return said if plain else UNREADABLE


def propose(
    store: EventStore,
    campaign_id: CampaignId,
    seat: SeatId,
    person: EntityId,
    channel: ChannelKind,
    text: str,
    reader: DirectiveReader,
) -> Proposal:
    """What the aide makes of ``text``, checked against the seat's desk. Files nothing;
    logs the call's cost. Raises ``DirectiveRefusedError`` for a bad address or empty
    words. A failed call is a proposal with a reason, not an error."""
    state = load_state(store, campaign_id)
    text = _addressed(state, seat, person, channel, text)
    view = project(state, seat)
    base = Proposal(person=person, channel=channel, text=text)
    try:
        reading = reader.read(view, state.entities[person].name, channel, text)
    except TransportError:
        return base.model_copy(update={"reason": UNREACHABLE})
    if reading.cost is not None:
        cmp.log_cost(store, campaign_id, "directive", [reading.cost], seat)
    levers = view.desk.levers if view.desk is not None else ()
    if reading.lever and valid(Draft(lever=reading.lever, params=reading.params), levers):
        return base.model_copy(update={"lever": reading.lever, "params": reading.params})
    return base.model_copy(update={"reason": UNREADABLE if reading.lever else _reason(reading)})


def sign(
    store: EventStore,
    campaign_id: CampaignId,
    seat: SeatId,
    proposal: Proposal,
) -> Order:
    """File the signed proposal: ``directive_given`` and ``order_drafted`` in one batch.
    The address and the order are checked again here, as the lever form's are. Raises
    ``ValueError``."""
    state = load_state(store, campaign_id)
    text = _addressed(state, seat, proposal.person, proposal.channel, proposal.text)
    order = draft_order(
        state, seat, proposal.lever, {k: str(v) for k, v in proposal.params.items()}
    )
    versions = cmp.versions()
    versions = versions.model_copy(
        update={"prompt": f"{versions.prompt},directive={prompt_version('directive')}"}
    )
    given = ev.DirectiveGiven(
        seat_id=seat,
        person=proposal.person,
        channel=proposal.channel,
        text=text,
        order_id=order.order_id,
        lever=order.lever,
        params=order.params,
    )
    batch = [
        Event(
            event_id=EventId(new_id()),
            campaign_id=campaign_id,
            seq=state.last_seq + i,
            tick=state.tick,
            actor=seat,
            scope=scope,
            payload=payload,
            versions=versions,
        )
        for i, (payload, scope) in enumerate(
            ((given, private_to(seat)), (ev.OrderDrafted(order=order), PUBLIC)), start=1
        )
    ]
    replay(batch, initial=state)
    store.append_all(batch)
    return order


__all__ = [
    "ClaudeDirectiveReader",
    "DirectiveReader",
    "DirectiveRefusedError",
    "Proposal",
    "Reading",
    "channels",
    "directive_prompt",
    "propose",
    "sign",
]
