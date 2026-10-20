"""Layer 1 gates: round-trip, closed schema, no free-form deltas, schema drift."""

import json
from pathlib import Path
from typing import Any, get_args

import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from pydantic import TypeAdapter, ValidationError

from revisionist.core import delta as d
from revisionist.core import events as ev
from revisionist.core.events import Event
from revisionist.core.ids import SeatId
from revisionist.core.visibility import Scope, Visibility
from revisionist.schema_export import SCHEMA_DIR, render
from tests import strategies as s

event_adapter: TypeAdapter[Event] = TypeAdapter(Event)
delta_adapter: TypeAdapter[Any] = TypeAdapter(d.Delta)


@h_settings(max_examples=200, deadline=None)
@given(s.events)
def test_event_round_trips_through_json(event: Event) -> None:
    again = event_adapter.validate_json(event_adapter.dump_json(event))
    assert again == event


@h_settings(max_examples=200, deadline=None)
@given(s.deltas)
def test_delta_round_trips_through_json(delta: Any) -> None:
    again = delta_adapter.validate_json(delta_adapter.dump_json(delta))
    assert again == delta


@pytest.mark.parametrize("name", sorted(s.ALL_MODEL_STRATEGIES))
def test_every_model_has_a_strategy_that_builds(name: str) -> None:
    example = s.ALL_MODEL_STRATEGIES[name].example()
    assert type(example).__name__ == name


def test_models_are_frozen_and_closed() -> None:
    scope = Scope(visibility=Visibility.PUBLIC)
    with pytest.raises(ValidationError):
        scope.visibility = Visibility.COVERT  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Scope(visibility=Visibility.PUBLIC, extra_field=1)  # type: ignore[call-arg]


def test_non_public_scope_needs_a_holder() -> None:
    with pytest.raises(ValidationError):
        Scope(visibility=Visibility.COVERT)
    seat = SeatId("01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert Scope(visibility=Visibility.PRIVATE, known_by=frozenset({seat})).visible_to(seat)


ID_FIELDS = {"controller", "owner", "subject", "target", "party", "beneficiary"}


def _is_id(field: str) -> bool:
    return field.endswith("_id") or field in ID_FIELDS


def test_deltas_have_no_free_form_fields() -> None:
    """No dict/Any payloads; every string is an ID, an enum or capped (spec §12)."""
    for cls in get_args(get_args(d.Delta)[0]):
        schema = cls.model_json_schema()
        for field, spec in schema["properties"].items():
            assert spec.get("type") != "object", f"{cls.__name__}.{field} is free-form"
            assert "additionalProperties" not in spec, f"{cls.__name__}.{field} is free-form"
            if spec.get("type") == "string" and field != "kind" and not _is_id(field):
                is_enum = "enum" in spec or "$ref" in spec or "anyOf" in spec
                assert is_enum or "maxLength" in spec, f"{cls.__name__}.{field} is uncapped"


def test_mutating_kinds_are_all_real_kinds() -> None:
    kinds = {cls.model_fields["kind"].default for cls in get_args(get_args(ev.Payload)[0])}
    assert ev.MUTATING_KINDS <= kinds
    assert "delta_applied" in ev.MUTATING_KINDS
    assert "narration_written" not in ev.MUTATING_KINDS


def test_committed_schema_is_current() -> None:
    path = SCHEMA_DIR / "revisionist.schema.json"
    assert path.exists(), "run: uv run python -m revisionist.schema_export"
    assert json.loads(path.read_text()) == json.loads(render()), (
        "schema drift: run uv run python -m revisionist.schema_export and commit the result"
    )


def test_schema_file_lives_at_repo_root() -> None:
    assert SCHEMA_DIR == Path(__file__).resolve().parents[1] / "schema"
