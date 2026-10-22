"""The pitch and its setting card (spec §5.2). Phase 2 unit.

``/establish <pitch>`` makes one cheap model call that reads the pitch and
returns a card: how the engine read it, 2-3 start points just before a turning
point, each with its date, contests, powers and area in focus, and any people
named. Nothing is written to a campaign until the host confirms a start point.
"""

from datetime import date
from typing import Any

from pydantic import Field, ValidationError

from revisionist.core._base import Model
from revisionist.core.events import CallCost
from revisionist.engine.llm import Request, Transport, prompt_text

PITCH_MAX = 200
PITCH_MODEL = "claude-haiku-5-5"

_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_TEXT = {"type": "array", "items": {"type": "string"}}

PITCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reading": {"type": "string"},
        "start_points": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "start_date": _DATE,
                    "contests": {**_TEXT, "minItems": 1, "maxItems": 3},
                    "powers": _TEXT,
                    "focus": {"type": "string"},
                    "schedule": {**_TEXT, "minItems": 0, "maxItems": 12},
                },
                "required": ["label", "start_date", "contests", "powers", "focus", "schedule"],
                "additionalProperties": False,
            },
        },
        "named": _TEXT,
        "refusal": {"type": "string"},
    },
    "required": ["reading", "start_points", "named", "refusal"],
    "additionalProperties": False,
}


class StartPoint(Model):
    label: str
    start_date: date
    contests: tuple[str, ...]
    powers: tuple[str, ...]
    focus: str
    schedule: tuple[str, ...] = ()
    """What really followed this date, one line each, as the host may strike."""


class SettingCard(Model):
    reading: str
    start_points: tuple[StartPoint, ...]
    named: tuple[str, ...] = ()


class PitchError(ValueError):
    """The pitch was refused: empty, too long, or no readable period. ``reason`` is
    plain text for the host."""

    def __init__(self, reason: str, costs: list[CallCost] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.costs = costs or []


LATEST_START = date(2025, 12, 31)


class _RawStart(Model):
    label: str
    start_date: str
    contests: tuple[str, ...] = Field(min_length=1, max_length=3)
    powers: tuple[str, ...]
    focus: str
    schedule: tuple[str, ...] = Field(max_length=12)


class _RawCard(Model):
    reading: str
    start_points: tuple[_RawStart, ...]
    named: tuple[str, ...]
    refusal: str


def _real_date(text: str) -> date | None:
    try:
        when = date.fromisoformat(text)
    except ValueError:
        return None
    return when if when <= LATEST_START else None


def _card(output: Any, costs: list[CallCost]) -> SettingCard:
    try:
        raw = _RawCard.model_validate(output)
    except ValidationError as err:
        raise PitchError("The setting card came back malformed. Try again.", costs) from err
    if raw.refusal.strip():
        raise PitchError(raw.refusal.strip(), costs)
    if not raw.start_points:
        raise PitchError("That pitch names no period I can read.", costs)
    points: list[StartPoint] = []
    for point in raw.start_points:
        when = _real_date(point.start_date)
        if when is None:
            continue
        points.append(
            StartPoint(
                label=point.label,
                start_date=when,
                contests=point.contests,
                powers=point.powers,
                focus=point.focus,
                schedule=point.schedule,
            )
        )
    if not points:
        raise PitchError("The start dates did not read as real dates.", costs)
    return SettingCard(reading=raw.reading, start_points=tuple(points[:3]), named=raw.named)


class PendingCard(Model):
    """A setting card waiting for the host's confirm, with what reading the pitch cost.

    Discord keeps one per host in memory; the CLI saves it to a file between the
    two commands. ``costs`` go to ``session_zero.confirm(card_costs=...)``."""

    pitch: str
    card: SettingCard
    costs: tuple[CallCost, ...] = ()


def _day(when: date) -> str:
    return f"{when.day} {when:%B} {when.year}"


def card_lines(card: SettingCard) -> list[str]:
    """The card as plain lines for the host: the reading, each start point by
    number with its history numbered for strikes, and anyone the pitch named."""
    lines = [card.reading.strip()]
    for n, p in enumerate(card.start_points, 1):
        lines.append(f"{n}. {p.label}, {_day(p.start_date)}. Focus: {p.focus}.")
        lines.append("   Contests: " + "; ".join(p.contests) + ".")
        if p.powers:
            lines.append("   Powers: " + ", ".join(p.powers) + ".")
        if p.schedule:
            lines.append("   History from here (strike by number):")
            lines += [f"   {i}) {item}" for i, item in enumerate(p.schedule, 1)]
    if card.named:
        lines.append("Named in the pitch: " + ", ".join(card.named) + ".")
    return lines


def strikes_from(text: str) -> tuple[int, ...]:
    """``"2, 4"`` or ``"2 4"`` as schedule numbers. Raises ``PitchError``."""
    parts = [p for p in text.replace(",", " ").split() if p]
    if not all(p.isdigit() for p in parts):
        raise PitchError("Strike history items by number, e.g. 2, 4.")
    return tuple(sorted({int(p) for p in parts}))


def end_from(text: str) -> date:
    """An in-world end date as ``YYYY-MM-DD``. Raises ``PitchError``."""
    try:
        return date.fromisoformat(text.strip())
    except ValueError as err:
        raise PitchError("Give the end date as YYYY-MM-DD, e.g. 1945-05-08.") from err


def read_pitch(
    pitch: str, transport: Transport, effort: int | None = None
) -> tuple[SettingCard, list[CallCost]]:
    """One model call; refuses an empty or over-long pitch before calling."""
    text = pitch.strip()
    if not text:
        raise PitchError("Give a pitch: a war, a year, a person, a place.")
    if len(text) > PITCH_MAX:
        raise PitchError(f"A pitch is at most {PITCH_MAX} characters.")
    request = Request(
        call_class="pitch",
        model=PITCH_MODEL,
        system=prompt_text("pitch"),
        prompt=f"## Pitch\n{text}",
        schema=PITCH_SCHEMA,
        max_usd=0.3,
        effort=effort,
    )
    reply = transport.call(request)
    costs = [reply.cost("pitch")]
    return _card(reply.output, costs), costs
