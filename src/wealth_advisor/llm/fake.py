from pydantic import BaseModel

from .base import Message


class FakeLLM:
    """Deterministic test double: returns queued responses in order and records calls."""

    def __init__(self, responses: list[BaseModel | str], model: str = "fake") -> None:
        self.model = model
        self._responses = list(responses)
        self.calls: list[list[Message]] = []

    def complete(self, messages: list[Message], schema: type[BaseModel] | None = None):
        self.calls.append(messages)
        if not self._responses:
            raise RuntimeError("FakeLLM has no more queued responses")
        resp = self._responses.pop(0)
        if schema is None:
            return resp if isinstance(resp, str) else resp.model_dump_json()
        if isinstance(resp, schema):
            return resp
        if isinstance(resp, str):
            return schema.model_validate_json(resp)
        return schema.model_validate(resp.model_dump())
