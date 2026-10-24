"""Directives (Phase 4): the aide reads the player's words as one lever from the seat's
View, code checks the reading, and only the player's signature files it."""

import json
from typing import get_type_hints

import pytest

from revisionist.core import events as ev
from revisionist.core.fold import state_hash
from revisionist.core.ids import EntityId, SeatId
from revisionist.core.project import project
from revisionist.core.realism.card import ChannelKind
from revisionist.core.report import leak_report
from revisionist.core.sealing import seal_holder
from revisionist.core.state import State, View
from revisionist.core.visibility import PUBLIC, Visibility, private_to
from revisionist.engine.directive import (
    UNREACHABLE,
    UNREADABLE,
    ClaudeDirectiveReader,
    DirectiveReader,
    DirectiveRefusedError,
    Proposal,
    Reading,
    directive_prompt,
    propose,
    sign,
)
from revisionist.engine.llm import RecordedTransport, Reply, Request, TransportError
from revisionist.engine.store import EventStore, MemoryStore, load_state
from tests.counsel_scenario import CANARY, plant_memo, roosevelt_seat, ticked
from tests.directive_scenario import (
    CABLE,
    CANAL,
    CHURCHILL,
    EVERYTHING,
    HOPKINS,
    HULL,
    KNOX,
    RAYBURN,
    SHOOT,
    WAR,
)
from tests.handwritten import DIRECTIVE
from tests.test_realism_tick import CAMPAIGN


class Capturing:
    def __init__(self) -> None:
        self.inner = RecordedTransport(DIRECTIVE)
        self.sent: list[Request] = []

    def call(self, request: Request) -> Reply:
        self.sent.append(request)
        return self.inner.call(request)


def ready() -> EventStore:
    store = MemoryStore()
    ticked(store)
    return store


def given(store: EventStore) -> list[ev.Event]:
    return [e for e in store.read(CAMPAIGN) if isinstance(e.payload, ev.DirectiveGiven)]


def test_a_cable_to_churchill_reads_as_the_new_lever_and_files_nothing() -> None:
    store = ready()
    seat = roosevelt_seat(store)
    before = load_state(store, CAMPAIGN)
    transport = Capturing()
    out = propose(
        store,
        CAMPAIGN,
        seat,
        CHURCHILL(store),
        ChannelKind.CABLE,
        CABLE,
        ClaudeDirectiveReader(transport),
    )
    assert out.lever == "cable_to_churchill" and out.reason == ""
    assert out.params == {"message": "Our destroyers will escort the convoys as far as Iceland."}
    (req,) = transport.sent
    assert req.call_class == "aide" and req.model == "claude-haiku-5-5"
    after = load_state(store, CAMPAIGN)
    assert after.orders == before.orders and given(store) == []
    (cost,) = [e for e in store.read(CAMPAIGN) if isinstance(e.payload, ev.CostLogged)]
    assert isinstance(cost.payload, ev.CostLogged) and cost.payload.purpose == "directive"
    assert cost.scope == private_to(seat)
    assert state_hash(after.model_copy(update={"last_seq": before.last_seq})) == state_hash(before)


def test_a_memo_maps_to_a_lever_with_choices() -> None:
    store = ready()
    out = propose(
        store,
        CAMPAIGN,
        roosevelt_seat(store),
        KNOX(store),
        ChannelKind.LETTER,
        SHOOT,
        ClaudeDirectiveReader(Capturing()),
    )
    assert (out.lever, out.params) == (
        "naval_order",
        {"order": "shoot_on_sight", "zone": "iceland_route"},
    )


def test_what_no_lever_does_comes_back_with_the_aides_reason() -> None:
    store = ready()
    out = propose(
        store,
        CAMPAIGN,
        roosevelt_seat(store),
        RAYBURN(store),
        ChannelKind.TELEPHONE,
        WAR,
        ClaudeDirectiveReader(Capturing()),
    )
    assert out.lever == "" and out.params == {}
    assert out.reason.startswith("Only Congress can declare war.")


def test_code_refuses_a_reading_that_does_not_fit_and_a_reason_in_engine_words() -> None:
    store = ready()
    seat = roosevelt_seat(store)
    reader = ClaudeDirectiveReader(Capturing())
    unfit = propose(
        store, CAMPAIGN, seat, HOPKINS(store), ChannelKind.IN_PERSON, EVERYTHING, reader
    )
    assert (unfit.lever, unfit.params, unfit.reason) == ("", {}, UNREADABLE)
    snake = propose(store, CAMPAIGN, seat, HULL(store), ChannelKind.LETTER, CANAL, reader)
    assert (snake.lever, snake.reason) == ("", UNREADABLE)


def test_signing_files_the_directive_and_the_order_in_one_batch() -> None:
    store = ready()
    seat = roosevelt_seat(store)
    out = propose(
        store,
        CAMPAIGN,
        seat,
        CHURCHILL(store),
        ChannelKind.CABLE,
        CABLE,
        ClaudeDirectiveReader(Capturing()),
    )
    before = load_state(store, CAMPAIGN).last_seq
    order = sign(store, CAMPAIGN, seat, out)
    log = list(store.read(CAMPAIGN))
    record, drafted = log[-2:]
    assert [record.seq, drafted.seq] == [before + 1, before + 2]
    assert isinstance(record.payload, ev.DirectiveGiven)
    assert isinstance(drafted.payload, ev.OrderDrafted)
    assert record.scope.visibility is Visibility.PRIVATE and record.scope.known_by == {seat}
    assert seal_holder(record) == seat and drafted.scope == PUBLIC
    assert record.payload.text == CABLE and record.payload.channel is ChannelKind.CABLE
    assert record.payload.order_id == order.order_id == drafted.payload.order.order_id
    assert (record.payload.lever, record.payload.params) == (order.lever, order.params)
    state = load_state(store, CAMPAIGN)
    assert state.pipelines[state.orders[order.order_id].pipeline].name == (
        "A cable to the Prime Minister"
    )


