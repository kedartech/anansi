"""The PROSE stage (Phase 4): one call per author per wake, pages only, numbers checked,
written once across a crash, and a failed call never stops the tick."""

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import get_type_hints

import pytest

from revisionist.core import events as ev
from revisionist.core.fold import state_hash
from revisionist.core.ids import DocumentId
from revisionist.core.project import project
from revisionist.core.realism.document import DocumentView
from revisionist.core.resolution import TickStage
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC
from revisionist.engine.llm import RecordedTransport, Reply, Request, Transport, TransportError
from revisionist.engine.prose import (
    Author,
    ClaudeProseWriter,
    ProseWriter,
    TemplateProseWriter,
    Written,
    template_prose,
)
from revisionist.engine.realism_tick import run_realism_tick
from revisionist.engine.store import EventStore, MemoryStore, SqliteStore, load_state
from tests.handwritten import PROSE
from tests.prose_scenario import CANARY, scenario
from tests.test_realism_tick import CAMPAIGN


class Counting:
    """Writes every page with the template and records each call's author and pages."""

    def __init__(self, invent: bool = False) -> None:
        self.calls: list[tuple[str, tuple[DocumentId, ...]]] = []
        self.invent = invent

    def write(self, author: Author, pages: Sequence[DocumentView]) -> Written:
        self.calls.append((author.name, tuple(p.doc_id for p in pages)))
        prose = {p.doc_id: template_prose(author, p) for p in pages}
        if self.invent:
            prose = {k: v + " Also 4,321 rifles." for k, v in prose.items()}
        return Written(prose=prose)


class Capturing:
    """Passes calls through to ``inner`` and keeps every request sent."""

    def __init__(self, inner: Transport) -> None:
        self.inner = inner
        self.sent: list[Request] = []

    def call(self, request: Request) -> Reply:
        self.sent.append(request)
        return self.inner.call(request)


class Failing:
    def call(self, request: Request) -> Reply:
        raise TransportError("model unavailable")


def written(store: EventStore) -> list[ev.DocumentWritten]:
    return [e.payload for e in store.read(CAMPAIGN) if isinstance(e.payload, ev.DocumentWritten)]


def test_one_call_per_author_per_wake() -> None:
    store = MemoryStore()
    cora, bram = scenario(store)
    writer = Counting()
    result = run_realism_tick(store, CAMPAIGN, prose=writer)
    state = load_state(store, CAMPAIGN)
    authors = [name for name, _ in writer.calls]
    assert sorted(authors) == ["Bram Tull", "Cora Senn"]  # two authors, two calls
    by_author = {name: set(ids) for name, ids in writer.calls}
    assert cora in by_author["Cora Senn"] and bram in by_author["Bram Tull"]
    bram_pages = {
        k for k, d in state.documents.items() if d.view.author == state.documents[bram].view.author
    }
    assert by_author["Bram Tull"] == bram_pages
    assert result.written == len(state.documents)
    assert all(d.view.prose for d in state.documents.values())
    writer.calls.clear()
    run_realism_tick(store, CAMPAIGN, prose=writer)  # the next wake writes only its new pages
    fresh = {
        k for k, d in load_state(store, CAMPAIGN).documents.items() if k not in state.documents
    }
    assert {i for _, ids in writer.calls for i in ids} == fresh


def test_stage_marker_follows_feeds_and_costs_reach_tick_closed() -> None:
    store = MemoryStore()
    scenario(store)
    run_realism_tick(store, CAMPAIGN, prose=ClaudeProseWriter(RecordedTransport(PROSE)))
    log = list(store.read(CAMPAIGN))
    stages = [e.payload.stage for e in log if isinstance(e.payload, ev.TickStageCompleted)]
    assert stages[-2:] == [TickStage.FEEDS, TickStage.PROSE]
    (closed,) = [e.payload for e in log if isinstance(e.payload, ev.TickClosed)]
    assert [c.call_class for c in closed.costs] == ["prose", "prose"]
    assert all(c.usd > 0 for c in closed.costs)
    prose_events = [e for e in log if isinstance(e.payload, ev.DocumentWritten)]
    assert all("document_prose=" in e.versions.prompt for e in prose_events)


