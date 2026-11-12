"""Parse raw documents -> chunks -> embeddings, written to data/processed/.

Outputs (committed to git so the hosted app needs no build step):
  chunks.jsonl      one JSON chunk per line (text + metadata + cite string)
  stories.json      the 59 user stories (number, title, cohort, pages, chunk ids)
  questions.json    consultation questions
  glossary.json     term -> expansion
  sources_index.json  what is indexed, from which version/fetch, how many chunks
  embeddings.npy    float32 [n_chunks, dim]  (skipped with --no-embed -> keyword search only)
  index_meta.json   embedding model name, build time

If a source's raw file is not available (e.g. a manual PDF that is not in the repo), its
chunks from the previous build are kept, so a CI refresh never silently drops documents.

Usage:
    python -m pipeline.build_index
    python -m pipeline.build_index --no-embed
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import numpy as np

from scraper.config import MANIFEST, PROCESSED, ROOT, load_config
from scraper.parse import parse_html, parse_pdf

from .chunk import chunk_generic_html, chunk_generic_pdf, chunk_pir_paper, context_prefix

EMBED_MODEL = "BAAI/bge-small-en-v1.5"


def latest_manifest() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if MANIFEST.exists():
        with open(MANIFEST, "r", encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                out[rec["source_id"]] = rec
    return out


def load_previous():
    prev = {"chunks": [], "stories": [], "questions": [], "glossary": {}}
    p = PROCESSED / "chunks.jsonl"
    if p.exists():
        prev["chunks"] = [json.loads(l) for l in open(p, encoding="utf-8")]
    for name in ("stories", "questions", "glossary"):
        f = PROCESSED / f"{name}.json"
        if f.exists():
            prev[name] = json.load(open(f, encoding="utf-8"))
    return prev


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-embed", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    manifest = latest_manifest()
    prev = load_previous()
    PROCESSED.mkdir(parents=True, exist_ok=True)

    chunks, stories, questions, glossary, sources_index = [], [], [], {}, []
    for src in cfg["sources"]:
        if not src.get("enabled", True):
            continue
        sid = src["id"]
        rec = manifest.get(sid)
        raw_path = (ROOT / rec["file"]) if rec else None
        if not raw_path or not raw_path.exists():
            kept = [c for c in prev["chunks"] if c["source_id"] == sid]
            if kept:
                print(f"[{sid}] raw file not available - keeping {len(kept)} chunks from previous build")
                chunks += kept
                if sid == "pir-consultation-paper":
                    stories, questions, glossary = prev["stories"], prev["questions"], prev["glossary"]
                sources_index.append(_index_row(src, rec, len(kept)))
            else:
                print(f"[{sid}] no raw file and nothing previously built - skipped (run scraper.fetch first)")
            continue

        print(f"[{sid}] parsing {raw_path.name}")
        meta = dict(src)
        if raw_path.suffix == ".pdf":
            pages = parse_pdf(raw_path, int(src.get("page_offset", 0)))
            if src.get("chunker") == "pir_paper":
                c, s, q, g = chunk_pir_paper(pages, meta)
                stories, questions, glossary = s, q, g
            else:
                c = chunk_generic_pdf(pages, meta)
        else:
            parsed = parse_html(raw_path)
            c = chunk_generic_html(parsed["text"], meta)
        print(f"    -> {len(c)} chunks")
        chunks += c
        sources_index.append(_index_row(src, rec, len(c)))

    with open(PROCESSED / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    json.dump(stories, open(PROCESSED / "stories.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(questions, open(PROCESSED / "questions.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(glossary, open(PROCESSED / "glossary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(sources_index, open(PROCESSED / "sources_index.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"chunks={len(chunks)} stories={len(stories)} questions={len(questions)} glossary={len(glossary)}")

    # fetch history for "what changed" questions
    history = []
    if MANIFEST.exists():
        history = [json.loads(l) for l in open(MANIFEST, encoding="utf-8")]
    json.dump(history, open(PROCESSED / "history.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    if args.no_embed or not chunks:
        print("skipping embeddings (keyword search only)")
        return
    from fastembed import TextEmbedding

    print(f"embedding {len(chunks)} chunks with {EMBED_MODEL} ...")
    model = TextEmbedding(model_name=EMBED_MODEL)
    texts = [f"{context_prefix(c)}\n{c['text']}" for c in chunks]
    vecs = np.array(list(model.embed(texts)), dtype="float32")
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
    np.save(PROCESSED / "embeddings.npy", vecs)
    json.dump(
        {"model": EMBED_MODEL, "dim": int(vecs.shape[1]), "n": int(vecs.shape[0]),
         "built_at": datetime.now(timezone.utc).isoformat()},
        open(PROCESSED / "index_meta.json", "w"),
    )
    print("done")


def _index_row(src: dict, rec: dict | None, n: int) -> dict:
    return {
        "source_id": src["id"],
        "title": src.get("title"),
        "publisher": src.get("publisher"),
        "doc_type": src.get("doc_type"),
        "url": src.get("url"),
        "version": src.get("version"),
        "doc_date": src.get("doc_date"),
        "fetched_at": (rec or {}).get("fetched_at"),
        "sha256": (rec or {}).get("sha256"),
        "n_chunks": n,
    }


if __name__ == "__main__":
    main()
