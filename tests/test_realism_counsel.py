"""Counsel (Phase 4): the request holds only the person's projection, the reply is the
seat's alone, the world does not move, and truth never reaches the model."""

import json
from collections.abc import Sequence
from typing import get_type_hints

import pytest

from revisionist.core import events as ev
from revisionist.core.fold import state_hash
from revisionist.core.project import project
from revisionist.core.realism.card import ChannelKind
from revisionist.core.realism.person import PersonView, project_person
from revisionist.core.report import leak_report
from revisionist.core.sealing import seal_holder
from revisionist.core.state import State
from revisionist.core.visibility import Visibility
from revisionist.engine.counsel import (
    Asker,
    ClaudeCounsellor,
    Counsellor,
    CounselRefusedError,
    Spoken,
    consult,
    counsel_prompt,
    counsel_records,
)
from revisionist.engine.llm import RecordedTransport, Reply, Request, TransportError
from revisionist.engine.store import EventStore, MemoryStore, load_state
from tests.counsel_scenario import CANARY, QUESTION, hopkins, plant_memo, roosevelt_seat, ticked
from tests.handwritten import COUNSEL
from tests.test_realism_tick import CAMPAIGN


class Capturing:
    def __init__(self) -> None:
        self.inner = RecordedTransport(COUNSEL)
        self.sent: list[Request] = []

    def call(self, request: Request) -> Reply:
        self.sent.append(request)
        return self.inner.call(request)


def ready() -> EventStore:
    store = MemoryStore()
    ticked(store)
    plant_memo(store)
    return store


def held(store: EventStore) -> list[ev.Event]:
    return [e for e in store.read(CAMPAIGN) if isinstance(e.payload, ev.CounselHeld)]


def test_the_request_holds_only_the_persons_projection() -> None:
    store = ready()
    state = load_state(store, CAMPAIGN)
    seat, person = roosevelt_seat(store), hopkins(store)
    transport = Capturing()
    consult(store, CAMPAIGN, seat, person, QUESTION, ClaudeCounsellor(transport))
    (req,) = transport.sent
    asker = Asker(
        name="Franklin D. Roosevelt", office=state.cards[state.seats[seat].embodies].office
    )
    seen = project_person(state, person)
    assert req.prompt == counsel_prompt(seen, asker, ChannelKind.IN_PERSON, QUESTION)
    assert req.call_class == "counsel"
    mine = project(state, seat).documents
    not_his = [d for k, d in mine.items() if k not in {x.doc_id for x in seen.documents}]
    assert not_his, "Roosevelt should hold pages Hopkins does not"
    for page in not_his:
        assert page.title not in req.prompt
    for page in seen.documents:
        assert page.title in req.prompt


def test_the_reply_is_private_to_the_seat_and_its_cost_is_logged() -> None:
    store = ready()
    seat = roosevelt_seat(store)
    out = consult(store, CAMPAIGN, seat, hopkins(store), QUESTION, ClaudeCounsellor(Capturing()))
    assert "500 a month" in out.text and out.channel is ChannelKind.IN_PERSON
    (record,) = held(store)
    assert record.scope.visibility is Visibility.PRIVATE and record.scope.known_by == {seat}
    assert seal_holder(record) == seat
    assert [p.text for p in counsel_records(list(store.read(CAMPAIGN)), seat)] == [out.text]
    (cost,) = [e.payload for e in store.read(CAMPAIGN) if isinstance(e.payload, ev.CostLogged)]
    assert cost.purpose == "counsel" and cost.costs[0].call_class == "counsel"


def test_counsel_changes_nothing_in_the_world() -> None:
    store = ready()
    before = load_state(store, CAMPAIGN)
    consult(
        store,
        CAMPAIGN,
        roosevelt_seat(store),
        hopkins(store),
        QUESTION,
        ClaudeCounsellor(Capturing()),
    )
    after = load_state(store, CAMPAIGN)
    assert after.last_seq == before.last_seq + 2  # counsel_held and cost_logged, both records
    assert state_hash(after.model_copy(update={"last_seq": before.last_seq})) == state_hash(before)
    assert after.orders == before.orders and after.in_world_date == before.in_world_date


def test_the_truth_canary_never_reaches_a_counsel_request() -> None:
    store = ready()
    state = load_state(store, CAMPAIGN)
    assert any(r.canary == CANARY for d in state.documents.values() for r in d.truth.truth_refs)
    transport = Capturing()
    consult(
        store,
        CAMPAIGN,
        roosevelt_seat(store),
        hopkins(store),
        QUESTION,
        ClaudeCounsellor(transport),
    )
    for req in transport.sent:
        assert CANARY not in req.system + req.prompt + json.dumps(req.schema)
        for secret in ("truth_refs", "loyalty", "doctrine", "interests"):
            assert secret not in req.prompt
    assert leak_report(load_state(store, CAMPAIGN), store.read(CAMPAIGN)) == []


def test_only_a_person_on_the_card_can_be_consulted() -> None:
    store = ready()
    state = load_state(store, CAMPAIGN)
    seat = roosevelt_seat(store)
    reach = {c.person for c in state.cards[state.seats[seat].embodies].contacts}
    stranger = next(e for e in state.cards if e not in reach and e != state.seats[seat].embodies)
    with pytest.raises(CounselRefusedError, match="within your reach"):
        consult(store, CAMPAIGN, seat, stranger, QUESTION, ClaudeCounsellor(Capturing()))
    with pytest.raises(CounselRefusedError):
        consult(store, CAMPAIGN, seat, hopkins(store), "   ", ClaudeCounsellor(Capturing()))
    assert held(store) == []


def test_a_failed_call_writes_nothing() -> None:
    class Down:
        def call(self, request: Request) -> Reply:
            raise TransportError("model unavailable")

    store = ready()
    before = load_state(store, CAMPAIGN).last_seq
    with pytest.raises(TransportError):
        consult(
            store,
            CAMPAIGN,
            roosevelt_seat(store),
            hopkins(store),
            QUESTION,
            ClaudeCounsellor(Down()),
        )
    assert load_state(store, CAMPAIGN).last_seq == before


def test_a_counsellor_takes_a_person_view_never_state() -> None:
    hints = get_type_hints(Counsellor.speak)
    assert hints["person"] is PersonView and State not in hints.values()
    store = ready()
    state = load_state(store, CAMPAIGN)
    with pytest.raises(TypeError):
        counsel_prompt(state, Asker(name="x"), ChannelKind.LETTER, "q")  # type: ignore[arg-type]

    class Echo:
        def speak(
            self, person: PersonView, asker: Asker, channel: ChannelKind, question: str
        ) -> Spoken:
            return Spoken(text=f"{len(person.documents)} papers")

    seq: Sequence[Counsellor] = [Echo()]
    out = consult(store, CAMPAIGN, roosevelt_seat(store), hopkins(store), QUESTION, seq[0])
    assert out.text.endswith("papers")
