"""Realism contract (Phase 1): the records store, project, scan and seal as specified,
and a legacy campaign replays and hashes exactly as it did before realism existed."""

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from revisionist.core import events as ev
from revisionist.core.canary import scan
from revisionist.core.fold import FoldError, replay, state_hash
from revisionist.core.ids import (
    CampaignId,
    DocumentId,
    EntityId,
    EventId,
    IntentId,
    OrderId,
    PipelineId,
    QuantityId,
    SeatId,
    derived_id,
)
from revisionist.core.intent import Intent, Verb
from revisionist.core.ontology import Controller, Phase, SeatKind
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
from revisionist.core.resolution import LEGACY_STAGES, TickStage
from revisionist.core.sealing import needs_admin_read, seal_holder
from revisionist.core.state import State, View
from revisionist.core.visibility import PUBLIC, Scope, private_to
from revisionist.core.world import QuantityDecl, SeatDecl, WorldFile
from revisionist.core.world_compile import WorldError, compile_world, resolve_keys
from revisionist.engine import campaign as cmp
from revisionist.engine.providers import (
    KeywordSafety,
    RecordingDeliverer,
    ScriptedResolver,
    TemplateNarrator,
)
from revisionist.engine.seal import KeyringSealer, MemoryKeyring
from revisionist.engine.store import MemoryStore, load_state
from revisionist.engine.tick import Providers, file_intent, run_tick
from revisionist.schema_export import build

LEGACY_LOG = Path(__file__).parent / "recordings" / "legacy-salt-strait.jsonl"
"""Salt Strait, three offline ticks, recorded by the code at 35274c2, before realism."""

REALISM_STATE = {"ruleset", "cards", "feeds", "pipelines", "documents", "orders", "quantities"}
CAMPAIGN = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FAV")
TRUTH_CANARY = "CANARY-TRUTH-OIL-51c2"


# --- legacy stays byte for byte -------------------------------------------------


def test_the_recorded_legacy_log_replays_and_reserialises_byte_for_byte() -> None:
    lines = LEGACY_LOG.read_text().splitlines()
    events = [ev.Event.model_validate_json(line) for line in lines]
    assert [e.model_dump_json() for e in events] == lines
    state = replay(events)  # every tick_closed re-checks its state hash
    assert state.tick == 3 and state.ruleset == "legacy"
    assert not REALISM_STATE & state.model_dump(mode="json").keys()


