"""Shared paths and source-config loading."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
MANUAL = DATA / "manual"
PROCESSED = DATA / "processed"
MANIFEST = DATA / "manifest.jsonl"


def load_config() -> dict:
    with open(ROOT / "sources.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("delay_seconds", 3)
    cfg.setdefault("timeout_seconds", 40)
    cfg.setdefault("sources", [])
    return cfg
