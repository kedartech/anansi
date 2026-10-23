"""The wiring gate: the real commands on a SQLite file, no model anywhere."""

import itertools
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from revisionist import cli
from revisionist.core import events as ev
from revisionist.core.ids import DocumentId, EventId, OrderId, QuantityId, derived_id
from revisionist.core.project import project
from revisionist.core.realism.card import ChannelKind
from revisionist.core.realism.document import (
    BodyField,
    DocumentKind,
    DocumentTruth,
    DocumentView,
    TruthRef,
)
from revisionist.core.realism.pipeline import OrderStatus
from revisionist.core.realism.pipelines import waiting_on_human
from revisionist.core.realism.resolvers import qid, value
from revisionist.core.report import leak_report
from revisionist.core.state import State
from revisionist.core.visibility import private_to
from revisionist.engine import campaign as cmp
from revisionist.engine import realism_tick
from revisionist.engine.store import EventStore, load_state
from revisionist.engine.web import realism_snapshot as rs
from revisionist.engine.web.snapshot import checked

runner = CliRunner()
CANARY = "CANARY-WIRING-TRUTH-77d1"


@pytest.fixture(autouse=True)
def db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REVISIONIST_DB", str(tmp_path / "game.db"))


@pytest.fixture
def fixed_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    """The record's friction is seeded by the campaign and order ids, so a test that
    follows one history needs fixed ones."""
    monkeypatch.setattr(cmp, "new_id", lambda: "01ARZ3NDEKTSV4RRFFQ69G5FAV")
    counter = itertools.count()
    monkeypatch.setattr(
        realism_tick, "new_id", lambda: derived_id("01ARZ3NDEKTSV4RRFFQ69G5FAV", next(counter))
    )


def run(*args: str) -> str:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def current() -> tuple[EventStore, State]:
    store = cmp.open_store()
    return store, load_state(store, cmp.current_campaign(store))


def order_id(out: str) -> OrderId:
    return OrderId(out.split()[1].rstrip(":"))


@pytest.mark.usefixtures("fixed_campaign")
def test_toy_levy_settles_through_the_pipeline_and_documents_arrive() -> None:
    run("new", "realism-toy", "--seat", "ada")
    run("lever", "propose_levy", "rate=5", "purpose=harbour")
    for _ in range(3):
        run("tick", "--offline")
        store, state = current()
        (seat,) = state.seats
        today = state.in_world_date
        assert today is not None
        mine = {k for k, d in state.documents.items() if d.view.scope.visible_to(seat)}
        shown = set(project(state, seat).documents)
        assert shown == {k for k in mine if state.documents[k].view.arrives <= today}
    assert state.documents
    (order,) = state.orders.values()
    bram = next(e for e, x in state.entities.items() if x.name == "Bram Tull")
    assert any(o.person == bram for o in order.outcomes)
    assert order.status is OrderStatus.ENACTED or order.step > 1
    assert leak_report(state, store.read(state.campaign_id)) == []
    assert "0 leaks" in run("leaks")


def test_a_new_realism_world_needs_a_seat_and_a_legacy_one_refuses_it() -> None:
    bare = runner.invoke(cli.app, ["new", "realism-toy"])
    assert bare.exit_code != 0 and "--seat" in bare.output
    legacy = runner.invoke(cli.app, ["new", "salt-strait", "--seat", "ada"])
    assert legacy.exit_code != 0 and "legacy" in legacy.output


def test_lever_refuses_a_parameter_outside_its_spec() -> None:
    run("new", "realism-toy", "--seat", "ada")
    bad = runner.invoke(cli.app, ["lever", "propose_levy", "rate=999", "purpose=harbour"])
    assert bad.exit_code == 2 and "outside" in bad.output
    missing = runner.invoke(cli.app, ["lever", "propose_levy", "rate=5"])
    assert missing.exit_code == 2


