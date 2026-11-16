"""M15: Event bus (in-memory pub-sub + Redis Streams / Kafka adapter behind a common interface).

Supported topics:
- `market_data`: NAV updates, price drops, volatility spikes
- `fund_events`: rating downgrade, fund manager exit, SEBI regulatory action, TER hike
- `portfolio_ticks`: portfolio valuation / bucket weight snapshots for drift checks
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

EventTopic = Literal["market_data", "fund_events", "portfolio_ticks"]
VALID_TOPICS: frozenset[str] = frozenset({"market_data", "fund_events", "portfolio_ticks"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class MarketEvent:
    topic: EventTopic
    event_type: str  # e.g. "rating_downgrade", "manager_exit", "sebi_action", "nav_drop", "portfolio_drift"
    profile_id: str = "default_user"
    instrument_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_utc_now)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


EventHandler = Callable[[MarketEvent], None]


class EventBusBackend(Protocol):
    """Common interface for in-memory pub-sub, Redis Streams, or Kafka event bus."""

    def subscribe(self, topic: EventTopic, handler: EventHandler) -> None: ...

    def publish(self, event: MarketEvent) -> None: ...

    def history(self, topic: EventTopic | None = None) -> list[MarketEvent]: ...


class InMemoryEventBus:
    """Deterministic in-memory synchronous pub-sub implementation of `EventBusBackend`."""

    def __init__(self) -> None:
        self._subscribers: dict[EventTopic, list[EventHandler]] = defaultdict(list)
        self._history: list[MarketEvent] = []

    def subscribe(self, topic: EventTopic, handler: EventHandler) -> None:
        if topic not in VALID_TOPICS:
            raise ValueError(f"Unsupported topic {topic!r}; expected one of {sorted(VALID_TOPICS)}")
        if handler not in self._subscribers[topic]:
            self._subscribers[topic].append(handler)

    def publish(self, event: MarketEvent) -> None:
        if event.topic not in VALID_TOPICS:
            raise ValueError(f"Unsupported topic {event.topic!r}; expected one of {sorted(VALID_TOPICS)}")
        self._history.append(event)
        for handler in list(self._subscribers[event.topic]):
            handler(event)

    def history(self, topic: EventTopic | None = None) -> list[MarketEvent]:
        if topic is None:
            return list(self._history)
        return [e for e in self._history if e.topic == topic]


class StreamBrokerAdapter:
    """Adapter stub for Redis Streams / Kafka behind `EventBusBackend`."""

    def __init__(self, broker_url: str = "redis://localhost:6379/0") -> None:
        self.broker_url = broker_url
        self._delegate = InMemoryEventBus()

    def subscribe(self, topic: EventTopic, handler: EventHandler) -> None:
        self._delegate.subscribe(topic, handler)

    def publish(self, event: MarketEvent) -> None:
        self._delegate.publish(event)

    def history(self, topic: EventTopic | None = None) -> list[MarketEvent]:
        return self._delegate.history(topic)


EventBus = InMemoryEventBus
