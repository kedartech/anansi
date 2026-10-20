"""Which key seals an event, and which events a person reading them must log (Layer 9b).

Pure; the cryptography lives in ``engine/seal.py``. Two tiers of key:

- the campaign key seals every event and snapshot of a sealed campaign, so the
  fold can always read the game;
- a seat key seals that seat's own words: what its player typed
  (``order_received``), what its agent asked back (``clarification_asked``) and
  its private briefings (``narration_written``). None of these is folded, so
  destroying a seat key erases them and replay still works.
"""

from typing import Any

from revisionist.core import events as ev
from revisionist.core._base import Model
from revisionist.core.events import Event
from revisionist.core.ids import SeatId
from revisionist.core.visibility import Scope, Visibility


def seal_holder(event: Event) -> SeatId | None:
    """The seat whose key seals this event, or ``None`` for the campaign key."""
    p = event.payload
    if isinstance(p, ev.OrderReceived | ev.ClarificationAsked | ev.GoalProposed):
        return p.seat_id
    if isinstance(p, ev.NarrationWritten) and event.scope.visibility is not Visibility.PUBLIC:
        return p.seat_id
    return None


def scopes_in(value: Any) -> list[Scope]:
    """Every ``Scope`` nested anywhere in a model, such as a covert fact inside a delta."""
    if isinstance(value, Scope):
        return [value]
    if isinstance(value, Model):
        return [s for name in type(value).model_fields for s in scopes_in(getattr(value, name))]
    if isinstance(value, tuple | list | frozenset | set):
        return [s for item in value for s in scopes_in(item)]
    if isinstance(value, dict):
        return [s for item in value.values() for s in scopes_in(item)]
    return []


def needs_admin_read(event: Event) -> bool:
    """Showing this event to a person is an admin read: something in it is not public.

    The envelope is not enough: ``delta_applied`` is logged Public even when its
    delta creates a covert fact.
    """
    if seal_holder(event) is not None:
        return True
    return any(
        s.visibility is not Visibility.PUBLIC for s in [event.scope, *scopes_in(event.payload)]
    )


__all__ = ["needs_admin_read", "scopes_in", "seal_holder"]
