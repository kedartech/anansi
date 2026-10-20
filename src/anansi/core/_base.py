"""Shared model configuration for the pure core."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class Model(BaseModel):
    """Immutable, closed record. Every core model inherits this.

    Frozen because state is a value: the fold returns a new state rather than
    mutating one. ``extra="forbid"`` so an LLM proposal with a stray field
    fails validation instead of being silently accepted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)


Label = Annotated[str, StringConstraints(max_length=60)]
"""A short, capped string for names, predicates and tags. Never prose."""
