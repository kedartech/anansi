"""The staged world draft with partial retry (spec §5.2). Phase 2 unit.

After the host confirms a start point, the world is drafted in stages (frame,
contests and schedule, people and roster), and a refused stage is retried on
its own instead of redrawing the whole file.
"""

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
) -> WorldFile:
    """Draft and check a v3 world file for ``start``. Raises if a stage never compiles."""
    raise NotImplementedError("Session Zero v3, Phase 2: staged draft")
