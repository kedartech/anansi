"""Realism snapshots: what a seat's JSON carries, built from its own view and nothing more."""

import json
from datetime import date, timedelta

import pytest

from revisionist.core import events as ev
from revisionist.core.fold import replay
from revisionist.core.ids import (
    CampaignId,
    DocumentId,
    EntityId,
    EventId,
    OrderId,
    PipelineId,
    QuantityId,
    SeatId,
    derived_id,
)
from revisionist.core.project import project
from revisionist.core.realism.card import ChannelKind
from revisionist.core.realism.document import (
    BodyField,
    DocumentKind,
    DocumentTruth,
    DocumentView,
    TruthRef,
)
from revisionist.core.realism.pipeline import Order, OrderStatus, StepAction, StepOutcome
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC, Scope, private_to
from revisionist.core.world_compile import compile_world, resolve_keys
from revisionist.engine import campaign as cmp
from revisionist.engine.web import realism_snapshot as rs
from revisionist.engine.web.realism_api import DocumentListItem
from revisionist.engine.web.snapshot import checked

CAMPAIGN = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FAV")
TRUTH_CANARY = "CANARY-TRUTH-OIL-51c2"


class Toy:
    """The realism toy compiled, two of its people claimed, and helpers to append to it."""

    def __init__(self) -> None:
        world = cmp.load_world(cmp.world_path("realism-toy"))
        self.ids = resolve_keys(world, CAMPAIGN)
        self.events = compile_world(world, CAMPAIGN, cmp.versions())
        self.ada = SeatId(derived_id(CAMPAIGN, "seat", "ada"))
        self.bram = SeatId(derived_id(CAMPAIGN, "seat", "bram"))
        self.add(ev.CharacterClaimed(seat_id=self.ada, entity_id=self.entity("ada")))
        self.add(ev.CharacterClaimed(seat_id=self.bram, entity_id=self.entity("bram")))
        self.add(ev.SessionZeroClosed())

    def entity(self, key: str) -> EntityId:
        return EntityId(self.ids[key])

    def add(self, payload: ev.Payload, scope: Scope = PUBLIC) -> None:
        seq = len(self.events)
        self.events.append(
            ev.Event(
                event_id=EventId(derived_id(CAMPAIGN, "test-event", seq)),
                campaign_id=CAMPAIGN,
                seq=seq,
                tick=0,
                scope=scope,
                payload=payload,
                versions=cmp.versions(),
            )
        )

    @property
    def state(self) -> State:
        return replay(self.events)

    def today(self) -> date:
        today = self.state.in_world_date
        assert today is not None
        return today

    def document(self, n: int, *, arrives_in: int, scope: Scope) -> DocumentId:
        doc_id = DocumentId(derived_id(CAMPAIGN, "doc", n))
        today = self.today()
        view = DocumentView(
            doc_id=doc_id,
            kind=DocumentKind.WHIP_COUNT,
            author=self.entity("bram"),
            issued=today,
            arrives=today + timedelta(days=arrives_in),
            channel=ChannelKind.LETTER,
            title=f"Count {n}",
            body=(BodyField(label="Votes for the levy", value=8, unit="votes"),),
            prose="The Council is closer than the Speaker admits.",
            scope=scope,
        )
        truth = DocumentTruth(
            doc_id=doc_id,
            truth_refs=(
                TruthRef(
                    quantity_id=QuantityId(self.ids["council_votes"]),
                    value=7,
                    canary=TRUTH_CANARY,
                ),
            ),
        )
        self.add(ev.DocumentIssued(document=view, truth=truth), scope)
        return doc_id

    def order(self, n: int, issuer: SeatId) -> OrderId:
        order_id = OrderId(derived_id(CAMPAIGN, "order", n))
        self.add(
            ev.OrderDrafted(
                order=Order(
                    order_id=order_id,
                    issuer=issuer,
                    lever="propose_levy",
                    pipeline=PipelineId(self.ids["levy_bill"]),
                    params={"rate": 3, "purpose": "harbour"},
                    filed=self.today(),
                )
            ),
            private_to(issuer),
        )
        return order_id

    def step(self, order_id: OrderId, step: int, person: str, action: StepAction) -> None:
        self.add(
            ev.OrderStepTaken(
                order_id=order_id,
                outcome=StepOutcome(
                    step=step, person=self.entity(person), action=action, on=self.today()
                ),
            )
        )


@pytest.fixture
def toy() -> Toy:
    return Toy()


def _json(state: State, seat: SeatId, out: object) -> object:
    """The snapshot as the client gets it, checked for every canary the seat may not hold."""
    return checked(state, seat, json.loads(json.dumps(out)))


