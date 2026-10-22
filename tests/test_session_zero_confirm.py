"""Session Zero v3 two-step establish (spec §5.2): the pitch card, then one confirm."""

from collections.abc import Callable
from datetime import date

import pytest

from revisionist.core import events as ev
from revisionist.core.events import CallCost
from revisionist.core.ontology import Phase
from revisionist.core.settings import Settings
from revisionist.core.world import WorldFile
from revisionist.engine import draft as world_draft
from revisionist.engine import session_zero as sz
from revisionist.engine.llm import Reply, Request, Transport
from revisionist.engine.pitch import PitchError, SettingCard, StartPoint
from revisionist.engine.store import MemoryStore, load_state
from tests.test_draft import CARD, FEUD, START, _good
from tests.test_pitch import Pitch, card, start


class NoCall:
    """A transport that fails the test if anything calls it."""

    def call(self, request: Request) -> Reply:
        raise AssertionError(f"unexpected call: {request.call_class}")


def _card_costs() -> list[CallCost]:
    _, costs = sz.propose("the Pazzi conspiracy", Pitch(card(start("Florence", "1478-04-19"))))
    return costs


def test_propose_returns_the_card_and_its_pitch_call() -> None:
    gm = Pitch(card(start("Before the Meuse", "1940-05-10"), start("Dunkirk", "1940-05-26")))
    result, costs = sz.propose("WWII", gm)
    assert [p.label for p in result.start_points] == ["Before the Meuse", "Dunkirk"]
    assert len(gm.requests) == 1
    assert [c.call_class for c in costs] == ["pitch"]


def test_propose_passes_its_pitch_refusal_through() -> None:
    gm = Pitch(card(refusal="No real period in that pitch."))
    with pytest.raises(PitchError) as refused:
        sz.propose("a dragon's reign", gm)
    assert refused.value.reason == "No real period in that pitch."


@pytest.mark.parametrize(
    ("choice", "strikes", "end_date", "real_days", "reason"),
    [
        (0, (), None, None, "Pick a start point from 1 to 1."),
        (2, (), None, None, "Pick a start point from 1 to 1."),
        (1, (3,), None, None, "Strike items from 1 to 2 of that start point's schedule."),
        (1, (0,), None, None, "Strike items from 1 to 2 of that start point's schedule."),
        (1, (1,), date(1478, 4, 19), None, "The end date must come after the start, 1478-04-19."),
        (1, (), None, 0, "The real-life length is 1 to 365 days."),
        (1, (), None, 366, "The real-life length is 1 to 365 days."),
    ],
)
def test_a_refused_confirm_makes_no_call_and_writes_nothing(
    choice: int,
    strikes: tuple[int, ...],
    end_date: date | None,
    real_days: int | None,
    reason: str,
) -> None:
    store = MemoryStore()
    with pytest.raises(sz.StepRefusedError) as refused:
        sz.confirm(
            store,
            CARD,
            choice,
            Settings(),
            NoCall(),
            strikes=strikes,
            end_date=end_date,
            real_days=real_days,
        )
    assert str(refused.value) == reason
    assert store.campaign_ids() == []


def test_an_empty_schedule_has_no_items_to_strike() -> None:
    bare = SettingCard(
        reading=CARD.reading, start_points=(START.model_copy(update={"schedule": ()}),)
    )
    with pytest.raises(sz.StepRefusedError) as refused:
        sz.confirm(MemoryStore(), bare, 1, Settings(), NoCall(), strikes=(1,))
    assert str(refused.value) == "Strike items from 1 to 0 of that start point's schedule."


def test_strikes_leave_the_schedule_before_the_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[StartPoint, Settings]] = []
    drafted = world_draft.draft

    def record(
        card: SettingCard,
        start: StartPoint,
        settings: Settings,
        transport: Transport,
        effort: int | None = None,
        *,
        detail_effort: int | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> tuple[WorldFile, list[CallCost]]:
        seen.append((start, settings))
        return drafted(card, start, settings, transport, effort, progress=progress)

    monkeypatch.setattr(world_draft, "draft", record)
    sz.confirm(MemoryStore(), CARD, 1, Settings(), _good(), strikes=(1,))
    [(drafted_start, _)] = seen
    assert drafted_start.schedule == (START.schedule[1],)
    assert CARD.start_points[0].schedule == START.schedule  # the card itself is untouched


def test_end_date_and_real_days_land_in_the_settings() -> None:
    store = MemoryStore()
    c = sz.confirm(
        store,
        CARD,
        1,
        Settings(),
        _good(),
        end_date=date(1478, 12, 31),
        real_days=90,
    )
    [settings] = [
        e.payload.settings for e in store.read(c) if isinstance(e.payload, ev.SettingsSet)
    ]
    assert settings.end_date == date(1478, 12, 31)
    assert settings.real_days == 90


def test_card_costs_and_draft_costs_are_both_logged() -> None:
    store = MemoryStore()
    c = sz.confirm(store, CARD, 1, Settings(), _good(), card_costs=_card_costs())
    logged = [e.payload for e in store.read(c) if isinstance(e.payload, ev.CostLogged)]
    assert [p.purpose for p in logged] == ["session_zero"]
    assert [x.call_class for x in logged[0].costs] == [
        "pitch",
        "draft_outline",
        "draft_detail",
        "draft_schedule",
    ]


def test_a_good_confirm_opens_session_zero_with_contests_and_the_date() -> None:
    store = MemoryStore()
    c = sz.confirm(store, CARD, 1, Settings(), _good())
    state = load_state(store, c)
    assert state.phase is Phase.SESSION_ZERO and state.tick == 0
    assert [k.name for k in state.contests.values()] == [FEUD]
    assert state.in_world_date == date(1478, 4, 19)
    assert len(state.roster) == 9
    assert state.goals == {}
