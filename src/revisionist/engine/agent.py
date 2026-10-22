"""The player agent and the AI factions (build plan Layer 6).

An ``Agent`` holds exactly ``project(state, seat)`` and the seat's persona. It
never sees ``State``. It turns player text into typed intents, writes briefings,
quotes perceived odds, and runs the seat as a deputy when no one is there. AI
factions are the same class in permanent deputy mode (decided pre-Layer-1).

The deputy is a pure function over the view, so the counterfactual leak test
can assert that removing a fact the seat cannot see changes nothing it does.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter, ValidationError

from revisionist.core import events as ev
from revisionist.core import places
from revisionist.core._base import Model
from revisionist.core.events import CallCost, Versions
from revisionist.core.ids import (
    EntityId,
    IntentId,
    SeatId,
    derived_id,
)
from revisionist.core.intent import IRREVERSIBLE_VERBS, Intent, Verb
from revisionist.core.ontology import EntityKind
from revisionist.core.project import project
from revisionist.core.referee import Rejection, holds, rate
from revisionist.core.resolution import FeasibilityBand, RejectionReason
from revisionist.core.state import State, View
from revisionist.core.visibility import Scope, private_to
from revisionist.engine.campaign import log_cost
from revisionist.engine.llm import Request, Transport, TransportError, prompt_text
from revisionist.engine.store import EventStore, load_state
from revisionist.engine.tick import _Emitter, file_intent

AGENT_MODEL = "claude-haiku-5-5"
_INTENT_ADAPTER = TypeAdapter(Intent)


class ClarifyingQuestion(Model):
    """What the agent asks instead of filing when the text is ambiguous or unaffordable."""

    question: str
    cost: CallCost | None = None


class Briefing(Model):
    text: str
    dilemma: str = ""
    moves: tuple[str, str, str] | None = None
    cost: CallCost | None = None


class StructureResult(Model):
    outcome: Intent | ClarifyingQuestion
    cost: CallCost | None = None
    rejection: Rejection | None = None
    """Set when the model produced a valid intent the referee then refused."""


# --- schemas (the closed verb enum and the IDs the seat can see, nothing else) ---


def structure_schema(view: View) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "kind": {"enum": ["intent", "question"]},
            "question": {"type": "string", "maxLength": 500},
            "intent": _intent_schema(view),
        },
        "required": ["kind"],
        "additionalProperties": False,
    }


def deputy_schema(view: View) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"intents": {"type": "array", "items": _intent_schema(view), "maxItems": 3}},
        "required": ["intents"],
        "additionalProperties": False,
    }


BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "dilemma": {"type": "string", "maxLength": 280},
        "moves": {
            "type": "array",
            "items": {"type": "string", "maxLength": 140},
            "minItems": 3,
            "maxItems": 3,
        },
    },
    "required": ["text", "dilemma", "moves"],
    "additionalProperties": False,
}


PLACE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "real": {"type": "boolean"},
        "name": {"type": "string"},
        "polity": {"type": "string"},
    },
    "required": ["real", "name", "polity"],
}


def _visible_ids(view: View) -> dict[str, list[str]]:
    return {
        "entities": sorted(view.entities),
        "assets": sorted(view.assets),
        "relationships": sorted(view.relationships),
        "meters": sorted(view.meters),
        "clocks": sorted(view.clocks),
        "facts": sorted(view.facts),
        "commitments": sorted(view.commitments),
        "seats": sorted(view.seats),
    }


def _ids_visible(view: View, raw: dict[str, Any]) -> bool:
    known = {i for group in _visible_ids(view).values() for i in group}
    named = [*(raw.get("targets") or []), *(raw.get("means") or [])]
    counterparty = raw.get("counterparty")
    if isinstance(counterparty, str):
        named.append(counterparty)
    return all(isinstance(x, str) and x in known for x in named)


def held_means(view: View) -> list[str]:
    """Assets this seat's embodied entity holds, sorted."""
    return sorted(a for a in view.assets if holds(view, view.me.embodies, a))