def test_the_in_tray_lists_arrived_pages_without_bodies_or_truth(toy: Toy) -> None:
    arrived = toy.document(1, arrives_in=0, scope=private_to(toy.ada))
    later = [toy.document(n, arrives_in=2, scope=private_to(toy.ada)) for n in (2, 3)]
    toy.add(ev.DateAdvanced(to=toy.today() + timedelta(days=2)))
    listing = rs.documents(project(toy.state, toy.ada), read_ids={arrived}, flagged_ids=set())
    assert [row["id"] for row in listing["documents"]] == [*sorted(later), arrived]
    assert listing["unread"] == 2
    row = listing["documents"][-1]
    assert set(row) == set(DocumentListItem.__annotations__)
    assert row["author"] == "Bram Tull" and row["unread"] is False
    assert "body" not in row and "truth_refs" not in json.dumps(listing)
    _json(toy.state, toy.ada, listing)


def test_a_seat_fetches_only_pages_it_has_received(toy: Toy) -> None:
    got = toy.document(1, arrives_in=0, scope=private_to(toy.ada))
    not_yet = toy.document(2, arrives_in=3, scope=private_to(toy.ada))
    not_theirs = toy.document(3, arrives_in=0, scope=private_to(toy.bram))
    view = project(toy.state, toy.ada)
    page = rs.document(view, got)
    assert page["body"] == [{"label": "Votes for the levy", "value": 8, "unit": "votes"}]
    assert page["prose"] == "The Council is closer than the Speaker admits."
    assert page["item"]["title"] == "Count 1"
    with pytest.raises(KeyError):
        rs.document(view, not_yet)
    with pytest.raises(KeyError):
        rs.document(view, not_theirs)
    _json(toy.state, toy.ada, page)


def test_a_stalled_order_shows_no_action_and_a_refusal_shows(toy: Toy) -> None:
    stalled = toy.order(1, toy.ada)
    refused = toy.order(2, toy.ada)
    for order_id, action in ((stalled, StepAction.STALL), (refused, StepAction.REFUSE)):
        toy.step(order_id, 0, "cora", StepAction.PASS)
        toy.step(order_id, 1, "bram", action)
    tracker = rs.orders(project(toy.state, toy.ada), toy.state.pipelines)
    by_id = {o["id"]: o for o in tracker["orders"]}
    waiting = by_id[stalled]
    assert waiting["with_person"] == "Bram Tull"
    assert waiting["since"] == toy.today().isoformat()
    assert waiting["decision_needed"] is False
    assert [s["action"] for s in waiting["steps"]] == ["pass", None]
    assert waiting["steps"][1]["kind"] == "decision"
    assert by_id[refused]["steps"][1]["action"] == "refuse"
    assert "stall" not in json.dumps(tracker)
    _json(toy.state, toy.ada, tracker)


def test_decision_needed_is_true_when_the_viewer_holds_the_step(toy: Toy) -> None:
    mine = toy.order(1, toy.bram)
    toy.step(mine, 0, "cora", StepAction.PASS)
    track = rs.orders(project(toy.state, toy.bram), toy.state.pipelines)["orders"][0]
    assert track["id"] == mine
    assert track["with_person"] == "Bram Tull" and track["decision_needed"] is True
    assert track["status"] == OrderStatus.MOVING.value and track["at_step"] == 1
    assert track["params"] == {"rate": 3, "purpose": "harbour"}


def test_a_settled_order_has_no_holder(toy: Toy) -> None:
    done = toy.order(1, toy.ada)
    toy.step(done, 0, "cora", StepAction.PASS)
    toy.add(ev.OrderSettled(order_id=done, status=OrderStatus.LAPSED))
    track = rs.orders(project(toy.state, toy.ada), toy.state.pipelines)["orders"][0]
    assert track["with_person"] is None and track["decision_needed"] is False
    assert track["status"] == "lapsed"
    assert [s["action"] for s in track["steps"]] == ["pass"]


def test_the_desk_names_people_and_leaves_out_the_ai_policy(toy: Toy) -> None:
    view = project(toy.state, toy.ada)
    desk = rs.desk(view)
    assert set(desk) == {
        "office",
        "powers",
        "levers",
        "contacts",
        "principals",
        "constraints",
        "personal",
        "horizon_days",
    }
    assert desk["office"] == "First Citizen of Vell" and desk["horizon_days"] == 14
    assert desk["principals"] == ["Vell"]
    assert [c["person"] for c in desk["contacts"]] == ["Bram Tull", "Cora Senn"]
    lever = desk["levers"][0]
    assert lever["name"] == "propose_levy" and lever["pipeline"] == toy.ids["levy_bill"]
    assert [p["name"] for p in lever["params"]] == ["rate", "purpose"]
    assert lever["params"][1]["choices"] == [
        {"value": "harbour", "label": "For the harbour"},
        {"value": "militia", "label": "For the militia"},
    ]
    text = json.dumps(desk)
    assert not {"voice", "loyalty", "doctrine", "interests"} & set(desk)
    assert "Brisk, plain" not in text and "reformer" not in text
    _json(toy.state, toy.ada, desk)


