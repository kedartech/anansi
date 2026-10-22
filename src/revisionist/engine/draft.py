"""The staged world draft with partial retry (spec §5.2). Phase 2 unit.

After the host confirms a start point, the world is drafted in stages (frame,
contests and schedule, people and roster), and a refused stage is retried on
its own instead of redrawing the whole file.
"""

from collections.abc import Callable

from revisionist.core.events import CallCost
from revisionist.core.settings import Settings
from revisionist.core.world import WorldFile
from revisionist.engine.llm import Transport
from revisionist.engine.pitch import SettingCard, StartPoint


def draft(
    card: SettingCard,
    start: StartPoint,
    settings: Settings,
    transport: Transport,
    effort: int | None = None,
    *,
    detail_effort: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[WorldFile, list[CallCost]]:
    """Draft and check a v3 world file for ``start`` (its ``schedule`` already
    has the host's strikes removed). Raises ``SessionZeroError`` if a stage never
    passes the checker. ``progress`` gets one plain line per finished stage."""
    raise NotImplementedError("Session Zero v3, Phase 2: staged draft")
