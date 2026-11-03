"""Télécharge des ticks Dukascopy (bi5) et construit des bougies H1 (bid).

Usage : python tools/dukascopy_fetch.py SYMBOL DEBUT(YYYY-MM-DD) FIN(YYYY-MM-DD) [workers]
Sortie : data_dukascopy/SYMBOL_H1.csv (UTC). Cache des heures dans data_dukascopy/cache/SYMBOL/
"""
import lzma
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data_dukascopy"
BASE = "https://datafeed.dukascopy.com/datafeed/{sym}/{y:04d}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"

# nom Dukascopy, diviseur de prix (nombre de décimales)
SYMBOLS = {
    "EURUSD": ("EURUSD", 100000),
    "US500": ("USA500IDXUSD", 1000),
    "US100": ("USATECHIDXUSD", 1000),
    "US30": ("USA30IDXUSD", 1000),
    "XAUUSD": ("XAUUSD", 1000),
    "XAGUSD": ("XAGUSD", 1000),
    "USOIL": ("LIGHTCMDUSD", 1000),
}

sess = requests.Session()
sess.headers["User-Agent"] = "Mozilla/5.0"


def fetch_hour(sym_dk, point, t):
    url = BASE.format(sym=sym_dk, y=t.year, m=t.month - 1, d=t.day, h=t.hour)
    for attempt in range(6):
        try:
            r = sess.get(url, timeout=30)
        except requests.RequestException:
            time.sleep(2 + attempt)
            continue
        if r.status_code == 200:
            if not r.content:
                return None
            raw = lzma.decompress(r.content)
            ticks = [struct.unpack(">IIIff", raw[i:i + 20]) for i in range(0, len(raw), 20)]
            if not ticks:
                return None
            bids = [b / point for _, _, b, _, _ in ticks]
            return (t, bids[0], max(bids), min(bids), bids[-1], len(bids))
        if r.status_code in (404,):
            return None
        time.sleep(2 + attempt * 2)  # 429 / 503 : on attend
    return "FAIL"


def run(sym, start, end, workers=12):
    sym_dk, point = SYMBOLS[sym]
    cache = OUT / "cache" / sym
    cache.mkdir(parents=True, exist_ok=True)
    hours = []
    t = start
    while t < end:
        if t.weekday() < 5 or (t.weekday() == 6):  # ignore samedi
            hours.append(t)
        t += timedelta(hours=1)
    rows, fails = [], 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, res in enumerate(ex.map(lambda h: fetch_hour(sym_dk, point, h), hours), 1):
            if res == "FAIL":
                fails += 1
            elif res is not None:
                rows.append(res)
            if i % 2000 == 0:
                print(f"{sym} {i}/{len(hours)} {time.time()-t0:.0f}s fails={fails}", flush=True)
    df_rows = [dict(time=r[0].strftime("%Y-%m-%d %H:%M:%S"), open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5]) for r in rows]
    import pandas as pd
    df = pd.DataFrame(df_rows)
    if not df.empty:
        df = df.sort_values("time").drop_duplicates("time")
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / f"{sym}_H1.csv", index=False)
    print(f"{sym}: {len(df)} heures, échecs={fails}, durée {time.time()-t0:.0f}s -> {OUT / (sym + '_H1.csv')}", flush=True)


def run_to(sym, start, end, path, workers=3):
    """Variante : écrit directement dans un fichier donné."""
    import pandas as pd
    sym_dk, point = SYMBOLS[sym]
    hours = [t for t in (start + timedelta(hours=k) for k in range(int((end - start).total_seconds() // 3600)))
             if t.weekday() < 5 or t.weekday() == 6]
    rows, fails = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(lambda h: fetch_hour(sym_dk, point, h), hours):
            if res == "FAIL":
                fails += 1
            elif res is not None:
                rows.append(dict(time=res[0].strftime("%Y-%m-%d %H:%M:%S"), open=res[1], high=res[2],
                                 low=res[3], close=res[4], volume=res[5]))
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    print(f"  {sym} {start:%Y-%m}: {len(df)} heures, échecs={fails}", flush=True)
    return fails


if __name__ == "__main__":
    s = sys.argv[1]
    a = datetime.strptime(sys.argv[2], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    b = datetime.strptime(sys.argv[3], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    w = int(sys.argv[4]) if len(sys.argv) > 4 else 12
    run(s, a, b, w)
