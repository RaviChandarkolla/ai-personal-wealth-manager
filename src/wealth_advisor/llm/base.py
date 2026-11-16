import threading
from typing import Protocol, TypeVar, overload

from pydantic import BaseModel

Message = dict[str, str]  # {"role": "system|user|assistant", "content": "..."}
T = TypeVar("T", bound=BaseModel)


class LLMBudgetExceeded(RuntimeError):
    """Raised when a pipeline run exceeds its configured maximum LLM call budget."""


class LLMClient(Protocol):
    model: str

    @overload
    def complete(self, messages: list[Message], schema: type[T]) -> T: ...
    @overload
    def complete(self, messages: list[Message], schema: None = None) -> str: ...

    def complete(self, messages: list[Message], schema: type[BaseModel] | None = None):
        """Return text, or a validated instance of `schema` when given."""
        ...


class BudgetedLLM:
    """Thread-safe wrapper enforcing a maximum number of LLM calls per pipeline run."""

    def __init__(self, inner: LLMClient, max_calls: int) -> None:
        self._inner = inner
        self.model = inner.model
        self.max_calls = max_calls
        self.calls = 0
        self._lock = threading.Lock()

    def complete(self, messages: list[Message], schema: type[BaseModel] | None = None):
        with self._lock:
            if self.calls >= self.max_calls:
                raise LLMBudgetExceeded(
                    f"LLM call budget ({self.max_calls}) exceeded for this run"
                )
            self.calls += 1
        return self._inner.complete(messages, schema)
