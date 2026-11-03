"""Collecte d'actualités horodatées (sources gratuites, sans clé).

- GDELT DOC 2.0 : articles des 3 derniers mois (limite 1 requête / 5 s, respectée).
- Google News RSS : flux récent par requête (pas d'historique long).

Sortie : data_news/ (ignoré par Git). Usage : python tools/news_collect.py
"""
import csv
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data_news"
QUERIES = {
    "fed_rates": "Federal Reserve interest rates",
    "inflation": "US inflation CPI",
    "trump_tariffs": "Trump tariffs",
    "iran_war": "Iran war",
    "oil": "oil prices crude",
    "stocks": "stock market S&P 500 Nasdaq",
    "gold": "gold price",
}
UA = {"User-Agent": "Mozilla/5.0 (research)"}


def gdelt(name, q):
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?query=" + quote_plus(q) +
           "&mode=artlist&format=json&maxrecords=250&timespan=3months&sort=datedesc")
    for attempt in range(5):
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code == 200 and r.text.strip().startswith("{"):
            arts = r.json().get("articles", [])
            with open(OUT / f"gdelt_{name}.json", "w") as f:
                json.dump(arts, f, ensure_ascii=False)
            return len(arts)
        time.sleep(7 + attempt * 5)
    return -1


def gnews(name, q):
    url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en"
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    rows = []
    for it in root.iter("item"):
        pub = it.findtext("pubDate", "")
        try:
            ts = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
        except ValueError:
            ts = None
        rows.append(dict(time_utc=ts.isoformat() if ts else pub, title=it.findtext("title", ""),
                         source=(it.find("source").text if it.find("source") is not None else ""),
                         link=it.findtext("link", "")))
    with open(OUT / f"gnews_{name}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["time_utc", "title", "source", "link"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, q in QUERIES.items():
        g = gdelt(name, q)
        time.sleep(6)
        n = gnews(name, q)
        print(f"{name:14s} GDELT={g:4d}  GoogleNews={n:4d}", flush=True)
        time.sleep(6)


if __name__ == "__main__":
    main()
