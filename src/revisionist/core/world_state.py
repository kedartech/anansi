"""The public world state after a tick (playtest #31), built in code.

Reads only PUBLIC-scope events, so the channel never learns what a seat hid.
Unit U8 fills it in; U7 posts it after ``/advance``.
"""

from collections.abc import Sequence

from revisionist.core.events import Event
from revisionist.core.state import State


def world_state(state: State, events: Sequence[Event], tick: int) -> list[str]:
    """Plain lines for the channel. A line ending in ``:`` is a section header."""
    return []


__all__ = ["world_state"]
