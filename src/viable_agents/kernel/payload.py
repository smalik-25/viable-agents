"""Payload base and registry. The kernel never names a concrete payload type.

This is the answer to the hardest kernel question: type message bodies so mypy
strict is satisfied and Phase 2+ can add message types without editing the kernel.
A discriminated union in the kernel would fail the second requirement; a bare
``dict`` would fail the first. Instead every body is a ``Payload`` subclass that
declares a ``kind`` literal, self-registers, and is rehydrated by that key.
"""

from __future__ import annotations

from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict
from pydantic_core import PydanticUndefined


class PayloadKindError(ValueError):
    """A payload kind is unknown, missing, or double-registered."""


class Payload(BaseModel):
    """Base for every message body. Concrete subclasses live outside ``kernel/``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str

    _registry: ClassVar[dict[str, type[Payload]]] = {}

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        # Not __init_subclass__: that fires before Pydantic populates
        # model_fields, so `kind` reads back as PydanticUndefined and every
        # subclass registers under the same key.
        super().__pydantic_init_subclass__(**kwargs)
        field = cls.model_fields.get("kind")
        if field is None or field.default is PydanticUndefined or field.default is None:
            return  # an intermediate abstract subclass; nothing to register
        key = str(field.default)
        existing = Payload._registry.get(key)
        if existing is not None and existing is not cls:
            msg = f"payload kind {key!r} already registered to {existing.__name__}"
            raise PayloadKindError(msg)
        Payload._registry[key] = cls

    @staticmethod
    def resolve(kind: str) -> type[Payload]:
        try:
            return Payload._registry[kind]
        except KeyError as exc:
            msg = f"unknown payload kind {kind!r}"
            raise PayloadKindError(msg) from exc

    @classmethod
    def rehydrate(cls, data: dict[str, Any]) -> Payload:
        """Rebuild the concrete subclass from a persisted JSONB row."""
        kind = data.get("kind")
        if not isinstance(kind, str):
            msg = f"payload dict missing str 'kind': {data!r}"
            raise PayloadKindError(msg)
        return Payload.resolve(kind).model_validate(data)

    def narrow[T: Payload](self, expected: type[T]) -> T:
        """Typed downcast used by agents; raises rather than returning None."""
        if not isinstance(self, expected):
            msg = f"expected payload {expected.__name__}, got {type(self).__name__}"
            raise PayloadKindError(msg)
        return self

    def redacted(self) -> Self:
        """Override in payloads carrying CI log text before it reaches a prompt or trace."""
        return self
