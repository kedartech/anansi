"""The aide (Phase 4): reads only the seat's View, lists the decisions waiting on the
human, drafts orders that fit the seat's levers, and files nothing."""

import json
from typing import get_type_hints

import pytest

from revisionist.core import events as ev
from revisionist.core.fold import state_hash
from revisionist.core.ids import DocumentId, EventId
from revisionist.core.project import project
from revisionist.core.realism.pipelines import waiting_on_human
from revisionist.core.state import State, View
from revisionist.core.visibility import private_to
from revisionist.engine import campaign as cmp
from revisionist.engine import realism_tick
from revisionist.engine.aide import (
    ClaudeAide,
    Draft,
    TemplateAide,
    aide_for,
    brief,
    decisions_due,
    template_drafts,
    unread,
    valid,
)
from revisionist.engine.llm import RecordedTransport, Reply, Request
from revisionist.engine.prose import TemplateProseWriter
from revisionist.engine.realism_tick import run_realism_tick
from revisionist.engine.store import EventStore, MemoryStore, load_state
from tests.counsel_scenario import CANARY, plant_memo, roosevelt_seat, ticked
from tests.handwritten import AIDE
from tests.test_realism_tick import CAMPAIGN


class Capturing:
    def __init__(self) -> None:
        self.inner = RecordedTransport(AIDE)
        self.sent: list[Request] = []

    def call(self, request: Request) -> Reply:
        self.sent.append(request)
        return self.inner.call(request)


def ready() -> EventStore:
    store = MemoryStore()
    ticked(store)
    return store


def test_the_aide_reads_a_view_and_nothing_else() -> None:
    for fn in (brief, unread, decisions_due, template_drafts):
        hints = get_type_hints(fn)
        assert hints["view"] is View and State not in hints.values()
    state = load_state(ready(), CAMPAIGN)
    with pytest.raises(TypeError):
        brief(state, state.pipelines, frozenset(), TemplateAide())  # type: ignore[arg-type]


def test_the_model_path_summarises_and_drops_a_draft_that_does_not_fit() -> None:
    store = ready()
    seat = roosevelt_seat(store)
    transport = Capturing()
    out = aide_for(store, CAMPAIGN, seat, ClaudeAide(transport))
    assert "209,835" in out.summary
    assert [d.command() for d in out.drafts] == [
        "revisionist lever send_bill bill=neutrality_revision urgency=urgent",
        "revisionist lever naval_order order=escort_convoys zone=western_atlantic",
    ]
    (req,) = transport.sent
    assert req.call_class == "aide"
    view = project(load_state(store, CAMPAIGN), seat)
    assert set(out.unread) == set(view.documents)
    (cost,) = [e.payload for e in store.read(CAMPAIGN) if isinstance(e.payload, ev.CostLogged)]
    assert cost.purpose == "aide" and cost.seat_id == seat
    assert not load_state(store, CAMPAIGN).orders  # drafts are printed, never filed


def test_drafts_validate_against_the_lever_specs() -> None:
    store = ready()
    view = project(load_state(store, CAMPAIGN), roosevelt_seat(store))
    assert view.desk is not None
    levers = view.desk.levers
    drafts = template_drafts(view)
    assert drafts and all(valid(d, levers) for d in drafts)
    assert not valid(
        Draft(lever="send_bill", params={"bill": "abolish_congress", "urgency": "urgent"}), levers
    )
    assert not valid(Draft(lever="send_bill", params={"bill": "neutrality_revision"}), levers)
    assert not valid(
        Draft(lever="lend_lease_finding", params={"recipient": "uk", "share_pct": 101}), levers
    )
    assert not valid(Draft(lever="declare_war", params={}), levers)
    seat = roosevelt_seat(store)
    for d in drafts:  # what the aide drafts, ``revisionist lever`` would accept
        filed = realism_tick.file_order(
            store, CAMPAIGN, seat, d.lever, {k: str(v) for k, v in d.params.items()}
        )
        assert filed.params == d.params


def test_a_summary_with_an_invented_figure_falls_back_to_the_template() -> None:
    class Inventor:
        def brief(self, view, pages, due):  # type: ignore[no-untyped-def]
            return "Losses stand at 999,999.", [], None

    store = ready()
    view = project(load_state(store, CAMPAIGN), roosevelt_seat(store))
    out = brief(view, load_state(store, CAMPAIGN).pipelines, frozenset(), Inventor())
    assert "999" not in out.summary and out.summary.endswith(".")


def test_read_pages_are_not_unread() -> None:
    store = ready()
    state = load_state(store, CAMPAIGN)
    seat = roosevelt_seat(store)
    first = next(iter(project(state, seat).documents))
    store.append_all(
        [
            ev.Event(
                event_id=EventId("01ARZ3NDEKTSV4RRFFQ69G5FAX"),
                campaign_id=CAMPAIGN,
                seq=state.last_seq + 1,
                tick=state.tick,
                scope=private_to(seat),
                payload=ev.DocumentRead(seat_id=seat, doc_id=DocumentId(first)),
                versions=cmp.versions(),
            )
        ]
    )
    out = aide_for(store, CAMPAIGN, seat, TemplateAide())
    assert first not in out.unread and out.unread


def test_the_aide_lists_decisions_waiting_on_the_human_and_files_nothing() -> None:
    store = ready()
    seat = roosevelt_seat(store)
    order = realism_tick.file_order(
        store, CAMPAIGN, seat, "send_bill", {"bill": "neutrality_revision", "urgency": "urgent"}
    )
    for _ in range(200):
        state = load_state(store, CAMPAIGN)
        if waiting_on_human(state, state.orders[order.order_id]):
            break
        run_realism_tick(store, CAMPAIGN, prose=TemplateProseWriter())
    else:
        pytest.fail("the bill never reached Roosevelt's desk")
    plant_memo(store)
    before = load_state(store, CAMPAIGN)
    waiting = [o for o in before.orders.values() if waiting_on_human(before, o)]
    out = aide_for(store, CAMPAIGN, seat, TemplateAide())
    assert [d.order_id for d in out.decisions] == [o.order_id for o in waiting] == [order.order_id]
    assert "pass" in out.decisions[0].allowed
    after = load_state(store, CAMPAIGN)
    assert after.last_seq == before.last_seq  # the template aide writes nothing at all
    assert state_hash(after) == state_hash(before)
    assert CANARY not in json.dumps(out.model_dump(mode="json"))