def test_a_bill_waits_on_roosevelt_then_passes_and_history_follows(fixed_campaign: None) -> None:
    run("new", "autumn-1941", "--seat", "roosevelt")
    oid = order_id(run("lever", "send_bill", "bill=neutrality_revision", "urgency=urgent"))
    for _ in range(200):
        _, state = current()
        if waiting_on_human(state, state.orders[oid]):
            break
        run("tick", "--offline")
    else:
        pytest.fail("the bill never reached Roosevelt's desk")
    assert value(state, "neutrality_revision_house_yeas") is None
    run("step", oid, "pass")
    out = run("tick", "--offline")
    assert "woke on" in out
    _, state = current()
    assert value(state, "neutrality_revision_house_yeas") is not None
    assert value(state, "neutrality_revision_passed") is not None
    assert state.orders[oid].status is OrderStatus.ENACTED
    for _ in range(60):  # history's clock reaches 17 Oct some ticks later
        house = next(s for s in state.scheduled.values() if s.name.startswith("House votes"))
        if house.fired or house.skipped:
            break
        run("tick", "--offline")
        _, state = current()
    assert house.fired and not house.skipped


def test_a_step_out_of_turn_is_refused() -> None:
    run("new", "autumn-1941", "--seat", "roosevelt")
    oid = order_id(run("lever", "send_bill", "bill=neutrality_revision", "urgency=urgent"))
    result = runner.invoke(cli.app, ["step", oid, "pass"])
    assert result.exit_code == 2 and "not waiting on a human" in result.output


def test_a_truth_canary_reaches_no_view_snapshot_or_report() -> None:
    run("new", "realism-toy", "--seat", "ada")
    run("tick", "--offline")
    store, state = current()
    (seat,) = state.seats
    today = state.in_world_date
    assert today is not None
    doc_id = DocumentId(derived_id(state.campaign_id, "canary-doc"))
    author = state.seats[seat].embodies
    quantity = next(iter(state.quantities))
    view = DocumentView(
        doc_id=doc_id,
        kind=DocumentKind.WHIP_COUNT,
        author=author,
        issued=today,
        arrives=today,
        channel=ChannelKind.LETTER,
        title="A planted page",
        body=(BodyField(label="Votes", value=8, unit="votes"),),
        scope=private_to(seat),
    )
    truth = DocumentTruth(
        doc_id=doc_id,
        truth_refs=(TruthRef(quantity_id=QuantityId(quantity), value=7, canary=CANARY),),
    )
    planted = ev.DocumentIssued(document=view, truth=truth)
    store.append_all(
        [
            ev.Event(
                event_id=EventId(derived_id(state.campaign_id, "canary-event")),
                campaign_id=state.campaign_id,
                seq=state.last_seq + 1,
                tick=state.tick,
                scope=private_to(seat),
                payload=planted,
                versions=cmp.versions(),
            )
        ]
    )
    store, state = current()
    seen = project(state, seat)
    assert doc_id in seen.documents
    assert CANARY not in seen.model_dump_json()
    listing = checked(state, seat, json.loads(json.dumps(rs.documents(seen, set()))))
    page = checked(state, seat, json.loads(json.dumps(rs.document(seen, doc_id))))
    tracker = checked(state, seat, json.loads(json.dumps(rs.orders(seen, state.pipelines))))
    assert CANARY not in json.dumps([listing, page, tracker])
    assert leak_report(state, store.read(state.campaign_id)) == []


VOTES = ("neutrality_revision_house_yeas", "neutrality_revision_passed")


def test_the_final_step_counts_the_votes_at_once(fixed_campaign: None) -> None:
    run("new", "autumn-1941", "--seat", "roosevelt")
    oid = order_id(run("lever", "send_bill", "bill=neutrality_revision", "urgency=urgent"))
    for _ in range(200):
        _, state = current()
        if waiting_on_human(state, state.orders[oid]):
            break
        run("tick", "--offline")
    else:
        pytest.fail("the bill never reached Roosevelt's desk")
    run("step", oid, "pass")
    store, state = current()
    assert state.orders[oid].status is OrderStatus.ENACTED
    counted = {key: value(state, key) for key in VOTES}
    assert None not in counted.values(), counted
    seen = state.last_seq
    run("tick", "--offline")
    store, state = current()
    assert {key: value(state, key) for key in VOTES} == counted
    votes = {qid(state, key) for key in VOTES}
    recounted = [
        e
        for e in store.read(state.campaign_id)
        if e.seq > seen
        and isinstance(e.payload, ev.QuantitySet)
        and e.payload.quantity.quantity_id in votes
    ]
    assert recounted == []  # the tick does not resolve the order a second time
