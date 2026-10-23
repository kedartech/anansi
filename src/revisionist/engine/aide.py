"""The aide (realism, Phase 4): a seat's personal staff officer.

The aide reads the seat's projected ``View`` and nothing else (plus the pipeline
structures, as the web desk does, to tell which step an order is at). It returns a
summary of the unread pages, the decisions waiting on the human, and up to three
draft orders from the seat's own levers. Drafts are printed, never filed; the aide
never speaks for anyone on the card (that is counsel). The template path needs no
model; the model path logs its cost as ``cost_logged``.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from revisionist.core import events as ev
from revisionist.core._base import Model
from revisionist.core.events import CallCost, Event
from revisionist.core.ids import CampaignId, DocumentId, OrderId, PipelineId, SeatId
from revisionist.core.project import project
from revisionist.core.realism.card import Lever, ParamKind, ParamSpec, ParamValue
from revisionist.core.realism.document import DocumentView
from revisionist.core.realism.pipeline import OrderStatus, Pipeline, StepKind
from revisionist.core.realism.prose import invented_numbers
from revisionist.core.state import View
from revisionist.engine import campaign as cmp
from revisionist.engine.llm import Request, Transport, prompt_text
from revisionist.engine.store import EventStore, load_state

AIDE_MODEL = "claude-haiku-5-5"
"""[proposed] ``REVISIONIST_MODEL_AIDE`` overrides it per call."""
MAX_DRAFTS = 3


class Decision(Model):
    order_id: OrderId
    lever: str
    step: int
    allowed: tuple[str, ...]


class Draft(Model):
    lever: str
    params: dict[str, ParamValue]

    def command(self) -> str:
        pairs = " ".join(f"{k}={v}" for k, v in self.params.items())
        return f"revisionist lever {self.lever}" + (f" {pairs}" if pairs else "")


class AideBrief(Model):
    summary: str
    unread: tuple[DocumentId, ...] = ()
    decisions: tuple[Decision, ...] = ()
    drafts: tuple[Draft, ...] = ()
    cost: CallCost | None = None


def _view_only(view: object) -> View:
    if not isinstance(view, View):
        raise TypeError(f"the aide reads a View, not {type(view).__name__}")
    return view


def unread(view: View, read: frozenset[DocumentId]) -> list[DocumentView]:
    """The arrived pages the seat has not opened, newest first."""
    pages = [d for k, d in _view_only(view).documents.items() if k not in read]
    return sorted(pages, key=lambda d: (d.arrives, d.doc_id), reverse=True)


def decisions_due(view: View, pipelines: Mapping[PipelineId, Pipeline]) -> list[Decision]:
    """The seat's own orders whose current step is a decision its person holds."""
    me = _view_only(view).me.embodies
    out: list[Decision] = []
    for order in sorted(view.orders.values(), key=lambda o: (o.filed, o.order_id)):
        steps = pipelines[order.pipeline].steps
        if order.status is not OrderStatus.MOVING or order.step >= len(steps):
            continue
        step = steps[order.step]
        if step.kind is StepKind.DECISION and step.person == me:
            out.append(
                Decision(
                    order_id=order.order_id,
                    lever=order.lever,
                    step=order.step,
                    allowed=tuple(sorted(a.value for a in step.allowed)),
                )
            )
    return out


def fits(spec: ParamSpec, value: object) -> bool:
    if spec.kind is ParamKind.INT:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and spec.lo is not None
            and spec.hi is not None
            and spec.lo <= value <= spec.hi
        )
    if spec.kind is ParamKind.ENUM:
        return value in spec.choices
    return isinstance(value, str) and 0 < len(value) <= 200


def valid(draft: Draft, levers: Sequence[Lever]) -> bool:
    """A draft names one of the seat's levers and fits every one of its parameters."""
    lever = next((x for x in levers if x.name == draft.lever), None)
    if lever is None or set(draft.params) != {p.name for p in lever.params}:
        return False
    return all(fits(p, draft.params[p.name]) for p in lever.params)


def _default(spec: ParamSpec, view: View) -> ParamValue:
    if spec.kind is ParamKind.INT:
        assert spec.lo is not None and spec.hi is not None
        return (spec.lo + spec.hi) // 2
    if spec.kind is ParamKind.ENUM:
        return spec.choices[0]
    if spec.kind is ParamKind.ENTITY:
        contacts = view.desk.contacts if view.desk is not None else ()
        return str(contacts[0].person) if contacts else str(view.me.embodies)
    return "as you see fit"


def template_drafts(view: View) -> list[Draft]:
    """One draft per lever, middle of each range and first of each choice."""
    desk = _view_only(view).desk
    levers = desk.levers if desk is not None else ()
    return [
        Draft(lever=x.name, params={p.name: _default(p, view) for p in x.params})
        for x in levers[:MAX_DRAFTS]
    ]


def _who(view: View, page: DocumentView) -> str:
    entity = view.entities.get(page.author)
    return entity.name if entity is not None else "unknown"


def template_summary(view: View, pages: Sequence[DocumentView]) -> str:
    if not pages:
        return "Nothing unread on your desk."
    named = "; ".join(f"{p.title or p.kind.value} ({_who(view, p)})" for p in pages[:6])
    more = f", and {len(pages) - 6} more" if len(pages) > 6 else ""
    return f"{len(pages)} unread: {named}{more}."


# --- the model -------------------------------------------------------------------


