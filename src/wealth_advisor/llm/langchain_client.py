"""LangChain chat-model adapter with optional Langfuse tracing (needs the `llm` extra)."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from .base import Message

log = logging.getLogger(__name__)

_trace_meta: ContextVar[dict[str, Any] | None] = ContextVar("_trace_meta", default=None)


@contextmanager
def trace_context(
    *,
    run_id: str | None = None,
    step: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Attach run_id (as Langfuse session_id), step name, tags, and metadata to nested LLM calls."""
    prev = _trace_meta.get() or {}
    merged_tags = list(dict.fromkeys([*(prev.get("tags") or []), *(tags or [])]))
    merged_meta = {**(prev.get("metadata") or {}), **(metadata or {})}
    if step:
        merged_meta["step"] = step
    nxt: dict[str, Any] = {
        **prev,
        "run_id": run_id or prev.get("run_id"),
        "step": step or prev.get("step"),
        "tags": merged_tags,
        "metadata": merged_meta,
    }
    token = _trace_meta.set(nxt)
    try:
        yield
    finally:
        _trace_meta.reset(token)


def flush_langfuse() -> None:
    """Flush pending Langfuse events to the server if initialized."""
    try:
        from langfuse import get_client

        get_client().flush()
    except (ImportError, RuntimeError, OSError) as exc:
        log.debug("Langfuse flush skipped: %s", exc)


class LangChainClient:
    def __init__(self, chat_model: Any, model: str, trace: bool = True) -> None:
        self.model = model
        self._llm = chat_model
        self._trace = trace
        self._langfuse = None
        if trace:
            from langfuse import get_client

            self._langfuse = get_client()

    def _build_config(self, schema: type[BaseModel] | None) -> RunnableConfig:
        if not self._trace:
            return {}
        from langfuse.langchain import CallbackHandler

        ctx = _trace_meta.get() or {}
        step = ctx.get("step") or (schema.__name__ if schema else "chat")
        run_id = ctx.get("run_id")
        tags = list(ctx.get("tags") or ["wealth-advisor"])
        if step and step not in tags:
            tags.append(step)

        meta: dict[str, Any] = dict(ctx.get("metadata") or {})
        meta["langfuse_trace_name"] = f"wealth_advisor.{step}"
        meta["langfuse_tags"] = tags
        if run_id:
            meta["langfuse_session_id"] = run_id
            meta["run_id"] = run_id
        if schema is not None:
            meta["schema"] = schema.__name__

        # Fresh CallbackHandler per call ensures thread-safety in ThreadPoolExecutor
        return {
            "callbacks": [CallbackHandler()],
            "run_name": f"wealth_advisor.{step}",
            "tags": tags,
            "metadata": meta,
        }

    def complete(self, messages: list[Message], schema: type[BaseModel] | None = None):
        tuples = [(m["role"], m["content"]) for m in messages]
        config = self._build_config(schema)
        if schema is None:
            return self._llm.invoke(tuples, config=config).content
        return self._llm.with_structured_output(schema).invoke(tuples, config=config)