def test_signing_checks_everything_again() -> None:
    store = ready()
    seat = roosevelt_seat(store)
    good = Proposal(
        person=KNOX(store),
        channel=ChannelKind.LETTER,
        text=SHOOT,
        lever="naval_order",
        params={"order": "shoot_on_sight", "zone": "iceland_route"},
    )
    before = load_state(store, CAMPAIGN).last_seq
    bad = [
        good.model_copy(update={"lever": "declare_war"}),
        good.model_copy(update={"params": {"order": "sink_everything", "zone": "pacific"}}),
        good.model_copy(update={"params": {"order": "shoot_on_sight"}}),
        good.model_copy(update={"channel": ChannelKind.CABLE}),
        good.model_copy(update={"person": seat_person(store)}),
        good.model_copy(update={"text": "   "}),
        Proposal(
            person=CHURCHILL(store),
            channel=ChannelKind.CABLE,
            text=CABLE,
            lever="cable_to_churchill",
            params={"message": "  "},
        ),
    ]
    for proposal in bad:
        with pytest.raises(ValueError):
            sign(store, CAMPAIGN, seat, proposal)
    assert load_state(store, CAMPAIGN).last_seq == before
    sign(store, CAMPAIGN, seat, good)
    assert len(given(store)) == 1


def seat_person(store: EventStore) -> EntityId:
    state = load_state(store, CAMPAIGN)
    reach = {c.person for c in state.cards[state.seats[roosevelt_seat(store)].embodies].contacts}
    return next(e for e in state.cards if e not in reach)


def test_only_a_contact_by_a_channel_that_reaches_them() -> None:
    store = ready()
    seat = roosevelt_seat(store)
    reader = ClaudeDirectiveReader(Capturing())
    with pytest.raises(DirectiveRefusedError, match="within your reach"):
        propose(store, CAMPAIGN, seat, seat_person(store), ChannelKind.LETTER, SHOOT, reader)
    with pytest.raises(DirectiveRefusedError, match="that way"):
        propose(store, CAMPAIGN, seat, CHURCHILL(store), ChannelKind.LETTER, CABLE, reader)
    with pytest.raises(DirectiveRefusedError, match="that way"):
        propose(store, CAMPAIGN, seat, HULL(store), ChannelKind.CABLE, CANAL, reader)
    with pytest.raises(DirectiveRefusedError):
        propose(store, CAMPAIGN, seat, HULL(store), ChannelKind.LETTER, " ", reader)
    assert not reader.requests


def test_a_failed_call_is_a_reason_and_writes_nothing() -> None:
    class Down:
        def call(self, request: Request) -> Reply:
            raise TransportError("model unavailable")

    store = ready()
    before = load_state(store, CAMPAIGN).last_seq
    out = propose(
        store,
        CAMPAIGN,
        roosevelt_seat(store),
        CHURCHILL(store),
        ChannelKind.CABLE,
        CABLE,
        ClaudeDirectiveReader(Down()),
    )
    assert (out.lever, out.reason) == ("", UNREACHABLE)
    assert load_state(store, CAMPAIGN).last_seq == before


def test_the_reader_takes_a_view_never_state() -> None:
    hints = get_type_hints(DirectiveReader.read)
    assert hints["view"] is View and State not in hints.values()
    state = load_state(ready(), CAMPAIGN)
    with pytest.raises(TypeError):
        directive_prompt(state, "Cordell Hull", ChannelKind.LETTER, CANAL)  # type: ignore[arg-type]


def test_the_truth_canary_never_reaches_a_directive_request() -> None:
    store = ready()
    plant_memo(store)
    transport = Capturing()
    propose(
        store,
        CAMPAIGN,
        roosevelt_seat(store),
        CHURCHILL(store),
        ChannelKind.CABLE,
        CABLE,
        ClaudeDirectiveReader(transport),
    )
    for req in transport.sent:
        assert CANARY not in req.system + req.prompt + json.dumps(req.schema)
        for secret in ("truth_refs", "loyalty", "doctrine", "interests", "voice"):
            assert secret not in req.prompt
    assert leak_report(load_state(store, CAMPAIGN), store.read(CAMPAIGN)) == []


def test_another_seat_never_sees_a_directive() -> None:
    store = ready()
    seat = roosevelt_seat(store)
    out = Proposal(
        person=CHURCHILL(store),
        channel=ChannelKind.CABLE,
        text=CABLE,
        lever="cable_to_churchill",
        params={"message": "Escorts to Iceland."},
    )
    sign(store, CAMPAIGN, seat, out)
    (record,) = given(store)
    state = load_state(store, CAMPAIGN)
    others = [s for s in state.seats if s != seat] or [SeatId("01ARZ3NDEKTSV4RRFFQ69G5FAV")]
    for other in others:
        assert not record.scope.visible_to(other)
    view = project(state, seat)
    assert CABLE not in view.model_dump_json()  # the record is a record, not state


def test_a_reader_returning_no_cost_logs_none() -> None:
    class Quiet:
        def read(self, view: View, person: str, channel: ChannelKind, text: str) -> Reading:
            return Reading(lever="", reason="Not from this desk.")

    store = ready()
    before = load_state(store, CAMPAIGN).last_seq
    out = propose(
        store, CAMPAIGN, roosevelt_seat(store), HULL(store), ChannelKind.LETTER, CANAL, Quiet()
    )
    assert out.reason == "Not from this desk."
    assert load_state(store, CAMPAIGN).last_seq == before