def test_the_clock_reads_the_in_world_date_and_what_it_waits_on(toy: Toy) -> None:
    clock = rs.clock(
        project(toy.state, toy.ada),
        next_wake_at="2026-10-23T09:00:00+00:00",
        waiting_on="Bram's count, expected 9 Apr",
        days_collapsed=9,
        next_world_date="1900-04-09",
    )
    assert clock == {
        "world_date": "1900-04-02",
        "next_wake_at": "2026-10-23T09:00:00+00:00",
        "next_world_date": "1900-04-09",
        "waiting_on": "Bram's count, expected 9 Apr",
        "days_collapsed": 9,
    }
    _json(toy.state, toy.ada, clock)


def test_words_turns_a_key_into_plain_words() -> None:
    assert rs.words("send_bill") == "Send bill"
    assert rs.words("lend_lease_finding") == "Lend lease finding"
    assert rs.words("") == ""


def test_the_autumn_desks_and_orders_carry_no_snake_case() -> None:
    world = cmp.load_world(cmp.world_path("autumn-1941"))
    ids = resolve_keys(world, CAMPAIGN)
    events = compile_world(world, CAMPAIGN, cmp.versions())
    playable = [c.person for c in world.cards if c.playable]
    assert len(playable) == 8
    seats = {k: SeatId(derived_id(CAMPAIGN, "seat", k)) for k in playable}

    def add(p: ev.Payload) -> None:
        events.append(
            ev.Event(
                event_id=EventId(derived_id(CAMPAIGN, "t", len(events))),
                campaign_id=CAMPAIGN,
                seq=len(events),
                tick=0,
                scope=PUBLIC,
                payload=p,
                versions=cmp.versions(),
            )
        )

    for key, seat in seats.items():
        add(ev.CharacterClaimed(seat_id=seat, entity_id=EntityId(ids[key])))
    add(ev.SessionZeroClosed())
    state = replay(events)
    for seat in seats.values():
        desk = rs.desk(project(state, seat))
        assert desk["levers"]
        for lever in desk["levers"]:
            assert lever["label"] and lever["summary"], lever["name"]
            for text in (lever["label"], lever["summary"]):
                assert "_" not in text
            for param in lever["params"]:
                assert param["label"] and "_" not in param["label"]
                for choice in param["choices"]:
                    assert choice["label"] and "_" not in choice["label"]
                    assert choice["value"] != choice["label"]


def test_an_order_reads_in_the_levers_own_words(toy: Toy) -> None:
    order = Order(
        order_id=OrderId(derived_id(CAMPAIGN, "order", "words")),
        issuer=toy.ada,
        lever="propose_levy",
        pipeline=PipelineId(toy.ids["levy_bill"]),
        params={"rate": 3, "purpose": "harbour"},
        filed=toy.today(),
    )
    toy.add(ev.OrderDrafted(order=order))
    (track,) = rs.orders(project(toy.state, toy.ada), toy.state.pipelines)["orders"]
    assert track["label"] == "Propose a levy"
    assert track["terms"] == ["Rate: 3", "Purpose: For the harbour"]


def test_choice_labels_must_match_the_choices() -> None:
    from pydantic import ValidationError

    from revisionist.core.realism.card import ParamKind, ParamSpec

    ParamSpec(name="a", kind=ParamKind.ENUM, choices=("x", "y"), choice_labels=("Ex", "Why"))
    ParamSpec(name="a", kind=ParamKind.ENUM, choices=("x", "y"))
    with pytest.raises(ValidationError, match="one choice label per choice"):
        ParamSpec(name="a", kind=ParamKind.ENUM, choices=("x", "y"), choice_labels=("Ex",))


def test_empty_display_labels_leave_the_dump_unchanged() -> None:
    """Campaigns logged before labels existed must keep their state hash."""
    from revisionist.core.ids import PipelineId
    from revisionist.core.realism.card import Lever, ParamKind, ParamSpec

    spec = ParamSpec(name="urgency", kind=ParamKind.ENUM, choices=("ordinary", "urgent"))
    lever = Lever(name="send_bill", pipeline=PipelineId("p"), resolver="votes", params=(spec,))
    dumped = lever.model_dump(mode="json")
    assert "label" not in dumped and "summary" not in dumped
    assert set(dumped["params"][0]) == {"name", "kind", "lo", "hi", "choices"}
    labelled = lever.model_copy(update={"label": "Send a bill to Congress"})
    assert labelled.model_dump(mode="json")["label"] == "Send a bill to Congress"