@pytest.fixture(params=["memory", "sqlite"])
def stores(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[list[EventStore]]:
    made: list[EventStore] = [
        MemoryStore() if request.param == "memory" else SqliteStore(tmp_path / f"{n}.db")
        for n in range(2)
    ]
    yield made
    for s in made:
        if isinstance(s, SqliteStore):
            s.close()


def test_a_crash_after_feeds_resumes_and_writes_each_page_once(stores: list[EventStore]) -> None:
    whole, broken = stores
    for store in stores:
        scenario(store)
    run_realism_tick(whole, CAMPAIGN, prose=TemplateProseWriter())
    cut = run_realism_tick(
        broken, CAMPAIGN, prose=TemplateProseWriter(), stop_after=TickStage.FEEDS
    )
    assert cut.stopped_after is TickStage.FEEDS and not written(broken)
    resumed = run_realism_tick(broken, CAMPAIGN, prose=TemplateProseWriter())
    assert resumed.resumed_from is TickStage.FEEDS
    assert state_hash(load_state(broken, CAMPAIGN)) == state_hash(load_state(whole, CAMPAIGN))
    ids = [w.doc_id for w in written(broken)]
    assert len(ids) == len(set(ids)) == len(load_state(broken, CAMPAIGN).documents)
    again = run_realism_tick(broken, CAMPAIGN, prose=TemplateProseWriter())  # a later wake
    later = [w.doc_id for w in written(broken)]
    assert len(later) == len(set(later)) and again.state_hash


def test_a_figure_not_on_the_page_drops_the_prose() -> None:
    store = MemoryStore()
    scenario(store)
    result = run_realism_tick(store, CAMPAIGN, prose=Counting(invent=True))
    state = load_state(store, CAMPAIGN)
    assert written(store) == []
    assert all(d.view.prose is None for d in state.documents.values())
    assert len(result.prose_dropped) == len(state.documents)
    assert all("4321" in why for why in result.prose_dropped)
    assert any(isinstance(e.payload, ev.TickClosed) for e in store.read(CAMPAIGN))


def test_a_transport_failure_leaves_prose_none_and_closes_the_tick() -> None:
    store = MemoryStore()
    scenario(store)
    result = run_realism_tick(store, CAMPAIGN, prose=ClaudeProseWriter(Failing()))
    state = load_state(store, CAMPAIGN)
    assert result.state_hash
    assert all(d.view.prose is None for d in state.documents.values())
    assert all("call failed" in why for why in result.prose_dropped)
    (closed,) = [e.payload for e in store.read(CAMPAIGN) if isinstance(e.payload, ev.TickClosed)]
    assert closed.costs == ()


def test_the_truth_canary_reaches_no_prose_request_and_no_view() -> None:
    store = MemoryStore()
    _, bram = scenario(store)
    assert any(
        r.canary == CANARY for r in load_state(store, CAMPAIGN).documents[bram].truth.truth_refs
    )
    transport = Capturing(RecordedTransport(PROSE))
    run_realism_tick(store, CAMPAIGN, prose=ClaudeProseWriter(transport))
    assert len(transport.sent) == 2
    for req in transport.sent:
        assert CANARY not in req.system + req.prompt + json.dumps(req.schema)
        assert "truth_refs" not in req.prompt and "loyalty" not in req.prompt
    state = load_state(store, CAMPAIGN)
    assert state.documents[bram].view.prose  # the recorded prose was written
    for seat in state.seats:
        assert CANARY not in project(state, seat).model_dump_json()


def test_a_writer_takes_pages_never_state() -> None:
    hints = get_type_hints(ProseWriter.write)
    assert hints["pages"] == Sequence[DocumentView] and hints["author"] is Author
    assert State not in hints.values()
    store = MemoryStore()
    scenario(store)
    state = load_state(store, CAMPAIGN)
    with pytest.raises(TypeError):
        TemplateProseWriter().write(Author(entity=next(iter(state.entities)), name="x"), [state])  # type: ignore[list-item]
    truth = next(iter(state.documents.values())).truth
    with pytest.raises(TypeError):
        ClaudeProseWriter(Failing()).write(Author(entity=truth.doc_id, name="x"), [truth])  # type: ignore[list-item]


def test_template_prose_states_only_the_page() -> None:
    from revisionist.core.realism.prose import invented_numbers

    store = MemoryStore()
    scenario(store)
    run_realism_tick(store, CAMPAIGN, prose=TemplateProseWriter())
    for doc in load_state(store, CAMPAIGN).documents.values():
        assert doc.view.prose and invented_numbers(doc.view.prose, doc.view) == ()


def test_a_page_with_no_lines_gets_a_kind_fit_acknowledgement_and_passes_the_check() -> None:
    from revisionist.core.realism.document import DocumentKind
    from revisionist.engine.prose import accepted

    store = MemoryStore()
    scenario(store)
    run_realism_tick(store, CAMPAIGN)
    page = next(iter(load_state(store, CAMPAIGN).documents.values())).view
    author = Author(entity=page.author, name="A. Clerk")
    for kind in (DocumentKind.CABLE, DocumentKind.LETTER, DocumentKind.MEMO, DocumentKind.PRESS):
        bare = page.model_copy(update={"kind": kind, "body": (), "prose": None})
        text = template_prose(author, bare)
        assert "nothing to report" not in text
        assert accepted([bare], Written(prose={bare.doc_id: text}))[1] == []
    cable = page.model_copy(update={"kind": DocumentKind.CABLE, "body": (), "prose": None})
    assert "Message received. Fuller report follows." in template_prose(author, cable)


def test_a_private_page_is_addressed_to_the_office_that_holds_it() -> None:
    from revisionist.engine.prose import author_of, page_lines, prose_prompt

    store = MemoryStore()
    scenario(store)
    run_realism_tick(store, CAMPAIGN)
    state = load_state(store, CAMPAIGN)
    private = next(d.view for d in state.documents.values() if d.view.scope.known_by)
    author = author_of(state, private.author, [private])
    (office,) = author.to.values()
    assert office and page_lines(private, author)["to"] == office
    assert f"To the {office}" in template_prose(
        author, private.model_copy(update={"kind": "letter"})
    )
    public = private.model_copy(update={"scope": PUBLIC})
    assert author_of(state, private.author, [public]).to == {}
    assert "to" not in page_lines(public, author_of(state, private.author, [public]))
    assert "recipient" not in prose_prompt(author, [private]).lower()
