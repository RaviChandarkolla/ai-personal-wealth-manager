"""M9 append-only, hash-chained audit log and versioned profile store (SQLite)."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from pydantic import BaseModel

from ..models.core import UserFinancialProfile

GENESIS = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ts TEXT NOT NULL,
    agent TEXT NOT NULL, step TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
    payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TABLE IF NOT EXISTS profile (
    profile_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (profile_id, version)
);
CREATE TRIGGER IF NOT EXISTS profile_no_update BEFORE UPDATE ON profile
BEGIN SELECT RAISE(ABORT, 'profile rows are immutable; add a new version'); END;
"""


class AuditEntry(BaseModel):
    seq: int
    run_id: str
    ts: str
    agent: str
    step: str
    model: str
    prompt_version: str
    payload: dict
    prev_hash: str
    hash: str


def _digest(prev: str, run_id: str, ts: str, agent: str, step: str, model: str, pv: str,
            payload: str) -> str:
    blob = json.dumps([prev, run_id, ts, agent, step, model, pv, payload])
    return hashlib.sha256(blob.encode()).hexdigest()


def sha256_json(obj: object) -> str:
    """Deterministic SHA-256 hex digest of a JSON-serializable object."""
    blob = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()



class AuditLog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        conn.executescript(_SCHEMA)

    def append(self, run_id: str, agent: str, step: str, payload: dict, model: str = "",
               prompt_version: str = "") -> str:
        body = json.dumps(payload, sort_keys=True, default=str)
        ts = datetime.now(UTC).isoformat()
        row = self.conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else GENESIS
        h = _digest(prev, run_id, ts, agent, step, model, prompt_version, body)
        with self.conn:
            self.conn.execute(
                "INSERT INTO audit_log (run_id, ts, agent, step, model, prompt_version, payload,"
                " prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, ts, agent, step, model, prompt_version, body, prev, h),
            )
        return h

    def entries(self, run_id: str | None = None) -> list[AuditEntry]:
        sql = "SELECT * FROM audit_log" + (" WHERE run_id = ?" if run_id else "") + " ORDER BY seq"
        rows = self.conn.execute(sql, (run_id,) if run_id else ()).fetchall()
        return [AuditEntry(**{**dict(r), "payload": json.loads(r["payload"])}) for r in rows]

    def run_ids(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT run_id FROM audit_log GROUP BY run_id ORDER BY MIN(seq)"
        ).fetchall()
        return [r["run_id"] for r in rows]

    def verify(self) -> tuple[bool, int | None]:
        """Recompute the chain; returns (ok, first bad seq)."""
        prev = GENESIS
        for r in self.conn.execute("SELECT * FROM audit_log ORDER BY seq").fetchall():
            expect = _digest(prev, r["run_id"], r["ts"], r["agent"], r["step"], r["model"],
                             r["prompt_version"], r["payload"])
            if r["prev_hash"] != prev or r["hash"] != expect:
                return False, r["seq"]
            prev = r["hash"]
        return True, None


class ProfileStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        conn.executescript(_SCHEMA)

    def save(self, profile: UserFinancialProfile) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO profile VALUES (?,?,?)",
                (profile.profile_id, profile.version, profile.model_dump_json()),
            )

    def get(self, profile_id: str, version: int) -> UserFinancialProfile | None:
        row = self.conn.execute(
            "SELECT body FROM profile WHERE profile_id = ? AND version = ?", (profile_id, version)
        ).fetchone()
        return UserFinancialProfile.model_validate_json(row["body"]) if row else None

    def latest(self, profile_id: str) -> UserFinancialProfile | None:
        row = self.conn.execute(
            "SELECT body FROM profile WHERE profile_id = ? ORDER BY version DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        return UserFinancialProfile.model_validate_json(row["body"]) if row else None

