"""A scripted provider for tests. Never touches the network.

Outputs are queued per `purpose`; each is a pydantic instance (returned as is) or an
exception (raised). Every call is recorded exactly as a real provider would, so the
audit path is exercised too.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from pydantic import BaseModel

from .base import LLMProvider, LLMResponse, SchemaT, StructuredResult, Usage
from .errors import LLMError


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, model: str = "fake-1") -> None:
        self.model = model
        self._queues: dict[str, deque[Any]] = defaultdict(deque)
        self.calls: list[dict[str, Any]] = []

    @property
    def available(self) -> bool:
        return True

    def script(self, purpose: str, *outputs: BaseModel | Exception) -> FakeProvider:
        self._queues[purpose].extend(outputs)
        return self

    def clear(self, purpose: str) -> FakeProvider:
        self._queues[purpose].clear()
        return self

    def calls_for(self, purpose: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["purpose"] == purpose]

    def complete(
        self, prompt: str, *, system: str | None = None, purpose: str = "generic"
    ) -> LLMResponse:
        raise LLMError("FakeProvider only scripts structured calls")

    def _structured(
        self, system: str, user: str, schema: type[SchemaT], *, purpose: str
    ) -> StructuredResult:
        self.calls.append({"purpose": purpose, "system": system, "user": user, "schema": schema})
        queue = self._queues[purpose]
        if not queue:
            raise LLMError(f"FakeProvider has no scripted output left for {purpose!r}")
        item = queue.popleft()
        if isinstance(item, Exception):
            raise item
        if not isinstance(item, schema):
            raise LLMError(
                f"scripted output for {purpose!r} is {type(item).__name__}, wanted {schema.__name__}"
            )
        return StructuredResult(
            parsed=item,
            text=item.model_dump_json(),
            usage=Usage(input_tokens=100, output_tokens=50, cache_read_tokens=0),
        )
