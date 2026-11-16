import hashlib
import json
import threading
from pathlib import Path

from pydantic import BaseModel

from .base import LLMClient, Message


class CachedLLM:
    """On-disk cache keyed by (model, messages, schema); wraps any LLMClient."""

    def __init__(self, inner: LLMClient, cache_dir: Path) -> None:
        self._inner = inner
        self.model = inner.model
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()

    def _key(self, messages: list[Message], schema: type[BaseModel] | None) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "schema": schema.model_json_schema() if schema else None,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def complete(self, messages: list[Message], schema: type[BaseModel] | None = None):
        path = self._dir / f"{self._key(messages, schema)}.json"
        with self._lock:
            if path.exists():
                self.hits += 1
                raw = json.loads(path.read_text())["output"]
                return schema.model_validate_json(raw) if schema else raw
            self.misses += 1
        result = self._inner.complete(messages, schema)
        out = result.model_dump_json() if isinstance(result, BaseModel) else result
        with self._lock:
            path.write_text(json.dumps({"output": out}))
        return result
