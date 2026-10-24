"""The schema version moves with the schema (CONTRIBUTING.md, Rules).

Any change to the schema's shape (events, deltas, ontology, State, View) must
bump ``SCHEMA_VERSION`` and record the new fingerprint here. Descriptions and
titles are left out of the fingerprint, so a docstring edit needs no bump. Old
entries stay: they record which shape each number named.
"""

import hashlib
import json
from typing import Any

from revisionist.core import SCHEMA_VERSION
from revisionist.schema_export import build

FINGERPRINTS: dict[str, str] = {
    "11": "1770c651ebcae38a9273903012f29ecdc76628e52ca966b797f02d81674f6ce0",
    "12": "338078dee68421d3f8482f474ee45a1089c3aa0b9fb0ba384708e80e6c0df58e",
    "13": "52e4e8405f5b9b065def1ee6b47c686f0cbfb013b4cc836b3172db329a0e9921",
    "14": "541d333f00fdd0dd879c1499ced096a0cbecf298dffe3328637c5eb42c3a04f8",
    "15": "7ab8604b934d77c75ba385d502db7cdfd597f47c1086ccc2e20fa750c0ee0d61",
    "16": "bb4be1e8c0159133ac806ba87d8aec060ede948f340823136189f77bf59d8160",
}


def _shape(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _shape(v) for k, v in node.items() if k not in ("description", "title")}
    if isinstance(node, list):
        return [_shape(v) for v in node]
    return node


def fingerprint() -> str:
    text = json.dumps(_shape(build()), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def test_schema_version_matches_schema() -> None:
    actual = fingerprint()
    assert SCHEMA_VERSION in FINGERPRINTS, (
        f"SCHEMA_VERSION {SCHEMA_VERSION} has no fingerprint: add {actual!r} to FINGERPRINTS"
    )
    assert FINGERPRINTS[SCHEMA_VERSION] == actual, (
        "schema shape changed without a version bump: increment SCHEMA_VERSION in "
        f"src/revisionist/core/__init__.py and add {actual!r} to FINGERPRINTS under it"
    )


def test_fingerprints_are_unique() -> None:
    assert len(set(FINGERPRINTS.values())) == len(FINGERPRINTS)
