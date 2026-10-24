"""run_realism_tick: stages, the opening in-tray, resumption and replay."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from revisionist.core import events as ev
from revisionist.core.fold import replay, state_hash
from revisionist.core.ids import (
    CampaignId,
    EntityId,
    EventId,
    OrderId,
    PipelineId,
    SeatId,
    derived_id,
)
from revisionist.core.project import project
from revisionist.core.realism.pipeline import Order
from revisionist.core.resolution import TickStage
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC
from revisionist.core.world_compile import compile_world, resolve_keys
from revisionist.engine import campaign as cmp
from revisionist.engine import realism_tick
from revisionist.engine.realism_tick import RealismTickResult, run_realism_tick
from revisionist.engine.runner import play_tick
from revisionist.engine.store import EventStore, MemoryStore, SqliteStore, load_state
from revisionist.engine.tick import NotInPlayError

CAMPAIGN = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FAV")
STAGES = (TickStage.ADVANCE, TickStage.PIPELINES, TickStage.RESOLVE, TickStage.FEEDS)


@pytest.fixture(params=["memory", "sqlite"])
def stores(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[list[EventStore]]:
    """Two fresh stores of one kind: the interrupted run and the uninterrupted one."""
    made: list[EventStore] = []
    for n in range(2):
        made.append(
            MemoryStore() if request.param == "memory" else SqliteStore(tmp_path / f"{n}.db")
        )
    yield made
    for s in made:
        if isinstance(s, SqliteStore):
            s.close()


def seated(store: EventStore, world: str = "realism-toy", who: str = "ada") -> CampaignId:
    """The world compiled under a fixed campaign id, the human seated as ``who``."""
    decl = cmp.load_world(cmp.world_path(world))
    store.append_all(compile_world(decl, CAMPAIGN, cmp.versions()))
    realism_tick.seat_player(store, CAMPAIGN, EntityId(resolve_keys(decl, CAMPAIGN)[who]))
    return CAMPAIGN


def file_levy(store: EventStore, who: SeatId | None = None) -> None:
    """Ada's levy with a fixed order id, so two runs share a state hash. ``who`` names
    the seat when more than one is seated."""
    state = load_state(store, CAMPAIGN)
    (seat,) = [s for s in state.seats.values() if who is None or s.seat_id == who]
    card = state.cards[seat.embodies]
    lever = next(x for x in card.levers if x.name == "propose_levy")
    order = Order(
        order_id=OrderId(derived_id(CAMPAIGN, "order", 1)),
        issuer=seat.seat_id,
        lever="propose_levy",
        pipeline=PipelineId(lever.pipeline),
        params={"rate": 5, "purpose": "harbour"},
        filed=state.in_world_date,
    )
    store.append_all(
        [
            ev.Event(
                event_id=EventId(derived_id(CAMPAIGN, "levy-event")),
                campaign_id=CAMPAIGN,
                seq=state.last_seq + 1,
                tick=state.tick,
                scope=PUBLIC,
                payload=ev.OrderDrafted(order=order),
                versions=cmp.versions(),
            )
        ]
    )


def test_first_tick_issues_the_opening_in_tray_and_wakes_at_the_next_candidate() -> None:
    store = MemoryStore()
    cid = seated(store)
    start = load_state(store, cid).in_world_date
    result = run_realism_tick(store, cid)
    state = load_state(store, cid)
    assert result.wake is not None
    assert state.in_world_date == result.wake.to > start  # type: ignore[operator]
    assert state.documents
    assert any(d.view.issued == start for d in state.documents.values())
    assert result.wake.reason.what in {"document", "feed", "step", "history", "horizon"}
    assert result.delivered == ()


def test_documents_project_to_a_seat_only_on_arrival() -> None:
    store = MemoryStore()
    cid = seated(store)
    (seat,) = load_state(store, cid).seats
    held_back = 0
    for _ in range(4):
        run_realism_tick(store, cid)
        state = load_state(store, cid)
        view = project(state, seat)
        today = state.in_world_date
        assert today is not None
        mine = {k for k, d in state.documents.items() if d.view.scope.visible_to(seat)}
        arrived = {k for k in mine if state.documents[k].view.arrives <= today}
        assert set(view.documents) == arrived
        held_back += len(mine - arrived)
    assert held_back, "a document should sit in the log before it arrives"


def test_refuses_outside_play() -> None:
    store = MemoryStore()
    decl = cmp.load_world(cmp.world_path("realism-toy"))
    store.append_all(compile_world(decl, CAMPAIGN, cmp.versions()))
    with pytest.raises(NotInPlayError):
        run_realism_tick(store, CAMPAIGN)


@pytest.mark.parametrize("stage", STAGES)
def test_resumed_tick_reaches_the_same_hash(stage: TickStage, stores: list[EventStore]) -> None:
    broken, whole = stores
    for s in stores:
        seated(s)
        file_levy(s)
    for _ in range(2):
        run_realism_tick(whole, CAMPAIGN)
    run_realism_tick(broken, CAMPAIGN)
    cut = run_realism_tick(broken, CAMPAIGN, stop_after=stage)
    assert cut.stopped_after is stage
    if stage is not TickStage.FEEDS:
        assert cut.state_hash == ""
    resumed = run_realism_tick(broken, CAMPAIGN)
    assert resumed.resumed_from is stage
    assert state_hash(load_state(broken, CAMPAIGN)) == state_hash(load_state(whole, CAMPAIGN))


def test_the_log_replays_to_the_hash_each_tick_closed_with() -> None:
    store = MemoryStore()
    cid = seated(store)
    file_levy(store)
    for _ in range(4):
        result = run_realism_tick(store, cid)
    log = list(store.read(cid))
    closed = [e.payload for e in log if isinstance(e.payload, ev.TickClosed)]
    assert len(closed) == 4
    replay(log)  # TickClosed folds only if its hash matches
    before_close: State = replay(log[:-1])
    assert state_hash(before_close) == closed[-1].state_hash == result.state_hash
    assert [e.seq for e in log] == list(range(len(log)))


def test_each_stage_lands_with_its_marker_in_order() -> None:
    store = MemoryStore()
    cid = seated(store)
    run_realism_tick(store, cid)
    kinds = [
        e.payload.stage for e in store.read(cid) if isinstance(e.payload, ev.TickStageCompleted)
    ]
    assert kinds == list(STAGES)


def test_legacy_play_tick_is_not_diverted(tmp_path: Path) -> None:
    store = MemoryStore()
    decl = cmp.load_world(cmp.world_path("salt-strait"))
    cid = cmp.new_campaign(store, decl)
    run = play_tick(
        store,
        cid,
        offline=True,
        transport_factory=cmp.live_transport,
        briefings=tmp_path,
        warn=lambda _m: None,
    )
    assert not isinstance(run.result, RealismTickResult)
    assert run.result.tick == 0 and run.leaks == []
    kinds = {
        e.payload.stage for e in store.read(cid) if isinstance(e.payload, ev.TickStageCompleted)
    }
    assert TickStage.ADVANCE not in kinds and TickStage.DELIVER in kinds


def test_a_feed_due_mid_stretch_issues_on_its_own_day() -> None:
    """The walk issues each feed the day it falls due, so documents wait in the in-tray."""
    from datetime import date

    store = MemoryStore()
    cid = seated(store, "autumn-1941", "roosevelt")
    run_realism_tick(store, cid)
    state = load_state(store, cid)
    assert state.in_world_date == date(1941, 9, 4)
    assert date(1941, 9, 3) in {d.view.issued for d in state.documents.values()}


def test_a_reply_letter_rides_a_private_envelope_and_is_written_next_wake() -> None:
    from revisionist.core.realism.prose import invented_numbers
    from revisionist.engine import session_zero
    from revisionist.engine.prose import TemplateProseWriter

    store = MemoryStore()
    decl = cmp.load_world(cmp.world_path("realism-toy"))
    store.append_all(compile_world(decl, CAMPAIGN, cmp.versions()))
    ids = resolve_keys(decl, CAMPAIGN)
    cora = session_zero.claim(
        store, CAMPAIGN, SeatId(derived_id(CAMPAIGN, "seat", ids["cora"])), "Cora Senn"
    )
    assert cora
    realism_tick.seat_player(store, CAMPAIGN, EntityId(ids["ada"]))
    ada = SeatId(derived_id(CAMPAIGN, "seat", ids["ada"]))
    file_levy(store, ada)
    for _ in range(4):
        run_realism_tick(store, CAMPAIGN, prose=TemplateProseWriter())

    state = load_state(store, CAMPAIGN)
    other = SeatId(derived_id(CAMPAIGN, "seat", ids["cora"]))
    letters = [
        e
        for e in store.read(CAMPAIGN)
        if isinstance(e.payload, ev.DocumentIssued)
        and e.payload.document.title.startswith("On your")
    ]
    assert letters, "an order moving through other people should write back"
    for e in letters:
        assert isinstance(e.payload, ev.DocumentIssued)
        assert e.scope.visibility.value == "private" and e.scope.known_by == {ada}
        assert e.scope == e.payload.document.scope
        page = state.documents[e.payload.document.doc_id].view
        assert page.prose and invented_numbers(page.prose, page) == ()
    mine = set(project(state, ada).documents)
    theirs = set(project(state, other).documents)
    for e in letters:
        assert isinstance(e.payload, ev.DocumentIssued)
        assert e.payload.document.doc_id in mine
        assert e.payload.document.doc_id not in theirs
