"""Document prose (realism, Phase 4): a page's body lines written out in its author's voice.

The prose model reads pages, never truth: a ``ProseWriter`` takes ``DocumentView``s
and an ``Author`` (a name and the card's voice), and passing a ``State`` or a
``DocumentTruth`` is a type error before it is a leak. The engine batches pages by
author, one call per author per wake, and checks every number in the reply against
the page (``core.realism.prose``); a page whose prose states a figure it does not
carry keeps its body lines alone.

``TemplateProseWriter`` is the offline path: plain period sentences, no model.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import Field

from revisionist.core._base import Model
from revisionist.core.events import CallCost
from revisionist.core.ids import DocumentId, EntityId
from revisionist.core.realism.document import DocumentKind, DocumentView
from revisionist.core.realism.prose import invented_numbers
from revisionist.core.state import State
from revisionist.engine.llm import Request, Transport, prompt_text, prompt_version

PROSE_MODEL = "claude-sonnet-5-5"
"""[proposed] The P0 A/B (``scripts/prose_ab.py``) decides; ``REVISIONIST_MODEL_PROSE``
overrides it per call, as for every call class."""
log = logging.getLogger(__name__)


class Author(Model):
    """Who wrote a batch: what the prose model may know of them."""

    entity: EntityId
    name: str
    office: str = ""
    voice: str = ""
    to: dict[DocumentId, str] = Field(default_factory=dict)
    """The office each private page is addressed to; a public page has none."""


class Written(Model):
    """A writer's reply: prose by page, and what the call cost."""

    prose: dict[DocumentId, str]
    cost: CallCost | None = None


class ProseWriter(Protocol):
    """Pages and their author in, prose out. Takes views only; never a ``State``."""

    def write(self, author: Author, pages: Sequence[DocumentView]) -> Written: ...


def _addressee(state: State, page: DocumentView) -> str:
    """The office on the card of each person a holder seat embodies, joined with " and ".
    Empty for a public page, or when no holder has a card."""
    offices = [
        card.office
        for seat in sorted(page.scope.known_by)
        if (card := state.cards.get(state.seats[seat].embodies)) is not None
    ]
    return " and ".join(offices)


def author_of(state: State, entity: EntityId, pages: Sequence[DocumentView] = ()) -> Author:
    """The author block for ``entity``: the name, the office, the card's voice and who each
    of ``pages`` is addressed to. Read here, from the state, so no writer ever needs one."""
    card = state.cards.get(entity)
    return Author(
        entity=entity,
        name=state.entities[entity].name,
        office=card.office if card is not None else "",
        voice=card.voice if card is not None else "",
        to={p.doc_id: to for p in pages if (to := _addressee(state, p))},
    )


def _views_only(pages: Sequence[object]) -> Sequence[DocumentView]:
    for page in pages:
        if not isinstance(page, DocumentView):
            raise TypeError(f"a prose writer takes DocumentView pages, not {type(page).__name__}")
    return pages  # type: ignore[return-value]


# --- the model ---------------------------------------------------------------------


PROSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"doc_id": {"type": "string"}, "prose": {"type": "string"}},
                "required": ["doc_id", "prose"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["pages"],
    "additionalProperties": False,
}


def page_lines(page: DocumentView, author: Author) -> dict[str, Any]:
    """One page as the prompt shows it: the view's fields, omitted lines included."""
    return {
        "doc_id": page.doc_id,
        "kind": page.kind.value,
        "title": page.title,
        "author": author.name,
        **({"to": author.to[page.doc_id]} if page.doc_id in author.to else {}),
        "issued": page.issued.isoformat(),
        "arrives": page.arrives.isoformat(),
        "channel": page.channel.value,
        "classification": page.classification.value,
        "lines": [
            {
                "label": line.label,
                "value": "not stated" if line.value is None else line.value,
                **({"unit": line.unit} if line.unit else {}),
            }
            for line in page.body
        ],
    }


def prose_prompt(author: Author, pages: Sequence[DocumentView]) -> str:
    """The author block first, so a provider cache can reuse it across wakes; the pages last."""
    head = f"## Author\n{author.name}" + (f", {author.office}" if author.office else "")
    voice = f"\nVoice: {author.voice}" if author.voice else ""
    body = json.dumps([page_lines(p, author) for p in pages], ensure_ascii=False)
    return f"{head}{voice}\n\n## Pages to write\n{body}\n\nWrite each page."


