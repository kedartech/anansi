"""The opinion resolver (plan, Phase 2 unit B3).

Public opinion, moving with a lag.
"""

from datetime import date

from revisionist.core.realism.pipeline import Order
from revisionist.core.realism.quantity import QuantityChange
from revisionist.core.state import State


def resolve(state: State, order: Order) -> tuple[QuantityChange, ...]:
    """Changes from one order enacted through its pipeline."""
    raise NotImplementedError("unit B3")


def on_day(state: State, today: date) -> tuple[QuantityChange, ...]:
    """Changes that come with time alone, for one in-world day."""
    raise NotImplementedError("unit B3")
