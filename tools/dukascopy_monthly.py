"""Téléchargement Dukascopy H1 par mois, priorité aux 2 dernières années.

Chaque mois est sauvegardé dans data_dukascopy/chunks/SYMBOLE/AAAA-MM.csv ; un mois déjà
présent n'est pas retéléchargé (reprise possible après interruption).

Usage : python tools/dukascopy_monthly.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dukascopy_fetch as d  # noqa: E402

PRIORITY = ["US500", "US100", "US30", "XAUUSD"]
START = datetime(2024, 11, 1, tzinfo=timezone.utc)   # début de la fenêtre prioritaire
END = datetime(2026, 11, 4, tzinfo=timezone.utc)
EXTRA_START = datetime(2021, 11, 1, tzinfo=timezone.utc)  # extension ensuite, si on veut


def months(a, b):
    out = []
    cur = a.replace(day=1)
    while cur < b:
        nxt = (cur.replace(day=28) + pd.Timedelta(days=4)).replace(day=1)
        out.append((cur, min(nxt, b)))
        cur = nxt
    return out


def fetch_month(sym, a, b, workers=3):
    d.OUT.mkdir(parents=True, exist_ok=True)
    chunk_dir = d.OUT / "chunks" / sym
    chunk_dir.mkdir(parents=True, exist_ok=True)
    f = chunk_dir / f"{a:%Y-%m}.csv"
    if f.exists():
        return
    d.run_to(sym, a, b, f, workers)


def parse_month(txt):
    return datetime.strptime(txt, "%Y-%m").replace(tzinfo=timezone.utc)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 3:  # SYMBOLE DEBUT(AAAA-MM) FIN(AAAA-MM, exclu)
        sym, a, b = argv[0], parse_month(argv[1]), parse_month(argv[2])
        for ma, mb in months(a, b):
            fetch_month(sym, ma, mb)
            print("OK", sym, f"{ma:%Y-%m}", flush=True)
        print("TERMINE", sym, flush=True)
        return
    for sym in PRIORITY:
        for a, b in reversed(months(START, END)):  # du plus récent au plus ancien
            fetch_month(sym, a, b)
            print("OK", sym, f"{a:%Y-%m}", flush=True)
    print("TERMINE", flush=True)


if __name__ == "__main__":
    main()
