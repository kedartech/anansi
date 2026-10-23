"""Feeds: documents that report the truth through an author's bias (pillar 3).

``due_feeds`` says which feeds owe a document today; ``issue`` writes one. The
bias is arithmetic on the true figure, drawn from a seed, so a replay writes the
same page. No model is involved: prose is added later, from the view alone.
"""

import hashlib
from datetime import date, timedelta

from revisionist.core import events as ev
from revisionist.core.ids import CampaignId, DocumentId, SeatId, derived_id
from revisionist.core.realism.document import (
    BodyField,
    DocumentTruth,
    DocumentView,
    Feed,
    TruthRef,
)
from revisionist.core.state import State
from revisionist.core.visibility import Scope, Visibility


def seed_for_day(campaign_id: CampaignId, today: date) -> str:
    return hashlib.blake2b(
        f"{campaign_id}\x1f{today.isoformat()}".encode(), digest_size=16
    ).hexdigest()


def _start(state: State, feed: Feed) -> date | None:
    """The campaign start as this feed sees it: the earliest ``as_of`` among the
    quantities it reports, else today's in-world date."""
    dates = [state.quantities[q].as_of for q in feed.reports if q in state.quantities]
    return min(dates) if dates else state.in_world_date


def _last_issued(state: State, feed: Feed) -> date | None:
    issued = [d.view.issued for d in state.documents.values() if d.truth.feed == feed.feed_id]
    return max(issued) if issued else None


def next_issue(state: State, feed: Feed) -> date | None:
    """The day ``feed`` next owes a document, or None for a gap cadence. The first
    issue falls due at the start (see ``_start``), the next ``every_days`` after the
    last one issued."""
    if feed.every_days is None:
        return None
    last = _last_issued(state, feed)
    return _start(state, feed) if last is None else last + timedelta(days=feed.every_days)


def due_feeds(state: State, today: date) -> list[Feed]:
    """Feeds owing a document on or before ``today``. A gap cadence never fires."""
    due: list[Feed] = []
    for feed in state.feeds.values():
        when = next_issue(state, feed)
        if when is not None and when <= today:
            due.append(feed)
    return due


def _draw(seed: str, feed: Feed, quantity: str, tag: str, n: int) -> int:
    """A deterministic integer in [0, n)."""
    key = f"{seed}\x1f{feed.feed_id}\x1f{quantity}\x1f{tag}"
    return int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "big") % n


def _report(seed: str, feed: Feed, quantity: str, true: int) -> int | None:
    """What the author claims for one figure: skewed, noised, or left out."""
    bias = feed.bias
    if _draw(seed, feed, quantity, "omit", 100) < bias.omit_pct:
        return None
    noise = _draw(seed, feed, quantity, "noise", 2 * bias.noise_pct + 1) - bias.noise_pct
    return (true * (100 + bias.skew_pct + noise) + 50) // 100


def _holders(state: State, feed: Feed) -> frozenset[SeatId]:
    persons = {p for p, c in state.cards.items() if feed.feed_id in c.feeds}
    return frozenset(s.seat_id for s in state.seats.values() if s.embodies in persons)


def issue(state: State, feed: Feed, today: date, seed: str) -> ev.DocumentIssued | None:
    """The document ``feed`` issues today, or None when no seat holds the feed
    (a non-public scope needs a holder, and a feed no one receives is not a document)."""
    holders = _holders(state, feed)
    if not holders:
        return None
    refs: list[TruthRef] = []
    body: list[BodyField] = []
    for qid in feed.reports:
        q = state.quantities[qid]
        refs.append(TruthRef(quantity_id=qid, value=q.value))
        claimed = None if q.value is None else _report(seed, feed, qid, q.value)
        body.append(BodyField(label=q.name, value=claimed, unit=q.unit))
    doc_id = DocumentId(derived_id(state.campaign_id, feed.feed_id, today.isoformat()))
    arrives = today + timedelta(days=(feed.latency_days or 0) + feed.bias.delay_days)
    view = DocumentView(
        doc_id=doc_id,
        kind=feed.kind,
        author=feed.author,
        issued=today,
        arrives=arrives,
        channel=feed.channel,
        classification=feed.classification,
        title=f"{feed.name}, {today.isoformat()}",
        body=tuple(body),
        scope=Scope(visibility=Visibility.PRIVATE, known_by=holders),
    )
    truth = DocumentTruth(doc_id=doc_id, feed=feed.feed_id, truth_refs=tuple(refs), bias=feed.bias)
    return ev.DocumentIssued(document=view, truth=truth)
