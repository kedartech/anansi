"""Phase 4 contract: ``document_written`` folds once into the page, its prose is
scanned like the page, a person's projection carries no truth, and the number
post-check rejects a figure the page does not state."""

from datetime import date

import pytest

from revisionist.core import events as ev
from revisionist.core.canary import scan, surfaces_from_log
from revisionist.core.fold import FoldError, replay
from revisionist.core.ids import DocumentId, EntityId, derived_id
from revisionist.core.project import project
from revisionist.core.realism.card import ChannelKind
from revisionist.core.realism.document import BodyField, DocumentKind, DocumentView
from revisionist.core.realism.person import PersonView, project_person
from revisionist.core.realism.prose import invented_numbers, numbers_in
from revisionist.core.visibility import PUBLIC, private_to
from tests.test_realism_contract import CAMPAIGN, TRUTH_CANARY, Toy


def _written(toy: Toy, n: int = 1) -> tuple[DocumentId, ev.DocumentIssued]:
    issued = toy.document(n, arrives_in=0, scope=private_to(toy.ada))
    toy.add(issued, scope=issued.document.scope)
    return issued.document.doc_id, issued


def test_document_written_folds_into_the_view_and_projects() -> None:
    toy = Toy()
    doc_id, issued = _written(toy)
    toy.add(
        ev.DocumentWritten(doc_id=doc_id, prose="Eight votes, I make it."), issued.document.scope
    )
    state = toy.state
    assert state.documents[doc_id].view.prose == "Eight votes, I make it."
    assert project(state, toy.ada).documents[doc_id].prose == "Eight votes, I make it."
    assert doc_id not in project(state, toy.bram).documents


def test_a_document_is_written_once() -> None:
    toy = Toy()
    doc_id, issued = _written(toy)
    toy.add(ev.DocumentWritten(doc_id=doc_id, prose="First."), issued.document.scope)
    toy.add(ev.DocumentWritten(doc_id=doc_id, prose="Second."), issued.document.scope)
    with pytest.raises(FoldError, match="already written"):
        replay(toy.events)


def test_prose_for_an_unknown_document_is_refused() -> None:
    toy = Toy()
    toy.add(ev.DocumentWritten(doc_id=DocumentId(derived_id(CAMPAIGN, "nope")), prose="x"))
    with pytest.raises(FoldError, match="unknown document"):
        replay(toy.events)


def test_a_truth_canary_in_prose_is_a_leak_to_every_reader() -> None:
    toy = Toy()
    doc_id, issued = _written(toy)
    bad = toy.add(
        ev.DocumentWritten(doc_id=doc_id, prose=f"Truly {TRUTH_CANARY}."), issued.document.scope
    )
    state = toy.state
    leaks = scan(state, toy.events)
    assert [(x.seat_id, x.event_id, x.kind) for x in leaks] == [
        (toy.ada, bad.event_id, "document_written")
    ]
    seen = surfaces_from_log(
        toy.events, {"ada": frozenset({toy.ada}), "bram": frozenset({toy.bram})}
    )
    assert any(TRUTH_CANARY in text for _, text in seen["ada"])
    assert not any(TRUTH_CANARY in text for _, text in seen["bram"])


def test_a_person_holds_their_own_pages_and_their_feeds_never_truth() -> None:
    toy = Toy()
    _, issued = _written(toy)
    later = toy.document(2, arrives_in=5, scope=private_to(toy.ada))
    toy.add(later, scope=later.document.scope)
    state = toy.state
    bram = toy.entity("bram")
    seen = project_person(state, bram)  # Bram wrote both; only the arrived one is held
    assert [d.doc_id for d in seen.documents] == [issued.document.doc_id]
    assert seen.voice == state.cards[bram].voice
    dump = seen.model_dump_json()
    assert TRUTH_CANARY not in dump
    for policy in ("loyalty", "doctrine", "interests", "truth_refs", "levers"):
        assert policy not in dump
    assert set(PersonView.model_fields) == {
        "person",
        "name",
        "face",
        "voice",
        "today",
        "documents",
    }
    ada = project_person(state, toy.entity("ada"))  # Ada's seat holds what reached her
    assert [d.doc_id for d in ada.documents] == [issued.document.doc_id]


def test_a_person_projection_needs_a_known_person() -> None:
    with pytest.raises(KeyError):
        project_person(Toy().state, EntityId("nobody"))


def _page(*body: BodyField, title: str = "") -> DocumentView:
    return DocumentView(
        doc_id=DocumentId("d1"),
        kind=DocumentKind.REPORT,
        author=EntityId("a"),
        issued=date(1941, 10, 14),
        arrives=date(1941, 10, 16),
        channel=ChannelKind.CABLE,
        title=title,
        body=body,
        scope=PUBLIC,
    )


def test_numbers_are_read_with_separators_and_decimals() -> None:
    assert numbers_in("1,200 tons, 3.5 knots, 14th") == ["1200", "3.5", "14"]


def test_the_post_check_allows_the_page_and_rejects_invented_figures() -> None:
    page = _page(
        BodyField(label="Tonnage sunk", value=1200, unit="tons"),
        BodyField(label="Escorts", value=None),
        title="Atlantic summary, 1941-10-14",
    )
    assert invented_numbers("We lost 1,200 tons by the 14th October 1941.", page) == ()
    assert invented_numbers("Some 1200.0 tons; due 16 October.", page) == ()
    assert invented_numbers("We lost 1,300 tons and 4 escorts; 4 more.", page) == ("1300", "4")


def test_the_post_check_takes_a_batch_of_pages() -> None:
    a = _page(BodyField(label="Yeas", value=218))
    b = _page(BodyField(label="Nays", value=21))
    assert invented_numbers("218 to 21", [a, b]) == ()
    assert invented_numbers("218 to 22", [a]) == ("22",)
