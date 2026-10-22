"""The pitch and its setting card (spec §5.2). Phase 2 unit.

``/establish <pitch>`` makes one cheap model call that reads the pitch and
returns a card: how the engine read it, 2-3 start points just before a turning
point, each with its date, contests, powers and area in focus, and any people
named. Nothing is written to a campaign until the host confirms a start point.
"""

from datetime import date

from revisionist.core._base import Model
from revisionist.engine.llm import Transport

PITCH_MAX = 600


class StartPoint(Model):
    label: str
    start_date: date
    contests: tuple[str, ...]
    powers: tuple[str, ...]
    focus: str


class SettingCard(Model):
    reading: str
    start_points: tuple[StartPoint, ...]
    named: tuple[str, ...] = ()


def read_pitch(pitch: str, transport: Transport) -> SettingCard:
    """One model call; refuses an empty or over-long pitch before calling."""
    raise NotImplementedError("Session Zero v3, Phase 2: pitch card")
