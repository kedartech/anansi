"""The shipping resolver (plan, Phase 2 unit B3).

Atlantic losses from U-boat strength and escort level. Churchill's
``convoy_orders`` and Mussolini's ``libya_convoys`` change nothing in this wave.
Once the escort level has reached 1 the escort factor counts from that month, even
if a later order holds the ships in port.
"""

from datetime import date, timedelta

from revisionist.core.realism import resolvers as rs
from revisionist.core.realism.pipeline import Order, OrderStatus
from revisionist.core.realism.quantity import QuantityChange
from revisionist.core.realism.resolvers.votes import put
from revisionist.core.state import State

ESCORT_LEVEL = {"hold_in_port": 0, "escort_convoys": 1, "shoot_on_sight": 2}
ESCORT_ZONES = ("western_atlantic", "iceland_route")
POSTURE = {"restrict_us_ships": 0, "unrestricted_atlantic": 1}
PER_BOAT_GRT = 209_835 / 30  # [fitted] to Sep 1941 (209,835 GRT, 30 boats)
ESCORT_STEP_PM = 150  # [fitted] to the Oct (176,622) and Nov (120,541) record
ESCORT_FLOOR_PM = 500  # [proposed]
UNRESTRICTED_PM = 1300  # [proposed]
NOISE_PM = 80  # [proposed] friction keeps losses within +-8%
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def resolve(state: State, order: Order) -> tuple[QuantityChange, ...]:
    """Changes from one order enacted through its pipeline."""
    if order.status is not OrderStatus.ENACTED:
        return ()
    as_of = rs.settled_on(order)
    if order.lever == "naval_order" and order.params.get("zone") in ESCORT_ZONES:
        level = ESCORT_LEVEL.get(str(order.params.get("order")))
        change = None if level is None else put(state, "us_atlantic_escort", level, as_of)
    elif order.lever == "uboat_directive" and order.params.get("posture") in POSTURE:
        change = put(state, "uboat_posture", POSTURE[str(order.params["posture"])], as_of)
    else:
        return ()
    return () if change is None else (change,)


def _escorted_since(state: State, today: date) -> date | None:
    """The day the first enacted escort order (level 1 or more) settled."""
    days = [
        rs.settled_on(o)
        for o in state.orders.values()
        if o.status is OrderStatus.ENACTED
        and o.lever == "naval_order"
        and o.params.get("zone") in ESCORT_ZONES
        and ESCORT_LEVEL.get(str(o.params.get("order")), 0) >= 1
        and rs.settled_on(o) <= today
    ]
    return min(days, default=None)


def on_day(state: State, today: date) -> tuple[QuantityChange, ...]:
    """On the 1st, the month just ended's losses, if that quantity is still a gap."""
    if today.day != 1:
        return ()
    last = today - timedelta(days=1)
    key = f"uboat_losses_{MONTHS[last.month - 1]}"
    if rs.qid(state, key) not in state.quantities or rs.value(state, key) is not None:
        return ()
    boats = rs.value(state, "uboats_operational")
    if boats is None:
        return ()
    since = _escorted_since(state, last)
    months = 0 if since is None else (last.year - since.year) * 12 + last.month - since.month
    escort_pm = max(ESCORT_FLOOR_PM, 1000 - ESCORT_STEP_PM * months)
    posture_pm = UNRESTRICTED_PM if rs.value(state, "uboat_posture") == 1 else 1000
    noise_pm = 1000 - NOISE_PM + rs.friction(state, key, n=2 * NOISE_PM + 1)
    losses = round(PER_BOAT_GRT * boats * escort_pm * posture_pm * noise_pm / 10**9)
    change = put(state, key, losses, last)
    return () if change is None else (change,)
