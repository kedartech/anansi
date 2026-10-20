"""Shared model configuration for the pure core."""

from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
)


class Model(BaseModel):
    """Immutable, closed record. Every core model inherits this.

    Frozen because state is a value: the fold returns a new state rather than
    mutating one. ``extra="forbid"`` so an LLM proposal with a stray field
    fails validation instead of being silently accepted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)

    @model_serializer(mode="wrap")
    def _serialize_sets_sorted(self, handler: SerializerFunctionWrapHandler) -> Any:
        """Emit every set-valued field in sorted order.

        Set iteration order depends on string hashing, which is randomised per
        process. The state hash and the committed schema both need the same
        bytes from every process, so sets leave the model sorted.
        """
        data = handler(self)
        if isinstance(data, dict):
            for name in type(self).model_fields:
                if name in data and isinstance(getattr(self, name, None), frozenset | set):
                    data[name] = sorted(data[name])
        return data


Label = Annotated[str, StringConstraints(max_length=60)]
"""A short, capped string for names, predicates and tags. Never prose."""
