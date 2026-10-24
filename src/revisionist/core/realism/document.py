"""Documents: every metric reaches a person as a report (pillar 3).

A document has two halves. ``DocumentView`` is the page a seat reads: an
author, a date, a channel, a classification and the author's estimates.
``DocumentTruth`` is what the engine knows about that page: which truths it
was built from and the bias applied. Only the view ever enters a ``View``;
the truth half stays in ``State`` so the leak scan and the referee can see
how far an estimate is from the record.
"""

from datetime import date
from enum import StrEnum
from typing import ClassVar

from pydantic import Field, model_validator

from revisionist.core._base import Label, Model
from revisionist.core.ids import DocumentId, EntityId, FeatureId, FeedId, QuantityId
from revisionist.core.realism.card import ChannelKind
from revisionist.core.realism.geography import Sight, SightRef
from revisionist.core.visibility import Scope


class DocumentKind(StrEnum):
    REPORT = "report"
    CABLE = "cable"
    MEMO = "memo"
    LETTER = "letter"
    POLL = "poll"
    PRESS = "press"
    WHIP_COUNT = "whip_count"
    INTERCEPT = "intercept"
    TRANSCRIPT = "transcript"


class Classification(StrEnum):
    OPEN = "open"
    RESTRICTED = "restricted"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"
    MOST_SECRET = "most_secret"


class BodyField(Model):
    """One structured line of a document: what the author claims, never the truth."""

    label: Label
    value: int | Label | None = None
    """None: the author says nothing on this (an omission is still a line)."""
    unit: Label = ""
    sight: Sight | None = None
    """A line placing a feature: ``value`` names the places in words, this places them."""

    late_fields: ClassVar[frozenset[str]] = frozenset({"sight"})


class DocumentView(Model):
    """The page a seat reads. Visible once in scope and ``arrives`` has come."""

    doc_id: DocumentId
    kind: DocumentKind
    author: EntityId
    issued: date
    arrives: date
    channel: ChannelKind
    classification: Classification = Classification.OPEN
    title: Label = ""
    body: tuple[BodyField, ...] = ()
    prose: str | None = Field(default=None, max_length=4000)
    """Written by the prose model from this view and the author's voice, never from truth."""
    scope: Scope

    @model_validator(mode="after")
    def _arrives_after_issue(self) -> "DocumentView":
        if self.arrives < self.issued:
            raise ValueError(f"document {self.doc_id} arrives before it is issued")
        return self


class TruthRef(Model):
    """One truth a document was built from, and its value when issued."""

    quantity_id: QuantityId
    value: int | None
    canary: str | None = Field(default=None, max_length=60)
    """Test-only marker: must never reach any seat, since truth never does."""


class Bias(Model):
    """The transform the author's bias applied, in percent [proposed]."""

    skew_pct: int = Field(default=0, ge=-100, le=100)
    """Signed over- or under-estimate."""
    noise_pct: int = Field(default=0, ge=0, le=100)
    omit_pct: int = Field(default=0, ge=0, le=100)
    """Chance a line is left out."""
    delay_days: int = Field(default=0, ge=0, le=365)


class DocumentTruth(Model):
    """Engine only. Never in a ``View``, a snapshot or a prompt."""

    doc_id: DocumentId
    feed: FeedId | None = None
    """None for a document issued by an event rather than a feed (a leak, a transcript)."""
    truth_refs: tuple[TruthRef, ...] = ()
    bias: Bias = Bias()
    sightings: tuple[SightRef, ...] = ()

    late_fields: ClassVar[frozenset[str]] = frozenset({"sightings"})


class Document(Model):
    """A document as ``State`` holds it: both halves."""

    view: DocumentView
    truth: DocumentTruth

    @model_validator(mode="after")
    def _same_document(self) -> "Document":
        if self.view.doc_id != self.truth.doc_id:
            raise ValueError("a document's view and truth name different documents")
        return self


class Feed(Model):
    """A source of documents on a cadence (pillar 3). Whose desk it reaches is on the cards."""

    feed_id: FeedId
    name: Label
    author: EntityId
    kind: DocumentKind
    channel: ChannelKind
    classification: Classification = Classification.OPEN
    every_days: int | None = Field(default=None, ge=1, le=365)
    """None while the cadence is an unsourced gap."""
    latency_days: int | None = Field(default=None, ge=0, le=365)
    reports: tuple[QuantityId, ...] = ()
    bias: Bias = Bias()
    sights: tuple[FeatureId, ...] = ()
    """Features each issue places, after the figures."""

    late_fields: ClassVar[frozenset[str]] = frozenset({"sights"})


__all__ = [
    "Bias",
    "BodyField",
    "Classification",
    "Document",
    "DocumentKind",
    "DocumentTruth",
    "DocumentView",
    "Feed",
    "TruthRef",
]
