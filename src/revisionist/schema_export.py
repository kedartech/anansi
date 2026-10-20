"""Export the JSON Schema for the event log so schema drift shows in review.

Run ``uv run python -m revisionist.schema_export`` after changing anything in
``revisionist.core``; ``tests/test_schema.py`` fails if the committed file is stale.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from revisionist.core.events import Event
from revisionist.core.state import State, View

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"


def build() -> dict[str, Any]:
    return {
        "event": TypeAdapter(Event).json_schema(),
        "state": TypeAdapter(State).json_schema(),
        "view": TypeAdapter(View).json_schema(),
    }


def render() -> str:
    return json.dumps(build(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    SCHEMA_DIR.mkdir(exist_ok=True)
    (SCHEMA_DIR / "revisionist.schema.json").write_text(render())


if __name__ == "__main__":
    main()