AIDE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "drafts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lever": {"type": "string"},
                    "params": {
                        "type": "object",
                        "additionalProperties": {"type": ["string", "integer"]},
                    },
                },
                "required": ["lever", "params"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "drafts"],
    "additionalProperties": False,
}


def aide_prompt(view: View, pages: Sequence[DocumentView], due: Sequence[Decision]) -> str:
    """The desk first (stable between asks), then what is due, then the unread pages."""
    _view_only(view)
    desk = view.desk
    levers = [
        {
            "lever": x.name,
            "params": [
                {
                    "name": p.name,
                    "kind": p.kind.value,
                    **({"lo": p.lo, "hi": p.hi} if p.kind is ParamKind.INT else {}),
                    **({"choices": list(p.choices)} if p.choices else {}),
                }
                for p in x.params
            ],
        }
        for x in (desk.levers if desk is not None else ())
    ]
    principal = view.entities[view.me.embodies].name
    office = desk.office if desk is not None else ""
    papers = [
        {
            "title": p.title,
            "kind": p.kind.value,
            "from": _who(view, p),
            "arrived": p.arrives.isoformat(),
            "lines": [
                {"label": x.label, "value": "not stated" if x.value is None else x.value}
                for x in p.body
            ],
        }
        for p in pages
    ]
    waiting = [d.model_dump(mode="json") for d in due]
    return (
        f"## Your principal\n{principal}" + (f", {office}" if office else "") + "\n\n"
        f"## Their levers\n{json.dumps(levers, ensure_ascii=False)}\n\n"
        f"## Today\n{view.in_world_date.isoformat() if view.in_world_date else ''}\n\n"
        f"## Waiting on them\n{json.dumps(waiting)}\n\n"
        f"## Unread papers\n{json.dumps(papers, ensure_ascii=False)}\n\nBrief them."
    )


class AideWriter(Protocol):
    def brief(
        self, view: View, pages: Sequence[DocumentView], due: Sequence[Decision]
    ) -> tuple[str, list[Draft], CallCost | None]: ...


@dataclass
class ClaudeAide:
    transport: Transport
    model: str = AIDE_MODEL
    max_usd: float = 0.10
    requests: list[Request] = field(default_factory=list)

    def brief(
        self, view: View, pages: Sequence[DocumentView], due: Sequence[Decision]
    ) -> tuple[str, list[Draft], CallCost | None]:
        req = Request(
            call_class="aide",
            model=self.model,
            system=prompt_text("aide"),
            prompt=aide_prompt(view, pages, due),
            schema=AIDE_SCHEMA,
            max_usd=self.max_usd,
        )
        self.requests.append(req)
        reply = self.transport.call(req)
        out = reply.output if isinstance(reply.output, dict) else {}
        drafts: list[Draft] = []
        for item in out.get("drafts", []):
            try:
                drafts.append(Draft.model_validate(item))
            except ValueError:
                continue
        return str(out.get("summary", "")).strip(), drafts, reply.cost("aide")


class TemplateAide:
    def brief(
        self, view: View, pages: Sequence[DocumentView], due: Sequence[Decision]
    ) -> tuple[str, list[Draft], CallCost | None]:
        return template_summary(view, pages), template_drafts(view), None


def brief(
    view: View,
    pipelines: Mapping[PipelineId, Pipeline],
    read: frozenset[DocumentId],
    writer: AideWriter,
) -> AideBrief:
    """What the aide tells the seat. Pure apart from ``writer``; files nothing.

    A model summary that states a figure no unread page carries falls back to the
    template, and drafts that do not fit the seat's levers are dropped."""
    _view_only(view)
    pages = unread(view, read)
    due = decisions_due(view, pipelines)
    summary, drafts, cost = writer.brief(view, pages, due)
    if not summary or invented_numbers(summary, pages):
        summary = template_summary(view, pages)
    levers = view.desk.levers if view.desk is not None else ()
    kept = [d for d in drafts if valid(d, levers)][:MAX_DRAFTS]
    return AideBrief(
        summary=summary,
        unread=tuple(p.doc_id for p in pages),
        decisions=tuple(due),
        drafts=tuple(kept),
        cost=cost,
    )


__all__ = [
    "AIDE_MODEL",
    "AideBrief",
    "AideWriter",
    "ClaudeAide",
    "Decision",
    "Draft",
    "TemplateAide",
    "aide_for",
    "aide_prompt",
    "brief",
    "decisions_due",
    "read_marks",
    "template_drafts",
    "unread",
    "valid",
]


def read_marks(events: Iterable[Event], seat: SeatId) -> frozenset[DocumentId]:
    """The pages the seat has opened (``document_read``)."""
    return frozenset(
        e.payload.doc_id
        for e in events
        if isinstance(e.payload, ev.DocumentRead) and e.payload.seat_id == seat
    )


def aide_for(
    store: EventStore, campaign_id: CampaignId, seat: SeatId, writer: AideWriter
) -> AideBrief:
    """The seat's brief from its projection. Logs the call's cost; files nothing else."""
    state = load_state(store, campaign_id)
    if state.ruleset != "realism":
        raise ValueError("the aide is for realism campaigns")
    view = project(state, seat)
    out = brief(view, state.pipelines, read_marks(store.read(campaign_id), seat), writer)
    if out.cost is not None:
        cmp.log_cost(store, campaign_id, "aide", [out.cost], seat)
    return out