def _intent_schema(view: View) -> dict[str, Any]:
    ids = _visible_ids(view)
    targets = [i for group in ids.values() for i in group]
    properties: dict[str, Any] = {
        "verb": {"enum": [v.value for v in Verb]},
        "targets": {"type": "array", "items": {"enum": targets}, "maxItems": 6},
        "counterparty": {"enum": ids["entities"]},
        "duration_ticks": {"type": "integer", "minimum": 1, "maximum": 12},
        "spin": {"type": "string", "maxLength": 280},
        "condition": {"type": "string", "maxLength": 200},
        "destination": {
            "type": "string",
            "maxLength": 40,
            "pattern": "^[A-Za-z][A-Za-z.'-]*( [A-Za-z][A-Za-z.'-]*){0,3}$",
        },
    }
    held = held_means(view)
    if held:
        properties["means"] = {"type": "array", "items": {"enum": held}, "maxItems": 4}
    return {
        "type": "object",
        "properties": properties,
        "required": ["verb"],
        "additionalProperties": False,
    }


def _intent_from_model(view: View, raw: Any, n: int = 0) -> Intent:
    """Fill the fields the model must not choose: id, actor, scope, tick, spin.

    Spin is the player's own public line (Layer 9b), so whatever the model or
    the deputy wrote there is dropped; ``receive_order`` sets the player's.

    IDs the seat cannot see are refused here, not left to the schema: a reply can
    come from a recording or a model that ignored ``--json-schema``.

    ``n`` keeps two intents with the same verb in one tick apart: the ID is
    derived from the seat's count of pending intents plus the reply index, so
    it stays a pure function of the view.
    """
    if not isinstance(raw, dict) or not _ids_visible(view, raw):
        raise ValidationError.from_exception_data("intent", [])
    intent = _INTENT_ADAPTER.validate_python(
        {
            **raw,
            "intent_id": IntentId(
                derived_id(
                    view.campaign_id,
                    view.tick,
                    view.viewer,
                    raw.get("verb", ""),
                    len(view.own_pending) + n,
                )
            ),
            "actor": view.viewer,
            "scope": private_to(view.viewer),
            "filed_tick": view.tick,
            "spin": None,
        }
    )
    return intent


# --- the agent ----------------------------------------------------------------


@dataclass
class Agent:
    """One seat's interface to the game. Holds a view, never a state."""

    view: View
    transport: Transport
    model: str = AGENT_MODEL
    dropped: list[str] = field(default_factory=list)
    spent: list[CallCost] = field(default_factory=list)
    """Every model call this agent made, for ``cost_logged``."""

    @classmethod
    def for_seat(cls, state: State, seat: SeatId, transport: Transport) -> "Agent":
        return cls(project(state, seat), transport)

    @property
    def seat(self) -> SeatId:
        return self.view.viewer

    def perceived_odds(self, intent: Intent) -> FeasibilityBand:
        """What the seat believes its chances are. Differs from truth when the view lacks facts."""
        return rate(self.view, intent)

    def structure(self, text: str) -> StructureResult:
        """Player text in, an intent or a question out. Never a delta, never a raw string."""
        reply = self.transport.call(
            Request(
                "structure",
                self.model,
                prompt_text("structure"),
                _structure_prompt(self.view, text),
                structure_schema(self.view),
                max_usd=0.25,
            )
        )
        cost = reply.cost("structure")
        output = reply.output if isinstance(reply.output, dict) else {}
        if output.get("kind") == "question":
            return StructureResult(
                outcome=ClarifyingQuestion(question=str(output.get("question", ""))[:500]),
                cost=cost,
            )
        try:
            intent = _intent_from_model(self.view, output.get("intent"))
        except ValidationError as exc:
            self.dropped.append(str(exc)[:300])
            return StructureResult(
                outcome=ClarifyingQuestion(
                    question="I could not file that. Try naming one thing to do."
                ),
                cost=cost,
            )
        return StructureResult(outcome=intent, cost=cost)

    def brief(self) -> Briefing:
        reply = self.transport.call(
            Request(
                "brief",
                self.model,
                prompt_text("brief"),
                _brief_prompt(self.view),
                BRIEF_SCHEMA,
                max_usd=0.25,
            )
        )
        output = reply.output if isinstance(reply.output, dict) else {}
        moves = output.get("moves")
        return Briefing(
            text=str(output.get("text", "")).strip(),
            dilemma=str(output.get("dilemma", ""))[:280],
            moves=_three(moves),
            cost=reply.cost("brief"),
        )

    def deputy_turn(self) -> list[Intent]:
        """Standing orders from the view alone. Irreversible verbs need an order naming them.

        Pure: the same view always yields the same intents, which is what the
        counterfactual leak test relies on. A seat with no standing orders does nothing.
        """
        seat = self.view.me
        if not seat.standing_orders:
            return []
        reply = self.transport.call(
            Request(
                "deputy",
                self.model,
                prompt_text("deputy"),
                _deputy_prompt(self.view),
                deputy_schema(self.view),
                max_usd=0.25,
            )
        )
        self.spent.append(reply.cost("deputy"))
        output = reply.output if isinstance(reply.output, dict) else {}
        intents: list[Intent] = []
        for n, raw in enumerate(output.get("intents", [])):
            try:
                intent = _intent_from_model(self.view, raw, n)
            except ValidationError as exc:
                self.dropped.append(str(exc)[:300])
                continue
            if not _deputy_may(seat.standing_orders, intent):
                self.dropped.append(f"deputy refused irreversible {intent.verb.value}")
                continue
            intents.append(intent)
        return intents


