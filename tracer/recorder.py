"""Recorder — SQLite trace，写盘前强制 redact（Stage 5）。"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from tracer.redactor import redact

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, test_case TEXT, app_version TEXT, app_build TEXT,
    source_commit TEXT, start_time TEXT, end_time TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, step_index INTEGER,
    action_type TEXT, locator TEXT, status TEXT, error TEXT,
    latency_ms INTEGER, screenshot_path TEXT, ui_tree_path TEXT);
CREATE TABLE IF NOT EXISTS recoveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, step_id INTEGER, strategy TEXT,
    llm_target TEXT, confidence REAL, risk_level TEXT, latency_ms INTEGER,
    accepted BOOLEAN);
CREATE TABLE IF NOT EXISTS infra_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, event_type TEXT, timestamp TEXT);
"""


class Recorder:
    def __init__(self, db_path: str | Path = "out/trace.db"):
        Path(db_path).parent.mkdir(exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)

    def start_run(self, test_case: str) -> str:
        run_id = str(uuid.uuid4())[:8]
        self.conn.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
            (run_id, test_case, "debug", "local", _git_commit(),
             _now(), None, "RUNNING"),
        )
        self.conn.commit()
        return run_id

    def end_run(self, run_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE runs SET end_time=?, status=? WHERE run_id=?", (_now(), status, run_id)
        )
        self.conn.commit()

    def record_step(self, run_id: str, step_index: int, action_type: str,
                    locator: dict | None = None, status: str = "SUCCESS",
                    error: str | None = None, latency_ms: int = 0,
                    screenshot_path: str | None = None) -> int:
        """locator 先脱敏再落盘（硬约束：Recorder 内无先写后脱敏路径）。"""
        cur = self.conn.execute(
            "INSERT INTO steps (run_id, step_index, action_type, locator, status,"
            " error, latency_ms, screenshot_path) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, step_index, action_type,
             json.dumps(redact(locator)) if locator else None,
             status, error, latency_ms, screenshot_path),
        )
        self.conn.commit()
        return cur.lastrowid

    def record_recovery(self, step_id: int, strategy: str, llm_target: str | None,
                        confidence: float, risk_level: str, latency_ms: int,
                        accepted: bool) -> None:
        self.conn.execute(
            "INSERT INTO recoveries VALUES (NULL,?,?,?,?,?,?,?)",
            (step_id, strategy, llm_target, confidence, risk_level, latency_ms, accepted),
        )
        self.conn.commit()

    def record_infra(self, run_id: str, event_type: str) -> None:
        self.conn.execute(
            "INSERT INTO infra_events VALUES (NULL,?,?,?)", (run_id, event_type, _now())
        )
        self.conn.commit()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git_commit() -> str:
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unknown"
