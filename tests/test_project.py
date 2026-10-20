"""Layer 2: projection soundness. The type-level leak test."""

import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from anansi.core.fold import replay
from anansi.core.ids import SeatId
from anansi.core.project import HasScope, project
from anansi.core.state import State
from anansi.core.visibility import Visibility
from tests import fixtures as fx
from tests import strategies as s


def test_south_cannot_see_norths_secrets() -> None:
    state = replay(fx.creation_events())
    view = project(state, fx.SOUTH)
    assert fx.A_NORTH_SECRET_ARMY not in view.assets
    assert fx.F_NORTH_PLOT not in view.facts
    assert fx.C_NORTH_PLOT not in view.clocks
    assert fx.A_NORTH_ARMY in view.assets
    assert fx.F_PUBLIC_TREATY in view.facts
    assert fx.M_NORTH_STABILITY in view.meters  # restricted to north+south
    assert fx.CANARY not in view.model_dump_json()


def test_north_sees_its_own_secrets_and_guild_does_not_see_the_meter() -> None:
    state = replay(fx.creation_events())
    north = project(state, fx.NORTH)
    assert fx.A_NORTH_SECRET_ARMY in north.assets
    assert fx.F_NORTH_PLOT in north.facts
    guild = project(state, fx.GUILD)
    assert fx.M_NORTH_STABILITY not in guild.meters
    assert fx.A_GUILD_NETWORK in guild.assets
    assert fx.A_GUILD_NETWORK not in project(state, fx.HERALD).assets


def test_other_seats_private_aims_are_redacted() -> None:
    state = replay(fx.creation_events())
    south = project(state, fx.SOUTH)
    assert south.seats[fx.NORTH].objectives == frozenset()
    assert south.seats[fx.NORTH].standing_orders == ()
    north = project(state, fx.NORTH)
    assert north.seats[fx.NORTH].objectives == {fx.C_NORTH_PLOT}


def test_unknown_seat_is_refused() -> None:
    with pytest.raises(KeyError):
        project(replay(fx.creation_events()), SeatId("01ARZ3NDEKTSV4RRFFQ69G5ZZZ"))


@st.composite
def states_and_seats(draw: st.DrawFn) -> tuple[State, SeatId]:
    seats = draw(st.lists(s.seats, min_size=1, max_size=4, unique_by=lambda x: x.seat_id))
    state = State(
        campaign_id=draw(s.campaign_ids),
        assets={a.asset_id: a for a in draw(st.lists(s.assets, max_size=6))},
        relationships={r.relationship_id: r for r in draw(st.lists(s.relationships, max_size=4))},
        meters={m.meter_id: m for m in draw(st.lists(s.meters, max_size=4))},
        clocks={c.clock_id: c for c in draw(st.lists(s.clocks, max_size=6))},
        facts={f.fact_id: f for f in draw(st.lists(s.facts, max_size=8))},
        scheduled={e.scheduled_id: e for e in draw(st.lists(s.scheduled_events, max_size=3))},
        commitments={c.commitment_id: c for c in draw(st.lists(s.commitments, max_size=3))},
        seats={x.seat_id: x for x in seats},
    )
    return state, draw(st.sampled_from(seats)).seat_id


@h_settings(max_examples=300, deadline=None)
@given(states_and_seats())
def test_projection_is_sound(case: tuple[State, SeatId]) -> None:
    """Nothing in a view has a scope that excludes the viewer."""
    state, seat = case
    view = project(state, seat)
    scoped: list[HasScope] = [
        *view.assets.values(),
        *view.relationships.values(),
        *view.meters.values(),
        *view.clocks.values(),
        *view.facts.values(),
        *view.scheduled.values(),
        *view.commitments.values(),
    ]
    for item in scoped:
        assert item.scope.visible_to(seat)
    hidden = [
        f
        for f in state.facts.values()
        if f.scope.visibility is not Visibility.PUBLIC and seat not in f.scope.known_by
    ]
    for f in hidden:
        assert f.fact_id not in view.facts
    assert view.viewer == seat
