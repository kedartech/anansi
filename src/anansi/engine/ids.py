"""Mint IDs. Lives outside the core because a ULID reads the clock."""

from ulid import ULID


def new_id() -> str:
    return str(ULID())
