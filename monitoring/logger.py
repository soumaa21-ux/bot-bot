"""Journal structuré (JSON Lines) : signaux, décisions du risk engine, ordres, erreurs."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def get_logger(name: str = "bot") -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    log = logging.getLogger(name)
    if not log.handlers:
        log.setLevel(logging.INFO)
        h = logging.FileHandler(LOG_DIR / "bot.jsonl", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(h)
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(sh)
    return log


def jlog(log: logging.Logger, level: str, kind: str, **payload) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
    getattr(log, level.lower(), log.info)(json.dumps(rec, default=str, ensure_ascii=False))
