"""Chargement des fichiers de configuration (YAML)."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_settings() -> dict:
    return _load("settings.yaml")


def load_rules() -> dict:
    return _load("rules_firm.yaml")


def load_symbols() -> dict:
    return _load("symbols.yaml")
