"""The opinion resolver (plan, Phase 2 unit B3).

Public opinion, moving with a lag. An address moves the target; each Monday the
Gallup "about right" figure steps one point toward it. Other opinion levers
(Churchill's broadcast, cabinet statements, Chiang's appeal) change nothing yet.
"""

from datetime import date, timedelta

from revisionist.core.realism import resolvers as rs
from revisionist.core.realism.pipeline import Order, OrderStatus
from revisionist.core.realism.quantity import QuantityChange
from revisionist.core.realism.resolvers.votes import put
from revisionist.core.state import State
from revisionist.core.world_compile import object_id

THEME_POINTS = {"aid_to_allies": 2, "freedom_of_the_seas": 3, "national_unity": 1}  # [proposed]
INCIDENT_POINTS = 1  # [proposed]
INCIDENTS = ("greer_incident", "kearny_hit", "reuben_james_sunk")
ABOUT_RIGHT = "gallup_fdr_about_right"


def _clamp(n: int) -> int:
    return max(0, min(100, n))


def resolve(state: State, order: Order) -> tuple[QuantityChange, ...]:
    """Changes from one order enacted through its pipeline."""
    points = THEME_POINTS.get(str(order.params.get("theme")))
    if order.status is not OrderStatus.ENACTED or order.lever != "public_address" or points is None:
        return ()
    base = rs.value(state, "opinion_target")
    base = rs.value(state, ABOUT_RIGHT) if base is None else base
    if base is None:
        return ()
    change = put(state, "opinion_target", _clamp(base + points), rs.settled_on(order))
    return () if change is None else (change,)


def _incidents_last_week(state: State, today: date) -> int:
    """Atlantic incidents that fired since the previous Monday, so each counts once."""
    ids = {object_id(state.campaign_id, "history", k): k for k in INCIDENTS}
    return sum(
        1
        for s in state.scheduled.values()
        if s.fired
        and (s.scheduled_id in ids or s.name in INCIDENTS)
        and s.on_date is not None
        and today - timedelta(days=7) <= s.on_date < today
    )


def on_day(state: State, today: date) -> tuple[QuantityChange, ...]:
    """On a Monday, fold in last week's incidents, then step one point toward the target."""
    about = rs.value(state, ABOUT_RIGHT)
    if today.weekday() != 0 or about is None:
        return ()
    target = rs.value(state, "opinion_target")
    bump = INCIDENT_POINTS * _incidents_last_week(state, today)
    out = []
    if bump:
        target = _clamp((about if target is None else target) + bump)
        out.append(put(state, "opinion_target", target, today))
    if target is not None and target != about:
        out.append(put(state, ABOUT_RIGHT, about + (1 if target > about else -1), today))
    return tuple(c for c in out if c is not None)
