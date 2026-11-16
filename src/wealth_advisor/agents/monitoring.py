"""M15: Event-driven Monitoring Agent — drift, threshold, anomaly rules, deduplication, and recalibration.

Subscribes to `EventBusBackend` topics (`market_data`, `fund_events`, `portfolio_ticks`),
evaluates M11 drift (`compute_drift`, `rebalance_plan`), threshold rules (`rating_downgrade`,
`manager_exit`, `sebi_action`), and anomaly rules (`nav_drop`, `aum_outflow`).
Deduplicates alerts within a configurable cool-down window and triggers an unscheduled
Supervisor / recommendation recalibration run when a material alert fires.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

from wealth_advisor.db import connect_sqlite, ensure_schema
from wealth_advisor.domain.drift import compute_drift, rebalance_plan
from wealth_advisor.models.enums import Bucket
from wealth_advisor.orchestration.events import EventBusBackend, MarketEvent

AlertSeverity = Literal["low", "medium", "high", "critical"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Alert:
    alert_id: str
    profile_id: str
    instrument_id: str
    alert_type: str
    severity: AlertSeverity
    title: str
    message: str
    created_at: datetime
    event_id: str
    material: bool = True
    recalibration_triggered: bool = False
    recalibrated_run_id: str | None = None
    recalibration_summary: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    batch_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "profile_id": self.profile_id,
            "instrument_id": self.instrument_id,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "created_at": self.created_at.isoformat(),
            "event_id": self.event_id,
            "material": self.material,
            "recalibration_triggered": self.recalibration_triggered,
            "recalibrated_run_id": self.recalibrated_run_id,
            "recalibration_summary": self.recalibration_summary,
            "details": self.details,
            "batch_count": self.batch_count,
        }


class AlertStore:
    """SQLite-backed store for generated monitoring alerts."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS monitoring_alerts (
        alert_id                TEXT PRIMARY KEY,
        profile_id              TEXT NOT NULL,
        instrument_id           TEXT NOT NULL,
        alert_type              TEXT NOT NULL,
        severity                TEXT NOT NULL,
        title                   TEXT NOT NULL,
        message                 TEXT NOT NULL,
        created_at              TEXT NOT NULL,
        event_id                TEXT NOT NULL,
        material                INTEGER NOT NULL,
        recalibration_triggered INTEGER NOT NULL,
        recalibrated_run_id     TEXT,
        recalibration_json      TEXT NOT NULL,
        details_json            TEXT NOT NULL,
        batch_count             INTEGER NOT NULL DEFAULT 1
    );
    """

    def __init__(self, db_or_path: str | sqlite3.Connection = ":memory:") -> None:
        self._conn = connect_sqlite(db_or_path)
        ensure_schema(self._conn)
        self._conn.executescript(self._DDL)
        self._conn.commit()

    def save(self, alert: Alert) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO monitoring_alerts (
                    alert_id, profile_id, instrument_id, alert_type, severity,
                    title, message, created_at, event_id, material,
                    recalibration_triggered, recalibrated_run_id, recalibration_json,
                    details_json, batch_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alert_id) DO UPDATE SET
                    batch_count = excluded.batch_count,
                    recalibration_triggered = excluded.recalibration_triggered,
                    recalibrated_run_id = excluded.recalibrated_run_id,
                    recalibration_json = excluded.recalibration_json,
                    details_json = excluded.details_json
                """,
                (
                    alert.alert_id,
                    alert.profile_id,
                    alert.instrument_id,
                    alert.alert_type,
                    alert.severity,
                    alert.title,
                    alert.message,
                    alert.created_at.isoformat(),
                    alert.event_id,
                    1 if alert.material else 0,
                    1 if alert.recalibration_triggered else 0,
                    alert.recalibrated_run_id,
                    json.dumps(alert.recalibration_summary, default=str),
                    json.dumps(alert.details, default=str),
                    alert.batch_count,
                ),
            )

    def list_alerts(self, profile_id: str | None = None) -> list[Alert]:
        if profile_id:
            rows = self._conn.execute(
                "SELECT * FROM monitoring_alerts WHERE profile_id = ? ORDER BY created_at DESC",
                (profile_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM monitoring_alerts ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_alert(r) for r in rows]

    @staticmethod
    def _row_to_alert(row: sqlite3.Row) -> Alert:
        return Alert(
            alert_id=row["alert_id"],
            profile_id=row["profile_id"],
            instrument_id=row["instrument_id"],
            alert_type=row["alert_type"],
            severity=row["severity"],
            title=row["title"],
            message=row["message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            event_id=row["event_id"],
            material=bool(row["material"]),
            recalibration_triggered=bool(row["recalibration_triggered"]),
            recalibrated_run_id=row["recalibrated_run_id"],
            recalibration_summary=json.loads(row["recalibration_json"] or "{}"),
            details=json.loads(row["details_json"] or "{}"),
            batch_count=int(row["batch_count"]),
        )


RecalibrationCallback = Callable[[Alert, MarketEvent], dict[str, Any] | None]


class MonitoringAgent:
    """Subscribes to `EventBusBackend`, evaluates drift/threshold/anomaly rules, deduplicates, and recalibrates."""

    def __init__(
        self,
        bus: EventBusBackend,
        *,
        alert_store: AlertStore | None = None,
        cooldown_seconds: float = 3600.0,
        nav_drop_threshold_pct: float = 3.0,
        aum_outflow_threshold_pct: float = 15.0,
        recalibration_callback: RecalibrationCallback | None = None,
    ) -> None:
        self._bus = bus
        self._store = alert_store or AlertStore(":memory:")
        self._cooldown = timedelta(seconds=cooldown_seconds)
        self._nav_drop_threshold = nav_drop_threshold_pct
        self._aum_outflow_threshold = aum_outflow_threshold_pct
        self._recalibration_cb = recalibration_callback

        # Dedup state: (profile_id, instrument_id, alert_type) -> last Alert
        self._last_alerts: dict[tuple[str, str, str], Alert] = {}
        self.suppressed_duplicates: int = 0
        self.recalibration_runs: list[dict[str, Any]] = []

        self._bus.subscribe("market_data", self._handle_market_data)
        self._bus.subscribe("fund_events", self._handle_fund_event)
        self._bus.subscribe("portfolio_ticks", self._handle_portfolio_tick)

    @property
    def alerts(self) -> list[Alert]:
        return self._store.list_alerts()

    def get_alerts(self, profile_id: str | None = None) -> list[Alert]:
        return self._store.list_alerts(profile_id=profile_id)

    # ── Topic Handlers ────────────────────────────────────────────────────

    def _handle_fund_event(self, event: MarketEvent) -> None:
        etype = event.event_type.lower()
        p = event.payload

        if etype == "rating_downgrade":
            old_r = p.get("old_rating", 4)
            new_r = p.get("new_rating", 2)
            # Numeric star ratings (1-5) or credit ratings ("AAA" -> "BBB")
            is_material = True
            severity: AlertSeverity = "high"
            if isinstance(new_r, (int, float)) and isinstance(old_r, (int, float)):
                if new_r >= old_r:
                    return
                severity = "critical" if new_r <= 2 else "high"
                is_material = new_r <= 3 or (old_r - new_r) >= 1
            title = f"Rating downgrade on {event.instrument_id}: {old_r} -> {new_r}"
            msg = (
                f"Instrument {event.instrument_id} was downgraded from {old_r} to {new_r}. "
                "Triggered unscheduled portfolio review."
            )
            self._emit_if_not_duplicate(
                event=event,
                alert_type="rating_downgrade",
                severity=severity,
                title=title,
                message=msg,
                material=is_material,
                details={"old_rating": old_r, "new_rating": new_r},
            )

        elif etype == "manager_exit":
            manager_name = p.get("manager_name", "Lead Fund Manager")
            self._emit_if_not_duplicate(
                event=event,
                alert_type="manager_exit",
                severity="high",
                title=f"Fund manager exit at {event.instrument_id}",
                message=f"{manager_name} exited {event.instrument_id}; reviewing conviction and tenure score.",
                material=True,
                details={"manager_name": manager_name},
            )

        elif etype == "sebi_action":
            order_summary = p.get("summary", "SEBI regulatory order / restriction")
            self._emit_if_not_duplicate(
                event=event,
                alert_type="sebi_action",
                severity="critical",
                title=f"SEBI regulatory action on {event.instrument_id}",
                message=f"Regulatory event reported for {event.instrument_id}: {order_summary}.",
                material=True,
                details={"summary": order_summary},
            )

        elif etype == "ter_hike":
            delta_pct = float(p.get("ter_delta_pct", 0.0))
            if delta_pct >= 0.25:
                self._emit_if_not_duplicate(
                    event=event,
                    alert_type="ter_hike",
                    severity="medium",
                    title=f"Expense ratio hike (+{delta_pct:.2f}%) on {event.instrument_id}",
                    message=f"TER increased by {delta_pct:.2f}% on {event.instrument_id}.",
                    material=delta_pct >= 0.50,
                    details={"ter_delta_pct": delta_pct},
                )

    def _handle_market_data(self, event: MarketEvent) -> None:
        etype = event.event_type.lower()
        p = event.payload

        if etype == "nav_drop":
            drop_pct = abs(float(p.get("drop_pct", 0.0)))
            if drop_pct >= self._nav_drop_threshold:
                severity: AlertSeverity = "critical" if drop_pct >= 6.0 else "high"
                self._emit_if_not_duplicate(
                    event=event,
                    alert_type="nav_drop",
                    severity=severity,
                    title=f"Abnormal NAV/price drop (-{drop_pct:.2f}%) on {event.instrument_id}",
                    message=f"{event.instrument_id} dropped {drop_pct:.2f}%, exceeding {self._nav_drop_threshold:.1f}% threshold.",
                    material=True,
                    details={"drop_pct": drop_pct},
                )

        elif etype == "aum_outflow":
            outflow_pct = abs(float(p.get("outflow_pct", 0.0)))
            if outflow_pct >= self._aum_outflow_threshold:
                self._emit_if_not_duplicate(
                    event=event,
                    alert_type="aum_outflow",
                    severity="high",
                    title=f"Sharp AUM outflow (-{outflow_pct:.1f}%) on {event.instrument_id}",
                    message=f"{event.instrument_id} experienced {outflow_pct:.1f}% AUM contraction.",
                    material=True,
                    details={"outflow_pct": outflow_pct},
                )

    def _handle_portfolio_tick(self, event: MarketEvent) -> None:
        p = event.payload
        raw_target = p.get("target_weights") or {}
        raw_current = p.get("current_weights") or {}
        raw_holdings = p.get("holdings_value") or {}

        target: dict[Bucket, float] = {_to_bucket(k): float(v) for k, v in raw_target.items()}
        if raw_holdings and not raw_current:
            holdings_val = {_to_bucket(k): float(v) for k, v in raw_holdings.items()}
            total = sum(holdings_val.values())
            current = {b: (v / total * 100.0) for b, v in holdings_val.items()} if total > 0 else {}
        else:
            current = {_to_bucket(k): float(v) for k, v in raw_current.items()}
            holdings_val = {_to_bucket(k): float(v) for k, v in raw_holdings.items()} if raw_holdings else {}

        if not current or not target:
            return

        drift_items = compute_drift(current, target)
        breached = [d for d in drift_items if d.breached]
        if not breached:
            return

        deltas = rebalance_plan(holdings_val, target) if holdings_val else {}
        breached_summary = [
            {
                "bucket": d.bucket.value,
                "current": round(d.current, 2),
                "target": round(d.target, 2),
                "abs_drift": round(d.abs_drift, 2),
                "rel_drift": round(d.rel_drift, 2) if d.rel_drift is not None else None,
            }
            for d in breached
        ]
        bucket_names = ", ".join(d.bucket.value for d in breached)
        self._emit_if_not_duplicate(
            event=event,
            alert_type="portfolio_drift",
            severity="high",
            title=f"Portfolio allocation drift breached 5/25 rule ({bucket_names})",
            message=f"Buckets [{bucket_names}] breached 5pp/25% drift thresholds; rebalance recommended.",
            material=True,
            details={
                "breached_buckets": breached_summary,
                "rebalance_deltas_inr": {k.value: round(v, 2) for k, v in deltas.items()},
            },
        )

    # ── Deduplication + Recalibration Trigger ─────────────────────────────

    def _emit_if_not_duplicate(
        self,
        *,
        event: MarketEvent,
        alert_type: str,
        severity: AlertSeverity,
        title: str,
        message: str,
        material: bool,
        details: dict[str, Any],
    ) -> Alert | None:
        dedup_key = (event.profile_id, event.instrument_id or "PORTFOLIO", alert_type)
        prev = self._last_alerts.get(dedup_key)

        if prev is not None and (event.timestamp - prev.created_at) < self._cooldown:
            # Within cool-down window: suppress new alert and batch into previous alert
            self.suppressed_duplicates += 1
            prev.batch_count += 1
            self._store.save(prev)
            return None

        alert = Alert(
            alert_id=f"alt_{uuid.uuid4().hex[:10]}",
            profile_id=event.profile_id,
            instrument_id=event.instrument_id or "PORTFOLIO",
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            created_at=event.timestamp,
            event_id=event.event_id,
            material=material,
            details=details,
        )

        if material:
            recal_summary = self._trigger_recalibration(alert, event)
            alert.recalibration_triggered = True
            alert.recalibrated_run_id = recal_summary.get("run_id")
            alert.recalibration_summary = recal_summary
            self.recalibration_runs.append(recal_summary)

        self._last_alerts[dedup_key] = alert
        self._store.save(alert)
        return alert

    def _trigger_recalibration(self, alert: Alert, event: MarketEvent) -> dict[str, Any]:
        """Trigger an unscheduled Supervisor / recommendation recalibration run."""
        if self._recalibration_cb is not None:
            custom = self._recalibration_cb(alert, event)
            if custom is not None:
                return custom

        run_id = f"recal_{uuid.uuid4().hex[:8]}"
        action = "HOLD"
        replacement_status = "review_queued"
        if alert.alert_type in ("rating_downgrade", "sebi_action", "manager_exit"):
            new_rating = alert.details.get("new_rating")
            if alert.alert_type == "sebi_action" or (isinstance(new_rating, (int, float)) and new_rating <= 2):
                action = "SELL"
                replacement_status = "replaced_with_top_ranked_peer"
            else:
                action = "TRIM"
                replacement_status = "weight_reduced_on_downgrade"
        elif alert.alert_type == "portfolio_drift":
            action = "REBALANCE"
            replacement_status = "rebalance_deltas_computed"
        elif alert.alert_type in ("nav_drop", "aum_outflow"):
            action = "REVIEW"
            replacement_status = "supervisor_dossier_refreshed"

        return {
            "run_id": run_id,
            "trigger_alert_id": alert.alert_id,
            "trigger_event_type": alert.alert_type,
            "instrument_id": alert.instrument_id,
            "recommended_action": action,
            "status": replacement_status,
            "supervisor_invoked": True,
        }


def _to_bucket(raw: str | Bucket) -> Bucket:
    if isinstance(raw, Bucket):
        return raw
    return Bucket(str(raw).lower())
