"""Tools the Claude agent can call. All read-only."""
from __future__ import annotations

import re

from .retriever import Retriever

_R: Retriever | None = None


def retriever() -> Retriever:
    global _R
    if _R is None:
        _R = Retriever()
    return _R


def _snip(c: dict, limit: int = 1500) -> dict:
    t = c["text"]
    return {
        "cite": c["cite"],
        "source": c.get("source_title"),
        "pages": [c.get("page_start"), c.get("page_end")],
        "url": c.get("url"),
        "text": t if len(t) <= limit else t[:limit] + " ...[truncated]",
    }


TOOL_SCHEMAS = [
    {
        "name": "search_sources",
        "description": "Hybrid keyword+semantic search over all indexed public PIR/IRDAI/IIB sources. Use for any factual question. "
                       "Returns passages with a 'cite' string to quote in the answer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "description": "passages to return (default 6, max 10)"},
                "source_id": {"type": "string", "description": "optional: restrict to one source (see list_sources)"},
                "section": {"type": "integer", "description": "optional: consultation-paper section number"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_story",
        "description": "Full text of a numbered user story (1-59) from Section 8 of the consultation paper.",
        "input_schema": {"type": "object", "properties": {"story_no": {"type": "integer"}}, "required": ["story_no"]},
    },
    {
        "name": "list_stories",
        "description": "List user stories (number, title, cohort), optionally filtered by cohort number (1-8) or title keyword. "
                       "Use this for counting or enumerating stories.",
        "input_schema": {
            "type": "object",
            "properties": {"cohort_no": {"type": "integer"}, "keyword": {"type": "string"}},
        },
    },
    {
        "name": "get_question",
        "description": "Text of a numbered consultation question (Section 15). Omit question_no to list all.",
        "input_schema": {"type": "object", "properties": {"question_no": {"type": "integer"}}},
    },
    {
        "name": "define_term",
        "description": "Expand an acronym / look up a glossary term from the paper (e.g. 'MII', 'IRS', 'CKYC').",
        "input_schema": {"type": "object", "properties": {"term": {"type": "string"}}, "required": ["term"]},
    },
    {
        "name": "list_sources",
        "description": "What is indexed: source ids, titles, versions, fetch dates. Also the fetch history of each source "
                       "(use it to say when a document was last captured or changed).",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def run_tool(name: str, args: dict) -> dict:
    r = retriever()
    if not r.ready:
        return {"error": "The knowledge base is empty. The owner needs to run the scraper and build the index."}
    try:
        if name == "search_sources":
            k = max(1, min(int(args.get("k", 6)), 10))
            hits = r.search(args["query"], k=k, source_id=args.get("source_id"), section=args.get("section"))
            return {"results": [_snip(c) for c in hits]} if hits else {"results": [], "note": "nothing relevant found"}
        if name == "get_story":
            no = int(args["story_no"])
            st = next((s for s in r.stories if s["story_no"] == no), None)
            if not st:
                return {"error": f"story {no} not found (valid: 1-{len(r.stories)})"}
            return {"story": [_snip(r.by_id[cid], 4000) for cid in st["chunk_ids"] if cid in r.by_id]}
        if name == "list_stories":
            items = r.stories
            if args.get("cohort_no"):
                items = [s for s in items if s["cohort_no"] == int(args["cohort_no"])]
            kw = (args.get("keyword") or "").lower()
            if kw:
                items = [s for s in items if kw in s["title"].lower()]
            return {"count": len(items), "stories": [
                {"story_no": s["story_no"], "title": s["title"], "cohort_no": s["cohort_no"],
                 "cohort": s["cohort"], "pages": [s["page_start"], s["page_end"]]} for s in items]}
        if name == "get_question":
            if not args.get("question_no"):
                return {"count": len(r.questions), "questions": [
                    {"question_no": q["question_no"], "group": q["group"]} for q in r.questions]}
            q = next((q for q in r.questions if q["question_no"] == int(args["question_no"])), None)
            if not q:
                return {"error": "question not found"}
            return {"question": _snip(r.by_id[q["chunk_id"]])}
        if name == "define_term":
            t = args["term"].strip().lower()
            for k, v in r.glossary.items():
                if t == k.lower() or t in [x.strip().lower() for x in k.split("/")]:
                    return {"term": k, "expansion": v}
            near = [f"{k} = {v}" for k, v in r.glossary.items() if t in k.lower() or t in v.lower()]
            return {"matches": near[:5]} if near else {"note": "term not in the glossary"}
        if name == "list_sources":
            return {"sources": r.sources, "fetch_history": [
                {k: h.get(k) for k in ("source_id", "fetched_at", "sha256", "status")} for h in r.history]}
    except Exception as e:  # never crash the chat loop on a tool error
        return {"error": f"{type(e).__name__}: {e}"}
    return {"error": f"unknown tool {name}"}
