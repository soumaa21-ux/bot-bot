"""État persistant (SQLite) : baseline journalière, plus haut solde, verrous, journal d'événements.

Le bot redémarre sans perdre la baseline du drawdown journalier.
"""
import json
import sqlite3
from datetime import datetime, timezone


class StateStore:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts TEXT, level TEXT, kind TEXT, payload TEXT)")
        self.conn.commit()

    def get(self, key: str, default=None):
        row = self.conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (key, json.dumps(value)))
        self.conn.commit()

    def log_event(self, level: str, kind: str, payload: dict | None = None, ts: datetime | None = None) -> None:
        ts = ts or datetime.now(timezone.utc)
        self.conn.execute("INSERT INTO events (ts, level, kind, payload) VALUES (?, ?, ?, ?)",
                          (ts.isoformat(), level, kind, json.dumps(payload or {}, default=str)))
        self.conn.commit()

    def events(self, kind: str | None = None) -> list[tuple]:
        if kind:
            return self.conn.execute("SELECT ts, level, kind, payload FROM events WHERE kind=? ORDER BY id",
                                     (kind,)).fetchall()
        return self.conn.execute("SELECT ts, level, kind, payload FROM events ORDER BY id").fetchall()

    def close(self) -> None:
        self.conn.close()
