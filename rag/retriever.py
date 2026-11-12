"""Hybrid retrieval over data/processed: BM25 keyword + dense embeddings, fused with RRF.
Falls back to BM25-only if embeddings or fastembed are unavailable."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"
STOP = set("a an the of and or to in on for with is are was were be by as at from that this it its what which who how do does can".split())


def tokenize(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in STOP]


class BM25:
    """Tiny dependency-free Okapi BM25."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        from collections import Counter
        from math import log

        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = np.array([len(d) for d in docs], dtype="float32")
        self.avg = float(self.len.mean()) if len(docs) else 1.0
        df: Counter = Counter()
        for t in self.tf:
            df.update(t.keys())
        n = len(docs)
        self.idf = {w: log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}

    def get_scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(len(self.tf), dtype="float32")
        for w in set(query):
            idf = self.idf.get(w)
            if idf is None:
                continue
            for i, t in enumerate(self.tf):
                f = t.get(w)
                if f:
                    out[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return out


class Retriever:
    def __init__(self, processed: Path = PROCESSED):
        self.dir = processed
        self.chunks: list[dict] = []
        p = processed / "chunks.jsonl"
        if p.exists():
            self.chunks = [json.loads(l) for l in open(p, encoding="utf-8")]
        self.by_id = {c["id"]: c for c in self.chunks}
        self.stories = self._load("stories.json", [])
        self.questions = self._load("questions.json", [])
        self.glossary = self._load("glossary.json", {})
        self.sources = self._load("sources_index.json", [])
        self.history = self._load("history.json", [])
        self.bm25 = None
        if self.chunks:
            self.bm25 = BM25([tokenize(c["text"] + " " + (c.get("source_title") or "")) for c in self.chunks])
        self.emb = None
        self._embedder = None
        ef = processed / "embeddings.npy"
        if ef.exists() and self.chunks:
            arr = np.load(ef)
            if arr.shape[0] == len(self.chunks):
                self.emb = arr

    def _load(self, name, default):
        f = self.dir / name
        return json.load(open(f, encoding="utf-8")) if f.exists() else default

    @property
    def ready(self) -> bool:
        return bool(self.chunks)

    def _embed_query(self, q: str):
        if self._embedder is None:
            from fastembed import TextEmbedding

            meta = self._load("index_meta.json", {})
            self._embedder = TextEmbedding(model_name=meta.get("model", "BAAI/bge-small-en-v1.5"))
        try:
            v = next(iter(self._embedder.query_embed([q])))
        except Exception:
            v = next(iter(self._embedder.embed([q])))
        v = np.asarray(v, dtype="float32")
        return v / (np.linalg.norm(v) + 1e-9)

    def search(self, query: str, k: int = 6, source_id: str | None = None, kind: str | None = None,
               section: int | None = None) -> list[dict]:
        if not self.ready:
            return []
        mask = np.ones(len(self.chunks), dtype=bool)
        for i, c in enumerate(self.chunks):
            if (source_id and c["source_id"] != source_id) or (kind and c.get("kind") != kind) or \
               (section and c.get("section") != section):
                mask[i] = False
        if not mask.any():
            return []

        rankings = []
        bm = np.array(self.bm25.get_scores(tokenize(query)))
        bm[~mask] = -1
        rankings.append(np.argsort(-bm)[: 50])
        if self.emb is not None:
            try:
                qv = self._embed_query(query)
                sims = self.emb @ qv
                sims[~mask] = -9
                rankings.append(np.argsort(-sims)[: 50])
            except Exception:
                pass  # dense search unavailable -> keyword only
        scores: dict[int, float] = {}
        for r in rankings:
            for rank, idx in enumerate(r):
                if mask[idx]:
                    scores[int(idx)] = scores.get(int(idx), 0.0) + 1.0 / (60 + rank)
        top = sorted(scores, key=scores.get, reverse=True)[:k]
        return [self.chunks[i] for i in top]
