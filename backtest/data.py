"""Chargement et normalisation des données historiques.

Formats acceptés :
  - export MT5 (séparateur tabulation ou virgule) : <DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> [<SPREAD>]
  - CSV avec une colonne temps (time / datetime / date) et open, high, low, close (+ spread optionnel)
Les données sont converties en UTC naïf, triées, dédupliquées. Le spread CSV est en points MT5
(= tick_size du symbole) ; s'il est absent, le coût par défaut des settings est utilisé.
"""
import pandas as pd


def _read(path: str) -> pd.DataFrame:
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.readline()
    sep = "\t" if head.count("\t") > head.count(",") else ","
    if head.count(";") > max(head.count(sep), 0):
        sep = ";"
    df = pd.read_csv(path, sep=sep)
    df.columns = [str(c).strip().lower().strip("<>") for c in df.columns]
    return df


def normalize_ohlc(df: pd.DataFrame, point: float | None = None) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower().strip("<>") for c in df.columns]
    if "date" in df.columns and "time" in df.columns and df["time"].astype(str).str.contains(":").any():
        t = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce", utc=True)
    else:
        col = next((c for c in ("time", "datetime", "date") if c in df.columns), None)
        if col is None:
            raise ValueError("Colonne temps introuvable (time / datetime / date)")
        t = pd.to_datetime(df[col], errors="coerce", utc=True)
    df.index = pd.DatetimeIndex(t).tz_convert("UTC").tz_localize(None)
    df = df[~df.index.isna()]
    for c in ("open", "high", "low", "close"):
        if c not in df.columns:
            raise ValueError(f"Colonne manquante : {c}")
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    cols = ["open", "high", "low", "close"]
    if "spread" in df.columns:
        sp = pd.to_numeric(df["spread"], errors="coerce").fillna(0.0)
        if point:
            sp = sp * point
        df["spread"] = sp
        cols.append("spread")
    return df[cols]


def load_csv(path: str, point: float | None = None) -> pd.DataFrame:
    return normalize_ohlc(_read(path), point=point)


def resample(df: pd.DataFrame, rule: str = "15min") -> pd.DataFrame:
    """Agrège des bougies plus fines (ex. M1 -> M15). Bougies vides retirées."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "spread" in df.columns:
        agg["spread"] = "mean"
    out = df.resample(rule, label="left", closed="left").agg(agg)
    return out.dropna(subset=["open", "close"])
