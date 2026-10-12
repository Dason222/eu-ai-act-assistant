"""Shared persistent daily request and estimated LLM-spend caps.

Two containers (Streamlit and FastAPI) use the same SQLite file on a shared
volume. BEGIN IMMEDIATE serializes reservations so concurrent requests
cannot race beyond the global limit. Estimated USD is a conservative
reservation, not provider billing, and intentionally is not refunded.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from aiact_qa.config import Settings


class BudgetExceeded(ValueError):
    """Request not admitted due to request-count or estimated-spend limits."""


class UsageBudget:
    def __init__(self, path: Path, settings: Settings) -> None:
        self.path = Path(path)
        self.settings = settings

    def consume(self, actor: str, billable: bool) -> None:
        identity = hashlib.sha256((actor or "anonymous").encode()).hexdigest()
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        reserve = self.settings.reserved_cost_per_llm_request_usd if billable else 0.0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS usage_budget "
                "(day TEXT NOT NULL, actor TEXT NOT NULL, requests INTEGER NOT NULL, "
                "reserved_usd REAL NOT NULL, PRIMARY KEY(day, actor))"
            )
            db.execute("DELETE FROM usage_budget WHERE day < ?", (day,))
            rows = {}
            for key in ("__global__", identity):
                row = db.execute(
                    "SELECT requests, reserved_usd FROM usage_budget WHERE day=? AND actor=?",
                    (day, key),
                ).fetchone()
                rows[key] = row if row else (0, 0.0)
            global_count, global_cost = rows["__global__"]
            actor_count, _ = rows[identity]
            if global_count >= self.settings.daily_request_limit:
                raise BudgetExceeded("Daily request quota reached; try again tomorrow (UTC).")
            if actor_count >= self.settings.daily_actor_request_limit:
                raise BudgetExceeded("Session request quota reached; try again tomorrow (UTC).")
            if billable and global_cost + reserve > self.settings.daily_budget_usd + 1e-9:
                raise BudgetExceeded("Daily estimated LLM budget reached; try again tomorrow (UTC).")
            for key in ("__global__", identity):
                db.execute(
                    "INSERT INTO usage_budget(day, actor, requests, reserved_usd) VALUES (?,?,?,?) "
                    "ON CONFLICT(day, actor) DO UPDATE SET "
                    "requests=requests+1, reserved_usd=reserved_usd+excluded.reserved_usd",
                    (day, key, 1, reserve),
                )
            db.commit()