def _three(moves: Any) -> tuple[str, str, str] | None:
    if isinstance(moves, list) and len(moves) == 3:
        a, b, c = (str(m)[:140] for m in moves)
        return (a, b, c)
    return None


def _deputy_may(orders: Sequence[str], intent: Intent) -> bool:
    """An irreversible verb is allowed only when a standing order names it."""
    if intent.verb not in IRREVERSIBLE_VERBS:
        return True
    text = " ".join(orders).lower()
    return intent.verb.value in text or intent.verb.name.lower() in text


def _structure_prompt(view: View, text: str) -> str:
    seat = view.me
    return (
        f"## Seat: {seat.name} ({seat.persona.role if seat.persona else 'no persona'})\n"
        "## What this seat knows\n"
        + view.model_dump_json(exclude={"own_pending", "roster"}, exclude_none=True)
        + "\n\n## The player wrote\n"
        + text
        + "\n\nReturn one intent or one question."
    )


def _brief_prompt(view: View) -> str:
    seat = view.me
    clocks = [
        {"id": c.clock_id, "name": c.name, "filled": c.segments_filled, "total": c.segments_total}
        for c in view.clocks.values()
        if c.status == "open"
    ]
    return (
        f"## Reader: {seat.name}, tick {view.tick}\n"
        "## What this seat knows\n"
        + view.model_dump_json(exclude={"own_pending", "roster"}, exclude_none=True)
        + "\n\n## Open clocks\n"
        + str(clocks)
        + "\n\nWrite the briefing."
    )


def _deputy_prompt(view: View) -> str:
    seat = view.me
    return (
        f"## Seat: {seat.name}, tick {view.tick}\n"
        "## Standing orders\n"
        + "\n".join(f"- {o}" for o in seat.standing_orders)
        + "\n\n## What this seat knows\n"
        + view.model_dump_json(exclude={"own_pending", "roster"}, exclude_none=True)
        + "\n\nReturn up to three intents that carry out the orders."
    )


# --- filing player text -------------------------------------------------------


def _place_check(
    store: EventStore,
    state: State,
    seat: SeatId,
    destination: str,
    transport: Transport,
    versions: Versions,
) -> EntityId | None:
    """Admit a real place the world lacks. None when the model cannot place it."""
    polities = sorted(e.name for e in state.entities.values() if e.kind is EntityKind.POLITY)
    when = state.in_world_date.isoformat() if state.in_world_date else "unknown"
    prompt = (
        f"## Place\n{destination}\n## In-world date\n{when}\n## Polities in the world\n"
        + "\n".join(f"- {p}" for p in polities)
    )
    try:
        reply = transport.call(
            Request(
                "place_check",
                AGENT_MODEL,
                prompt_text("place_check"),
                prompt,
                PLACE_SCHEMA,
                max_usd=0.1,
            )
        )
    except TransportError:
        return None
    log_cost(store, state.campaign_id, "place_check", [reply.cost("place_check")], seat)
    output = reply.output if isinstance(reply.output, dict) else {}
    if output.get("real") is not True:
        return None
    current = load_state(store, state.campaign_id)
    name = str(output.get("name") or "").strip() or destination
    try:
        entity = places.admit(current, name, _polity_id(current, output.get("polity")))
    except ValueError:
        return None
    em = _Emitter(store, current, versions)
    em.commit([em.event(ev.EntityCreated(entity=entity))])
    return entity.entity_id


