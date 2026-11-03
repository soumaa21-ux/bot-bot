"""Alertes Telegram (optionnel). Sans variables d'environnement, ce module ne fait rien."""
import json
import os
import urllib.parse
import urllib.request


def notify(message: str) -> bool:
    token = os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": message[:4000]}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception:
        return False