def _without_realism(state: State) -> str:
    dump = {k: v for k, v in state.model_dump(mode="json").items() if k not in REALISM_STATE}
    canon = json.dumps(dump, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.blake2b(canon.encode(), digest_size=16).hexdigest()


def test_fresh_legacy_ticks_hash_as_if_realism_did_not_exist() -> None:
    store = MemoryStore()
    cid = cmp.new_campaign(store, cmp.load_world(cmp.world_path("salt-strait")))
    for n in range(3):
        state = load_state(store, cid)
        intent = Intent(
            intent_id=IntentId(derived_id(cid, "intent", n)),
            actor=cmp.human_seat(state),
            verb=Verb.DECLARE,
            scope=PUBLIC,
            filed_tick=state.tick,
        )
        file_intent(store, cid, intent, safety=KeywordSafety(), versions=cmp.versions())
        providers = Providers(
            ScriptedResolver(), TemplateNarrator(), RecordingDeliverer(), KeywordSafety()
        )
        run_tick(store, cid, providers, versions=cmp.versions())
    events = list(store.read(cid))
    assert "ruleset" not in events[0].model_dump_json()
    closed = [e for e in events if isinstance(e.payload, ev.TickClosed)]
    assert len(closed) == 3
    for c in closed:
        before = replay(e for e in events if e.seq < c.seq)
        assert isinstance(c.payload, ev.TickClosed)
        assert c.payload.state_hash == state_hash(before) == _without_realism(before)


def test_legacy_tick_stages_keep_their_values_and_order() -> None:
    assert [s.value for s in LEGACY_STAGES] == [
        "freeze",
        "propose",
        "validate",
        "apply",
        "narrate",
        "deliver",
    ]
    assert list(TickStage)[: len(LEGACY_STAGES)] == list(LEGACY_STAGES)
    assert {"advance", "pipelines", "resolve", "feeds", "prose"} <= {s.value for s in TickStage}


# --- a realism campaign ---------------------------------------------------------


class Toy:
    """The toy world compiled, two of its people claimed, and helpers to append to it."""

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

    def add(self, payload: ev.Payload, scope: Scope = PUBLIC) -> ev.Event:
        seq = len(self.events)
        e = ev.Event(
            event_id=EventId(derived_id(CAMPAIGN, "test-event", seq)),
            campaign_id=CAMPAIGN,
            seq=seq,
            tick=0,
            scope=scope,
            payload=payload,
            versions=cmp.versions(),
        )
        self.events.append(e)
        return e

    @property
    def state(self) -> State:
        return replay(self.events)

    def document(
        self, n: int, *, arrives_in: int, scope: Scope, label: str = "Votes for the levy"
    ) -> ev.DocumentIssued:
        today = self.state.in_world_date
        assert today is not None
        doc_id = DocumentId(derived_id(CAMPAIGN, "doc", n))
        view = DocumentView(
            doc_id=doc_id,
            kind=DocumentKind.WHIP_COUNT,
            author=self.entity("bram"),
            issued=today,
            arrives=today + timedelta(days=arrives_in),
            channel=ChannelKind.LETTER,
            body=(BodyField(label=label, value=8, unit="votes"),),
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
        return ev.DocumentIssued(document=view, truth=truth)

    def order(self, n: int, issuer: SeatId) -> Order:
        today = self.state.in_world_date
        assert today is not None
        return Order(
            order_id=OrderId(derived_id(CAMPAIGN, "order", n)),
            issuer=issuer,
            lever="propose_levy",
            pipeline=PipelineId(self.ids["levy_bill"]),
            params={"rate": 3, "purpose": "harbour"},
            filed=today,
        )


@pytest.fixture
def toy() -> Toy:
    return Toy()


def test_both_fixture_worlds_compile_to_realism_campaigns() -> None:
    for name, cards in (("realism-toy", 3), ("realism-slice-1941", 5)):
        world = cmp.load_world(cmp.world_path(name))
        state = replay(compile_world(world, CAMPAIGN, cmp.versions()))
        assert state.ruleset == "realism" and len(state.cards) == cards
        assert state.phase is Phase.SESSION_ZERO and not state.seats  # nobody is pre-seated
        assert state.in_world_date == world.start_date
        assert {c.person for c in state.cards.values() if c.playable} == set(state.roster)


def test_the_slice_leaves_every_figure_a_gap() -> None:
    world = cmp.load_world(cmp.world_path("realism-slice-1941"))
    assert world.start_date == date(1941, 9, 1)
    assert all(q.gap and q.value is None for q in world.quantities)
    assert all(f.gap for f in world.feeds) and all(p.gap for p in world.pipelines)


def _toy_with(**changes: object) -> WorldFile:
    world = cmp.load_world(cmp.world_path("realism-toy"))
    return world.model_copy(update=changes)


def test_a_realism_world_names_no_human_seat_and_cites_its_sources() -> None:
    human = SeatDecl(
        key="ada_seat",
        kind=SeatKind.PLAYER,
        name="Ada",
        embodies="ada",
        controller=Controller.HUMAN,
    )
    toy = _toy_with(seats=(human,))
    with pytest.raises(WorldError, match="names no human seat"):
        compile_world(toy, CAMPAIGN, cmp.versions())
    q = _toy_with().quantities[0].model_copy(update={"source": "nowhere"})
    with pytest.raises(WorldError, match="unknown source nowhere"):
        compile_world(_toy_with(quantities=(q,)), CAMPAIGN, cmp.versions())
    with pytest.raises(ValidationError, match="source"):
        QuantityDecl(key="x", name="x", value=1, unit="t")
    with pytest.raises(WorldError, match="only a realism world"):
        compile_world(_toy_with(ruleset="legacy"), CAMPAIGN, cmp.versions())


def test_a_realism_record_in_a_legacy_campaign_is_refused(toy: Toy) -> None:
    legacy = compile_world(cmp.load_world(cmp.world_path("salt-strait")), CAMPAIGN, cmp.versions())
    q = toy.state.quantities[QuantityId(toy.ids["council_votes"])]
    stray = legacy[-1].model_copy(
        update={
            "seq": len(legacy),
            "event_id": EventId(derived_id(CAMPAIGN, "stray")),
            "payload": ev.QuantitySet(quantity=q.model_copy(update={"owner": None})),
        }
    )
    with pytest.raises(FoldError, match="realism campaign"):
        replay([*legacy, stray])


def test_a_document_is_in_view_only_in_scope_and_once_arrived(toy: Toy) -> None:
    toy.add(toy.document(1, arrives_in=3, scope=private_to(toy.ada)), private_to(toy.ada))
    doc = DocumentId(derived_id(CAMPAIGN, "doc", 1))
    assert doc in toy.state.documents
    assert doc not in project(toy.state, toy.ada).documents  # in scope, not yet arrived
    today = toy.state.in_world_date
    assert today is not None
    toy.add(ev.DateAdvanced(to=today + timedelta(days=3)))
    assert project(toy.state, toy.ada).documents[doc].arrives == toy.state.in_world_date
    assert doc not in project(toy.state, toy.bram).documents  # arrived, never in scope


def test_a_seat_sees_only_its_own_orders(toy: Toy) -> None:
    toy.add(ev.OrderDrafted(order=toy.order(1, toy.ada)), private_to(toy.ada))
    toy.add(ev.OrderDrafted(order=toy.order(2, toy.bram)), private_to(toy.bram))
    ada, bram = project(toy.state, toy.ada), project(toy.state, toy.bram)
    assert [o.issuer for o in ada.orders.values()] == [toy.ada]
    assert [o.issuer for o in bram.orders.values()] == [toy.bram]


def test_an_order_moves_only_as_its_steps_allow(toy: Toy) -> None:
    o = toy.order(1, toy.ada)
    toy.add(ev.OrderDrafted(order=o), private_to(toy.ada))
    today = o.filed
    clerk, speaker = toy.entity("cora"), toy.entity("bram")
    toy.add(
        ev.OrderStepTaken(
            order_id=o.order_id,
            outcome=StepOutcome(step=0, person=clerk, action=StepAction.PASS, on=today),
        )
    )
    toy.add(
        ev.OrderStepTaken(
            order_id=o.order_id,
            outcome=StepOutcome(step=1, person=speaker, action=StepAction.STALL, on=today),
        )
    )
    toy.add(
        ev.OrderStepTaken(
            order_id=o.order_id,
            outcome=StepOutcome(
                step=1, person=speaker, action=StepAction.AMEND, on=today, amended={"rate": 2}
            ),
        )
    )
    moved = toy.state.orders[o.order_id]
    assert moved.step == 2 and moved.params["rate"] == 2 and len(moved.outcomes) == 3
    toy.add(ev.OrderSettled(order_id=o.order_id, status=OrderStatus.ENACTED))
    assert toy.state.orders[o.order_id].status is OrderStatus.ENACTED
    with pytest.raises(FoldError, match="settled"):
        toy.add(
            ev.OrderStepTaken(
                order_id=o.order_id,
                outcome=StepOutcome(step=2, person=speaker, action=StepAction.PASS, on=today),
            )
        )
        _ = toy.state


def test_a_clerical_step_cannot_refuse(toy: Toy) -> None:
    o = toy.order(1, toy.ada)
    toy.add(ev.OrderDrafted(order=o), private_to(toy.ada))
    refuse = StepOutcome(step=0, person=toy.entity("cora"), action=StepAction.REFUSE, on=o.filed)
    toy.add(ev.OrderStepTaken(order_id=o.order_id, outcome=refuse))
    with pytest.raises(FoldError, match="does not allow"):
        _ = toy.state


def test_document_truth_and_card_policy_never_reach_a_view() -> None:
    view = build()["view"]
    assert '"truth_refs"' not in json.dumps(view)
    assert '"truth_refs"' in json.dumps(build()["state"])
    defs = view["$defs"]
    assert {"DocumentView", "CardFace", "Desk"} <= defs.keys()
    assert not {"Document", "DocumentTruth", "TruthRef", "SeatCard"} & defs.keys()
    for name in ("CardFace", "Desk"):
        policy = {"voice", "loyalty", "doctrine", "interests"} & defs[name]["properties"].keys()
        assert not policy, (name, policy)


def test_a_view_carries_faces_and_the_viewers_own_desk(toy: Toy) -> None:
    view: View = project(toy.state, toy.ada)
    assert set(view.cards) == set(toy.state.cards) and view.ruleset == "realism"
    assert view.desk is not None and view.desk.person == toy.entity("ada")
    assert [lv.name for lv in view.desk.levers] == ["propose_levy"]
    assert "voice" not in view.desk.model_dump()
    assert "levers" not in view.cards[view.desk.person].model_dump()


def test_a_truth_canary_in_a_document_body_is_caught(toy: Toy) -> None:
    clean = toy.add(toy.document(1, arrives_in=1, scope=private_to(toy.ada)))
    leaked = toy.add(toy.document(2, arrives_in=1, scope=PUBLIC, label=f"Votes ({TRUTH_CANARY})"))
    counsel = toy.add(
        ev.CounselHeld(
            seat_id=toy.bram,
            person=toy.entity("cora"),
            channel=ChannelKind.IN_PERSON,
            text=f"The true count is {TRUTH_CANARY}.",
        ),
        private_to(toy.bram),
    )
    leaks = scan(toy.state, [clean, leaked, counsel])
    assert {(lk.event_id, lk.seat_id) for lk in leaks} == {
        (leaked.event_id, toy.ada),
        (leaked.event_id, toy.bram),
        (counsel.event_id, toy.bram),
    }
    assert all(lk.canary == TRUTH_CANARY for lk in leaks)


def test_realism_records_seal_and_unseal_and_counsel_is_the_seats_own(toy: Toy) -> None:
    doc = toy.add(toy.document(1, arrives_in=1, scope=private_to(toy.ada)), private_to(toy.ada))
    counsel = toy.add(
        ev.CounselHeld(
            seat_id=toy.ada,
            person=toy.entity("bram"),
            channel=ChannelKind.LETTER,
            text="Hold the levy at three.",
        ),
        private_to(toy.ada),
    )
    assert seal_holder(doc) is None and seal_holder(counsel) == toy.ada
    engine_only = [
        e for e in toy.events if isinstance(e.payload, ev.CardsDeclared | ev.QuantitySet)
    ]
    assert engine_only and all(needs_admin_read(e) for e in [doc, *engine_only])
    keyring = MemoryKeyring()
    keyring.create_campaign_key(CAMPAIGN)
    store = MemoryStore(KeyringSealer(keyring))
    store.append_all(toy.events)
    at_rest = [e.model_dump_json() for e in store._events[CAMPAIGN]]
    assert not any("Hold the levy" in r or "Votes for the levy" in r for r in at_rest)
    assert all('"sealed"' in r for r in at_rest)
    assert list(store.read(CAMPAIGN)) == toy.events
    assert state_hash(load_state(store, CAMPAIGN)) == state_hash(toy.state)
    keyring.destroy(keyring.seats[(CAMPAIGN, toy.ada)])
    erased = list(store.read(CAMPAIGN))
    assert isinstance(erased[-1].payload, ev.Sealed)  # the counsel is gone
    assert state_hash(replay(erased)) == state_hash(toy.state)  # the game is not