@dataclass
class ClaudeProseWriter:
    """One call per batch through ``Transport``. Never sees ``State``."""

    transport: Transport
    model: str = PROSE_MODEL
    max_usd: float = 0.25
    requests: list[Request] = field(default_factory=list)

    def request(self, author: Author, pages: Sequence[DocumentView]) -> Request:
        return Request(
            call_class="prose",
            model=self.model,
            system=prompt_text("document_prose"),
            prompt=prose_prompt(author, _views_only(pages)),
            schema=PROSE_SCHEMA,
            max_usd=self.max_usd,
        )

    def write(self, author: Author, pages: Sequence[DocumentView]) -> Written:
        req = self.request(author, pages)
        self.requests.append(req)
        reply = self.transport.call(req)
        out: dict[DocumentId, str] = {}
        items = reply.output.get("pages", []) if isinstance(reply.output, dict) else []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("prose"), str):
                out[DocumentId(str(item.get("doc_id")))] = item["prose"].strip()
        return Written(prose=out, cost=reply.cost("prose"))


# --- offline -------------------------------------------------------------------------


_OPENING: dict[DocumentKind, str] = {
    DocumentKind.LETTER: "Dear Sir,\n\n",
    DocumentKind.MEMO: "Memorandum.\n\n",
    DocumentKind.CABLE: "",
    DocumentKind.WHIP_COUNT: "Sir,\n\nMy count stands as follows. ",
}

_ADDRESSED_OPENING: dict[DocumentKind, str] = {
    DocumentKind.LETTER: "To the {to}:\n\n",
    DocumentKind.MEMO: "Memorandum for the {to}.\n\n",
    DocumentKind.WHIP_COUNT: "To the {to}:\n\nMy count stands as follows. ",
}


_ACKNOWLEDGEMENT: dict[DocumentKind, str] = {
    DocumentKind.CABLE: "Message received. Fuller report follows.",
    DocumentKind.LETTER: "I acknowledge your last and will send fuller particulars shortly.",
    DocumentKind.MEMO: "Noted. A fuller paper will follow.",
}


def _figure(value: int | str) -> str:
    return f"{value:,}" if isinstance(value, int) else value


def template_prose(author: Author, page: DocumentView) -> str:
    """The page's lines as plain period sentences. States no figure the page lacks."""
    said: list[str] = []
    for line in page.body:
        if line.value is None:
            said.append(f"On {line.label.lower()} I can give you nothing firm at present.")
        else:
            unit = f" {line.unit}" if line.unit else ""
            said.append(f"{line.label}: {_figure(line.value)}{unit}.")
    if not said:
        said.append(_ACKNOWLEDGEMENT.get(page.kind, "Your attention is drawn to this matter."))
    stop = " STOP " if page.kind is DocumentKind.CABLE else " "
    to = author.to.get(page.doc_id)
    opening = (
        _ADDRESSED_OPENING[page.kind].format(to=to)
        if to and page.kind in _ADDRESSED_OPENING
        else _OPENING.get(page.kind, "I have the honour to report as follows. ")
    )
    return f"{opening}{stop.join(said)}\n\n{author.name}"


class TemplateProseWriter:
    """The offline writer: no model, no cost."""

    def __init__(self) -> None:
        self.calls = 0

    def write(self, author: Author, pages: Sequence[DocumentView]) -> Written:
        self.calls += 1
        return Written(prose={p.doc_id: template_prose(author, p) for p in _views_only(pages)})


# --- the check -------------------------------------------------------------------------


@dataclass(frozen=True)
class Rejected:
    doc_id: DocumentId
    reason: str


def accepted(
    pages: Sequence[DocumentView], written: Written
) -> tuple[dict[DocumentId, str], list[Rejected]]:
    """The prose that passes: one per page asked for, non-empty, within length and
    stating no figure the page lacks. The rest is logged and dropped."""
    keep: dict[DocumentId, str] = {}
    dropped: list[Rejected] = []
    for page in pages:
        text = written.prose.get(page.doc_id)
        if not text:
            dropped.append(Rejected(page.doc_id, "no prose returned"))
            continue
        if len(text) > 4000:
            dropped.append(Rejected(page.doc_id, f"{len(text)} characters, over 4000"))
            continue
        invented = invented_numbers(text, page)
        if invented:
            dropped.append(Rejected(page.doc_id, f"states figures not on the page: {invented}"))
            continue
        keep[page.doc_id] = text
    for r in dropped:
        log.warning("prose for %s dropped: %s", r.doc_id, r.reason)
    return keep, dropped


def prose_prompt_version() -> str:
    return "document_prose=" + prompt_version("document_prose")


__all__ = [
    "PROSE_MODEL",
    "PROSE_SCHEMA",
    "Author",
    "ClaudeProseWriter",
    "ProseWriter",
    "Rejected",
    "TemplateProseWriter",
    "Written",
    "accepted",
    "author_of",
    "prose_prompt",
    "prose_prompt_version",
    "template_prose",
]
