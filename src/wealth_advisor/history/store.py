"""SQLite storage for past advice runs and grounded follow-up chat threads."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _format_inr(amount: float) -> str:
    n = int(round(amount))
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) <= 3:
        return f"{sign}₹{s}"
    last3 = s[-3:]
    rest = s[:-3]
    groups: list[str] = []
    while len(rest) > 2:
        groups.append(rest[-2:])
        rest = rest[:-2]
    if rest:
        groups.append(rest)
    groups.reverse()
    return f"{sign}₹{','.join(groups)},{last3}"


def build_run_title(
    goal_type: str,
    amount: float,
    horizon_years: float,
    query: str | None = None,
) -> str:
    """Build a short human-readable title from query or goal/amount/horizon."""
    q = (query or "").strip()
    if q:
        return q[:60] + ("…" if len(q) > 60 else "")
    g = (goal_type or "Goal").replace("_", " ").strip().title()
    hz = int(horizon_years) if float(horizon_years).is_integer() else round(float(horizon_years), 1)
    return f"{g} · {_format_inr(amount)} · {hz}y"


class HistoryStore:
    """Persistence layer for saved advice runs and per-run follow-up messages."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS advice_history (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                goal_type TEXT NOT NULL,
                horizon_years REAL NOT NULL,
                risk_preference TEXT NOT NULL,
                amount REAL NOT NULL,
                risk_category TEXT NOT NULL,
                risk_score REAL NOT NULL,
                rule_set TEXT NOT NULL,
                data_mode TEXT NOT NULL,
                n_recommendations INTEGER NOT NULL DEFAULT 0,
                request_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL,
                context_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_advice_history_created
                ON advice_history(created_at DESC);

            CREATE TABLE IF NOT EXISTS followup_message (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                referenced_funds_json TEXT NOT NULL DEFAULT '[]',
                sources_json TEXT NOT NULL DEFAULT '[]',
                FOREIGN KEY (run_id) REFERENCES advice_history(run_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_followup_run
                ON followup_message(run_id, id ASC);
            """
        )

    def save(
        self,
        run_id: str,
        title: str | None = None,
        request_dict: dict[str, Any] | None = None,
        response_dict: dict[str, Any] | None = None,
        context_dict: dict[str, Any] | None = None,
        data_mode: str = "synthetic",
        *,
        query: str | None = None,
        created_at: str | None = None,
    ) -> None:
        req = request_dict or {}
        resp = response_dict or {}
        ctx = context_dict or {}
        ts = created_at or datetime.now(timezone.utc).isoformat()
        goal = resp.get("goal") if isinstance(resp.get("goal"), dict) else req
        goal_type = str(
            goal.get("goal_type")
            or req.get("purpose")
            or req.get("goal_type")
            or "wealth_creation"
        )
        horizon_years = float(
            goal.get("horizon_years") or req.get("horizon_years") or 0.0
        )
        risk_preference = str(
            goal.get("risk_preference") or req.get("risk_preference") or "Moderate"
        )
        amount = float(goal.get("amount") or req.get("amount") or 0.0)
        profile_dict = (
            resp.get("profile") if isinstance(resp.get("profile"), dict) else {}
        )
        risk_category = str(
            resp.get("risk_category")
            or profile_dict.get("category")
            or ctx.get("risk_category")
            or risk_preference
        )
        risk_score = float(
            resp.get("risk_score")
            if resp.get("risk_score") is not None
            else (profile_dict.get("risk_score") or 0.0)
        )
        rule_set = str(
            resp.get("rule_set")
            or ctx.get("rule_set")
            or req.get("rule_set")
            or "set2"
        )
        effective_mode = str(resp.get("data_mode") or data_mode or "synthetic")
        effective_query = str(query if query is not None else (req.get("query") or ""))
        recs = resp.get("recommendations")
        if not isinstance(recs, list):
            recs = ctx.get("recommendations") or []
        n_recommendations = len(recs)
        effective_title = title or build_run_title(
            goal_type, amount, horizon_years, query=effective_query
        )

        self.conn.execute("PRAGMA foreign_keys = ON")
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO advice_history (
                    run_id, created_at, title, query, goal_type, horizon_years,
                    risk_preference, amount, risk_category, risk_score,
                    rule_set, data_mode, n_recommendations,
                    request_json, response_json, context_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    ts,
                    effective_title,
                    effective_query,
                    goal_type,
                    horizon_years,
                    risk_preference,
                    amount,
                    risk_category,
                    risk_score,
                    rule_set,
                    effective_mode,
                    n_recommendations,
                    json.dumps(req, sort_keys=True, default=str),
                    json.dumps(resp, sort_keys=True, default=str),
                    json.dumps(ctx, sort_keys=True, default=str),
                ),
            )

    def list(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
                h.run_id,
                h.created_at,
                h.title,
                h.query,
                h.goal_type,
                h.horizon_years,
                h.risk_preference,
                h.amount,
                h.risk_category,
                h.risk_score,
                h.rule_set,
                h.data_mode,
                h.n_recommendations,
                COUNT(m.id) AS message_count
            FROM advice_history h
            LEFT JOIN followup_message m ON m.run_id = h.run_id
            GROUP BY h.run_id
            ORDER BY h.created_at DESC, h.rowid DESC
            LIMIT ? OFFSET ?
            """,
            (int(limit), int(offset)),
        ).fetchall()
        return [
            {
                "run_id": r["run_id"],
                "created_at": r["created_at"],
                "title": r["title"],
                "query": r["query"],
                "goal_type": r["goal_type"],
                "horizon_years": float(r["horizon_years"]),
                "risk_preference": r["risk_preference"],
                "amount": float(r["amount"]),
                "risk_category": r["risk_category"],
                "risk_score": float(r["risk_score"]),
                "rule_set": r["rule_set"],
                "data_mode": r["data_mode"],
                "n_recommendations": int(r["n_recommendations"]),
                "message_count": int(r["message_count"]),
                "followup_count": int(r["message_count"]),
            }
            for r in rows
        ]

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM advice_history WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            return None
        msgs = self.messages(run_id)
        return {
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "title": row["title"],
            "query": row["query"],
            "goal_type": row["goal_type"],
            "horizon_years": float(row["horizon_years"]),
            "risk_preference": row["risk_preference"],
            "amount": float(row["amount"]),
            "risk_category": row["risk_category"],
            "risk_score": float(row["risk_score"]),
            "rule_set": row["rule_set"],
            "data_mode": row["data_mode"],
            "n_recommendations": int(row["n_recommendations"]),
            "request": json.loads(row["request_json"]),
            "response": json.loads(row["response_json"]),
            "context": json.loads(row["context_json"]),
            "messages": msgs,
            "message_count": len(msgs),
            "followup_count": len(msgs),
        }

    def delete(self, run_id: str) -> bool:
        self.conn.execute("PRAGMA foreign_keys = ON")
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM advice_history WHERE run_id = ?",
                (run_id,),
            )
        return cur.rowcount > 0

    def add_message(
        self,
        run_id: str,
        role: str,
        content: str,
        referenced_funds: list[dict[str, Any]] | None = None,
        sources: list[dict[str, Any]] | None = None,
        *,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        self.conn.execute("PRAGMA foreign_keys = ON")
        ts = created_at or datetime.now(timezone.utc).isoformat()
        refs = referenced_funds or []
        srcs = sources or []
        payload = {"referenced_funds": refs, "sources": srcs}
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO followup_message (
                    run_id, created_at, role, content, payload_json,
                    referenced_funds_json, sources_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    ts,
                    role,
                    content,
                    json.dumps(payload, sort_keys=True, default=str),
                    json.dumps(refs, sort_keys=True, default=str),
                    json.dumps(srcs, sort_keys=True, default=str),
                ),
            )
            msg_id = int(cur.lastrowid)
        return {
            "id": msg_id,
            "run_id": run_id,
            "created_at": ts,
            "role": role,
            "content": content,
            "referenced_funds": refs,
            "sources": srcs,
            "payload": payload,
        }

    def messages(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, run_id, created_at, role, content, payload_json,
                   referenced_funds_json, sources_json
            FROM followup_message
            WHERE run_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
            refs = json.loads(r["referenced_funds_json"]) if r["referenced_funds_json"] else payload.get("referenced_funds", [])
            srcs = json.loads(r["sources_json"]) if r["sources_json"] else payload.get("sources", [])
            out.append(
                {
                    "id": int(r["id"]),
                    "run_id": r["run_id"],
                    "created_at": r["created_at"],
                    "role": r["role"],
                    "content": r["content"],
                    "referenced_funds": refs,
                    "sources": srcs,
                    "payload": payload,
                }
            )
        return out
