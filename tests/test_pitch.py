"""The pitch and its setting card (spec §5.2 steps 1-2): refusals, one call, reply checks."""

import json
from datetime import date
from typing import Any

import pytest

from revisionist.engine.llm import Reply, Request
from revisionist.engine.pitch import PITCH_MAX, PITCH_SCHEMA, PitchError, read_pitch


class Pitch:
    """Replies to ``read_pitch`` calls with the given replies, in order."""

    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies = list(replies)
        self.requests: list[Request] = []

    def call(self, request: Request) -> Reply:
        assert request.call_class == "pitch"
        self.requests.append(request)
        return Reply(self.replies.pop(0), request.model, 900, 400, 0, 0.01)


def start(label: str, when: str, **over: Any) -> dict[str, Any]:
    point: dict[str, Any] = {
        "label": label,
        "start_date": when,
        "contests": ["Western front: the Low Countries and France"],
        "powers": ["France", "Germany"],
        "focus": "The Meuse crossings",
        "schedule": ["1940-05-14: German armour crosses the Meuse at Sedan."],
    }
    point.update(over)
    return point


def card(
    *points: dict[str, Any], refusal: str = "", named: list[str] | None = None
) -> dict[str, Any]:
    return {
        "reading": "The Western Front in 1940, read from the spring offensive.",
        "start_points": list(points),
        "named": ["Erwin Rommel"] if named is None else named,
        "refusal": refusal,
    }


def test_an_empty_pitch_is_refused_with_no_call() -> None:
    gm = Pitch()
    with pytest.raises(PitchError) as refused:
        read_pitch("   ", gm)
    assert refused.value.reason == "Give a pitch: a war, a year, a person, a place."
    assert gm.requests == []


def test_an_over_long_pitch_is_refused_with_no_call() -> None:
    gm = Pitch()
    with pytest.raises(PitchError) as refused:
        read_pitch("x" * (PITCH_MAX + 1), gm)
    assert refused.value.reason == f"A pitch is at most {PITCH_MAX} characters."
    assert gm.requests == []


def test_a_good_reply_builds_the_card_and_makes_one_pitch_call() -> None:
    gm = Pitch(card(start("Before the Meuse", "1940-05-10"), start("Dunkirk", "1940-05-26")))
    result, costs = read_pitch("  WWII  ", gm, effort=2)
    assert len(gm.requests) == 1
    request = gm.requests[0]
    assert request.effort == 2
    assert "WWII" in request.prompt
    assert request.schema == PITCH_SCHEMA
    assert result.reading == "The Western Front in 1940, read from the spring offensive."
    assert result.named == ("Erwin Rommel",)
    assert [p.label for p in result.start_points] == ["Before the Meuse", "Dunkirk"]
    assert result.start_points[0].start_date == date(1940, 5, 10)
    assert result.start_points[0].contests == ("Western front: the Low Countries and France",)
    assert result.start_points[0].powers == ("France", "Germany")
    assert result.start_points[0].focus == "The Meuse crossings"
    assert result.start_points[0].schedule == (
        "1940-05-14: German armour crosses the Meuse at Sedan.",
    )
    assert [c.call_class for c in costs] == ["pitch"]


def test_a_refusal_raises_with_its_text_and_the_cost() -> None:
    gm = Pitch(card(refusal="No real period in that pitch."))
    with pytest.raises(PitchError) as refused:
        read_pitch("a dragon's reign", gm)
    assert refused.value.reason == "No real period in that pitch."
    assert [c.call_class for c in refused.value.costs] == ["pitch"]


def test_a_start_point_with_a_bad_date_is_dropped() -> None:
    gm = Pitch(card(start("Bad", "1940-02-30"), start("Good", "1940-05-10")))
    result, _ = read_pitch("1940", gm)
    assert [p.label for p in result.start_points] == ["Good"]


def test_a_start_point_after_2025_is_dropped() -> None:
    gm = Pitch(card(start("Too late", "2026-01-01"), start("Good", "2025-12-31")))
    result, _ = read_pitch("the present", gm)
    assert [p.label for p in result.start_points] == ["Good"]


def test_all_bad_dates_are_refused_with_the_cost() -> None:
    gm = Pitch(card(start("A", "1940-02-30"), start("B", "2030-01-01")))
    with pytest.raises(PitchError) as refused:
        read_pitch("1940", gm)
    assert refused.value.reason == "The start dates did not read as real dates."
    assert [c.call_class for c in refused.value.costs] == ["pitch"]


def test_more_than_three_start_points_keep_the_first_three() -> None:
    points = [start(f"Point {n}", f"194{n}-05-10") for n in range(4)]
    result, _ = read_pitch("1940s", Pitch(card(*points)))
    assert [p.label for p in result.start_points] == ["Point 0", "Point 1", "Point 2"]


def test_no_start_points_and_no_refusal_says_no_period_can_be_read() -> None:
    with pytest.raises(PitchError) as refused:
        read_pitch("a feeling", Pitch(card()))
    assert refused.value.reason == "That pitch names no period I can read."
    assert [c.call_class for c in refused.value.costs] == ["pitch"]


def test_a_reply_missing_a_field_is_malformed() -> None:
    reply = card(start("Good", "1940-05-10"))
    del reply["named"]
    with pytest.raises(PitchError) as refused:
        read_pitch("1940", Pitch(reply))
    assert refused.value.reason == "The setting card came back malformed. Try again."
    assert [c.call_class for c in refused.value.costs] == ["pitch"]


def test_the_schema_is_plain_json_schema() -> None:
    text = json.dumps(PITCH_SCHEMA)
    assert "$ref" not in text
    assert "discriminator" not in text