def _polity_id(state: State, name: Any) -> EntityId | None:
    if not isinstance(name, str) or not name.strip():
        return None
    key = name.strip().casefold()
    return next(
        (
            e.entity_id
            for e in state.entities.values()
            if e.kind is EntityKind.POLITY and e.name.casefold() == key
        ),
        None,
    )


def receive_order(
    store: EventStore,
    state: State,
    seat: SeatId,
    text: str,
    transport: Transport,
    *,
    versions: Versions | None = None,
    scope: Scope | None = None,
    spin: str | None = None,
) -> StructureResult:
    """Log the raw text privately, structure it, file what validates, ask otherwise.

    ``order_received`` lands before structuring so ``/why`` can show what was
    typed against what was filed, even when the model call fails. ``scope`` and
    ``spin`` are the player's choices and replace whatever the agent filled in;
    the default is Private with no spin.
    """
    em = _Emitter(store, state, versions or Versions(engine="0.0.1", schema_version="1"))
    received = em.event(
        ev.OrderReceived(seat_id=seat, text=text[:4000]), actor=seat, scope=private_to(seat)
    )
    em.commit([received])
    agent = Agent.for_seat(load_state(store, state.campaign_id), seat, transport)
    result = agent.structure(text)
    if isinstance(result.outcome, Intent):
        chosen = _INTENT_ADAPTER.validate_python(
            {**result.outcome.model_dump(), "scope": scope or result.outcome.scope, "spin": spin}
        )
        result = result.model_copy(update={"outcome": chosen})
        if chosen.destination and places.missing(state, (chosen.destination,)):
            place = _place_check(store, state, seat, chosen.destination, transport, em.versions)
            if place is None:
                refused = Rejection(
                    reason=RejectionReason.TARGET_UNKNOWN,
                    detail=f"I could not place {chosen.destination} at this date.",
                )
                return result.model_copy(update={"rejection": refused})
            chosen = chosen.model_copy(
                update={"targets": (*chosen.targets, place), "destination": None}
            )
            result = result.model_copy(update={"outcome": chosen})
        rejection = file_intent(
            store,
            state.campaign_id,
            chosen,
            versions=versions,
            from_order=received.event_id,
        )
        return result.model_copy(update={"rejection": rejection})
    asked = em.event(
        ev.ClarificationAsked(
            seat_id=seat, from_order=received.event_id, question=result.outcome.question
        ),
        actor=seat,
        scope=private_to(seat),
    )
    em.commit([asked])
    return result


# --- the counterfactual leak test --------------------------------------------


def counterfactual_diff(
    state: State, seat: SeatId, fact_id: str, transport: Transport
) -> list[str]:
    """Intents the deputy files differently once a fact it cannot see is removed.

    An empty list is the pass. Any entry is a leak: the seat's behaviour changed
    because of something outside its view, which means the projection failed.
    """
    from revisionist.core.ids import FactId

    hidden = FactId(fact_id)
    without = state.model_copy(
        update={"facts": {k: v for k, v in state.facts.items() if k != hidden}}
    )
    before = Agent.for_seat(state, seat, transport).deputy_turn()
    after = Agent.for_seat(without, seat, transport).deputy_turn()
    return [
        f"{a.intent_id}: {a.model_dump_json()} vs {b.model_dump_json()}"
        for a, b in zip(before, after, strict=False)
        if a != b
    ] or ([] if len(before) == len(after) else [f"count {len(before)} vs {len(after)}"])


__all__ = [
    "AGENT_MODEL",
    "Agent",
    "Briefing",
    "ClarifyingQuestion",
    "StructureResult",
    "counterfactual_diff",
    "deputy_schema",
    "receive_order",
    "structure_schema",
]
