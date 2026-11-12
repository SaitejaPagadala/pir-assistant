"""Polite fetcher for the sources listed in sources.yaml.

- honours robots.txt, identifies itself, rate-limits requests
- saves raw bytes under data/raw/<source_id>/<UTC timestamp>.<ext>
- records a content hash in data/manifest.jsonl; a new line is written ONLY when
  the content changed, which is what powers the "what changed / how did PIR evolve" feature
- sources marked `manual: true` are read from data/manual/ instead of the network

Usage:
    python -m scraper.fetch                 # all enabled sources
    python -m scraper.fetch --only iib-pir-portal
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

import httpx

from .config import MANIFEST, RAW, ROOT, load_config


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _last_hash(source_id: str) -> str | None:
    if not MANIFEST.exists():
        return None
    last = None
    with open(MANIFEST, "r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("source_id") == source_id:
                last = rec.get("sha256")
    return last


def _append_manifest(rec: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _robots_allows(url: str, ua: str, cache: dict) -> bool:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    if base not in cache:
        rp = robotparser.RobotFileParser()
        rp.set_url(base + "/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None  # robots.txt unreachable -> treat as allowed, but stay polite
        cache[base] = rp
    rp = cache[base]
    return True if rp is None else rp.can_fetch(ua, url)


def _ext_for(content_type: str, src_type: str) -> str:
    ct = (content_type or "").lower()
    if "pdf" in ct or src_type == "pdf":
        return "pdf"
    return "html"


def _record(src: dict, data: bytes, ext: str, status: str) -> dict | None:
    """Save raw bytes if content is new. Returns the manifest record or None if unchanged."""
    digest = _sha256(data)
    if digest == _last_hash(src["id"]):
        print(f"  = unchanged ({src['id']})")
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = RAW / src["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{ts}.{ext}"
    out.write_bytes(data)
    rec = {
        "source_id": src["id"],
        "title": src.get("title"),
        "publisher": src.get("publisher"),
        "url": src.get("url"),
        "file": str(out.relative_to(ROOT)).replace("\\", "/"),
        "sha256": digest,
        "bytes": len(data),
        "fetched_at": ts,
        "status": status,
    }
    _append_manifest(rec)
    print(f"  + saved {rec['file']} ({len(data)} bytes)")
    return rec


def fetch_manual(src: dict) -> dict | None:
    path = ROOT / src["file"]
    if not path.exists():
        print(f"  ! manual file missing: {src['file']} -- download it and drop it there")
        return None
    return _record(src, path.read_bytes(), path.suffix.lstrip(".") or "pdf", "manual")


def fetch_url(src: dict, client: httpx.Client, ua: str, robots_cache: dict) -> dict | None:
    url = src["url"]
    if not _robots_allows(url, ua, robots_cache):
        print(f"  ! robots.txt disallows {url} -- skipping (download manually if you are allowed to)")
        return None
    try:
        resp = client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        print(f"  ! request failed: {e}")
        return None
    if resp.status_code != 200:
        print(f"  ! HTTP {resp.status_code} for {url} -- site may block bots; try manual download")
        return None
    return _record(src, resp.content, _ext_for(resp.headers.get("content-type", ""), src.get("type", "html")), "ok")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="fetch a single source id")
    args = ap.parse_args()

    cfg = load_config()
    ua = cfg.get("user_agent", "pir-assistant")
    robots_cache: dict = {}
    changed = 0
    with httpx.Client(headers={"User-Agent": ua}, timeout=cfg["timeout_seconds"]) as client:
        for src in cfg["sources"]:
            if not src.get("enabled", True):
                continue
            if args.only and src["id"] != args.only:
                continue
            print(f"[{src['id']}]")
            rec = fetch_manual(src) if src.get("manual") else fetch_url(src, client, ua, robots_cache)
            if rec:
                changed += 1
            if not src.get("manual"):
                time.sleep(cfg["delay_seconds"])
    print(f"done. new/changed documents: {changed}")


if __name__ == "__main__":
    main()
