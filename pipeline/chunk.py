"""Structure-aware chunking.

`chunk_pir_paper` understands the layout of the IRDAI PIR consultation paper:
  * "Section N - Title" headings, numbered paragraphs inside each section
  * Section 8: 59 user stories ("13: Insurers identify ...") grouped under "8.x User Cohort n"
  * Section 15: numbered consultation questions grouped A-F
  * Section 17: glossary (term -> expansion), returned separately and NOT embedded
`chunk_generic_*` handles everything else (other PDFs, web pages) with size-based windows.

Every chunk carries a ready-made `cite` string so answers can reference it precisely.
"""
from __future__ import annotations

import re
from collections import Counter

MAX_CHARS = 2200        # paragraphs longer than this are split
STORY_MAX = 3800
WINDOW = 1100           # generic window size
OVERLAP = 150

DASH = "[\u2013\u2014-]"
HEAD_RE = re.compile(rf"^Section\s+(\d{{1,2}})\s*{DASH}\s*(\S.*)$")
COHORT_RE = re.compile(r"^8\.(\d)\s+User Cohort\s+(\d)\s*:\s*(.*)$")
STORY_RE = re.compile(r"^(\d{1,2})\s*:\s+(\S.*)$")
NUMPARA_RE = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")
SUBITEM_RE = re.compile(r"^\(?([a-i])\)\s+")
GROUP_RE = re.compile(r"^([A-F])\.\s+(\S.*)$")
SUBSEC_RE = re.compile(r"^7\.(\d)\.?\s+(\S.*)$")
NEEDS_RE = re.compile(r"^Users[\u2019']\s*Needs\s*$")
NEWLINE_BEFORE_RE = re.compile(r"^(\(?[a-i]\)|[\u2022\-\u2013]|\d+\))\s")

THEMES = {
    "consent": [r"consent", r"revocab"],
    "privacy_data_protection": [r"privacy", r"dpdp", r"data protection", r"personal data", r"anonymi", r"tokeni", r"minimi[sz]"],
    "identity": [r"identity", r"\bkyc\b", r"aadhaar", r"\bpan\b", r"functional identifier"],
    "governance": [r"governance", r"\bboard\b", r"oversight", r"mandate", r"neutrality"],
    "legal": [r"\bact\b", r"regulations?\b", r"sbsr", r"statut"],
    "data_standards": [r"standard", r"interoperab", r"legacy", r"mapping layer"],
    "grievance": [r"grievance", r"complaint", r"ombudsman", r"bima bharosa"],
    "claims": [r"\bclaims?\b", r"settlement"],
    "underwriting_risk": [r"underwrit", r"risk score", r"pricing", r"anomal", r"fraud"],
    "intermediaries": [r"intermediar", r"\bagents?\b", r"posp", r"\bbrokers?\b"],
    "financial_inclusion": [r"inclusion", r"underserved", r"protection gap"],
    "implementation": [r"phase", r"implementation", r"transition", r"readiness"],
}


# ----------------------------------------------------------------------------- helpers
def tag_themes(text: str, top: int = 4) -> list[str]:
    low = text.lower()
    counts = Counter()
    for theme, pats in THEMES.items():
        c = sum(len(re.findall(p, low)) for p in pats)
        if c:
            counts[theme] = c
    return [t for t, _ in counts.most_common(top)]


def _clean_title(t: str) -> str:
    t = t.strip()
    return re.sub(r"(?<=[A-Za-z])\d$", "", t)  # drop footnote marker glued to a heading


def join_lines(lines: list[tuple[int, str]]) -> str:
    out: list[str] = []
    for _, s in lines:
        if out and NEWLINE_BEFORE_RE.match(s):
            out.append("\n" + s)
        else:
            out.append(s)
    text = " ".join(out)
    return re.sub(r" ?\n ?", "\n", text).strip()


def split_long(text: str, max_chars: int = WINDOW, overlap: int = OVERLAP) -> list[str]:
    """Split on sentence boundaries into windows of ~max_chars with a small overlap."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.;:?!])\s+|\n", text)
    pieces, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) + 1 > max_chars:
            pieces.append(cur.strip())
            cur = cur[-overlap:] + " " + s if overlap else s
        else:
            cur = (cur + " " + s).strip()
    if cur.strip():
        pieces.append(cur.strip())
    return pieces


def flatten(pages: list[dict]) -> list[tuple[int, str]]:
    """pages -> [(printed_page, line)], dropping page numbers, blanks and TOC dot-leader lines."""
    lines: list[tuple[int, str]] = []
    for p in pages:
        raw = [l.rstrip() for l in p["text"].splitlines()]
        non_empty = [i for i, l in enumerate(raw) if l.strip()]
        drop = set()
        for idx in ([non_empty[0], non_empty[-1]] if non_empty else []):
            tok = raw[idx].strip()
            if tok.isdigit() and int(tok) in (p["page"], p["pdf_page"]):
                drop.add(idx)
        for i, l in enumerate(raw):
            s = l.strip()
            if i in drop or not s or re.search(r"\.{5,}", s):
                continue
            lines.append((p["page"], s))
    return lines


def _pages(lines) -> tuple[int, int]:
    return lines[0][0], lines[-1][0]


def _page_str(a: int, b: int) -> str:
    return f"p.{a}" if a == b else f"p.{a}-{b}"


def walk_numbered(lines, heading_res=()):
    """Split a section into numbered paragraphs (strictly sequential 1,2,3...)."""
    pre, paras = [], []
    cur, expected, tag = None, 1, None
    for page, s in lines:
        hit = False
        for hr, kind in heading_res:
            hm = hr.match(s)
            if hm:
                tag, hit = (kind, hm.groups()), True
                break
        if hit:
            continue
        m = NUMPARA_RE.match(s)
        if m and int(m.group(1)) == expected:
            cur = {"n": expected, "lines": [(page, m.group(2))], "tag": tag}
            paras.append(cur)
            expected += 1
            continue
        (pre if cur is None else cur["lines"]).append((page, s))
    return pre, paras


def _split_subitems(lines) -> list[list[tuple[int, str]]]:
    """Break a long paragraph at a)/b)/c) sub-items, then merge neighbours up to ~1500 chars."""
    pieces: list[list] = [[]]
    for ln in lines:
        if SUBITEM_RE.match(ln[1]) and pieces[-1]:
            pieces.append([])
        pieces[-1].append(ln)
    merged: list[list] = []
    for p in pieces:
        if merged and len(join_lines(merged[-1])) + len(join_lines(p)) < 1500:
            merged[-1].extend(p)
        else:
            merged.append(list(p))
    return merged


# ----------------------------------------------------------------------------- PIR paper
def chunk_pir_paper(pages: list[dict], meta: dict):
    """Returns (chunks, stories, questions, glossary)."""
    sid = meta["id"]
    short = meta.get("short") or f"PIR Consultation Paper ({meta.get('version', '')})"
    base = {
        "source_id": sid,
        "source_title": meta.get("title"),
        "publisher": meta.get("publisher"),
        "doc_type": meta.get("doc_type"),
        "version": meta.get("version"),
        "doc_date": meta.get("doc_date"),
        "visibility": "public",
        "url": meta.get("url"),
    }
    chunks: list[dict] = []
    stories: list[dict] = []
    questions: list[dict] = []
    glossary: dict[str, str] = {}

    def add(cid, kind, text, lines, cite_core, **extra):
        a, b = _pages(lines)
        c = dict(base)
        c.update(
            {
                "id": f"{sid}:{cid}",
                "kind": kind,
                "text": text,
                "page_start": a,
                "page_end": b,
                "cite": f"{short}, {cite_core} ({_page_str(a, b)})",
                "themes": tag_themes(text),
            }
        )
        c.update(extra)
        chunks.append(c)
        return c

    # --- split into sections (skip cover + index: start at the first real "Section 1 -" heading)
    sections, cur, expect = [], None, 1
    for page, s in flatten(pages):
        m = HEAD_RE.match(s)
        if m and int(m.group(1)) == expect:
            cur = {"num": expect, "title": _clean_title(m.group(2)), "lines": []}
            sections.append(cur)
            expect += 1
            continue
        if cur is not None:
            cur["lines"].append((page, s))

    for sec in sections:
        n, title, lines = sec["num"], sec["title"], sec["lines"]
        if not lines:
            continue
        common = {"section": n, "section_title": title}

        # ---------------- Section 8: user stories
        if n == 8:
            pre, cohort, cur_story, last = [], None, None, 0
            i = 0
            while i < len(lines):
                page, s = lines[i]
                cm = COHORT_RE.match(s)
                if cm:
                    name = cm.group(3).strip()
                    # heading may wrap onto the next line (unbalanced parenthesis)
                    if name.count("(") > name.count(")") and i + 1 < len(lines):
                        name = f"{name} {lines[i + 1][1].strip()}"
                        i += 1
                    cohort = {"no": int(cm.group(2)), "name": name}
                    cur_story = None
                    i += 1
                    continue
                sm = STORY_RE.match(s)
                if sm and int(sm.group(1)) == last + 1:
                    tl, j = [sm.group(2)], i + 1
                    while j < len(lines) and not NEEDS_RE.match(lines[j][1]) and len(tl) < 3:
                        tl.append(lines[j][1])
                        j += 1
                    last += 1
                    cur_story = {"no": last, "title": " ".join(tl), "cohort": cohort, "lines": [(page, "")]}
                    stories.append(cur_story)
                    i = j
                    continue
                if cur_story is not None:
                    cur_story["lines"].append((page, s))
                elif cohort is None:
                    pre.append((page, s))
                i += 1
            if pre:
                add("s8:intro", "paragraph", join_lines(pre), pre, "Section 8 intro", **common, label="intro")
            for st in stories:
                body_lines = [l for l in st["lines"] if l[1]]
                body = join_lines(body_lines)
                coh = st["cohort"] or {"no": None, "name": ""}
                head = f"Story {st['no']}: {st['title']}\nCohort {coh['no']}: {coh['name']}\n"
                parts = split_long(body, STORY_MAX, 200) if len(body) > STORY_MAX else [body]
                for k, part in enumerate(parts, 1):
                    suffix = f" part {k}" if len(parts) > 1 else ""
                    ch = add(
                        f"story{st['no']}" + (f":{k}" if len(parts) > 1 else ""),
                        "story",
                        head + part,
                        body_lines,
                        f"Story {st['no']}{suffix}",
                        **common,
                        label=f"story {st['no']}",
                        story_no=st["no"],
                        cohort_no=coh["no"],
                        cohort=coh["name"],
                    )
                    st.setdefault("chunk_ids", []).append(ch["id"])
                st["page_start"], st["page_end"] = _pages(body_lines)
            continue

        # ---------------- Section 17: glossary
        if n == 17:
            blob = "\n".join(s for _, s in lines)
            for m in re.finditer(rf"(?:^|\n)(\d{{1,2}})\.\s+(.+?)\s+{DASH}\s+(.+?)(?=\n\d{{1,2}}\.\s|\Z)", blob, re.S):
                glossary[re.sub(r"\s+", " ", m.group(2)).strip()] = re.sub(r"\s+", " ", m.group(3)).strip()
            continue

        # ---------------- Section 15: consultation questions (grouped A-F)
        if n == 15:
            pre, paras = walk_numbered(lines, heading_res=[(GROUP_RE, "group")])
            if pre:
                add("s15:intro", "paragraph", join_lines(pre), pre, "Section 15 intro", **common, label="intro")
            for p in paras:
                grp = p["tag"][1] if p["tag"] else ("", "")
                text = join_lines(p["lines"])
                ch = add(
                    f"q{p['n']}",
                    "question",
                    f"Consultation question {p['n']} (group {grp[0]}: {grp[1]})\n{text}",
                    p["lines"],
                    f"Consultation Question {p['n']}",
                    **common,
                    label=f"Q{p['n']}",
                    question_no=p["n"],
                    group=f"{grp[0]}. {grp[1]}".strip(". "),
                )
                questions.append(
                    {"question_no": p["n"], "group": ch["group"], "chunk_id": ch["id"],
                     "page_start": ch["page_start"], "page_end": ch["page_end"]}
                )
            continue

        # ---------------- all other sections: numbered paragraphs
        heading_res = [(SUBSEC_RE, "sub")] if n == 7 else []
        pre, paras = walk_numbered(lines, heading_res)
        if pre:
            add(f"s{n}:pre", "paragraph", join_lines(pre), pre, f"\u00a7{n} {title}", **common, label="intro")
        for p in paras:
            sub = p["tag"][1][1] if p["tag"] else None
            text = join_lines(p["lines"])
            if len(text) <= MAX_CHARS:
                groups = [p["lines"]]
            else:
                groups = _split_subitems(p["lines"])
                if len(groups) == 1:  # no sub-items (e.g. a table) -> size windows
                    texts = split_long(text, WINDOW, OVERLAP)
                    groups = None
            if groups is not None:
                for k, g in enumerate(groups, 1):
                    part = f":{k}" if len(groups) > 1 else ""
                    add(
                        f"s{n}:p{p['n']}{part}",
                        "paragraph",
                        join_lines(g),
                        g,
                        f"\u00a7{n} \u00b6{p['n']}" + (f" part {k}" if len(groups) > 1 else ""),
                        **common,
                        label=f"\u00b6{p['n']}",
                        para=p["n"],
                        subsection=sub,
                    )
            else:
                for k, t in enumerate(texts, 1):
                    add(
                        f"s{n}:p{p['n']}:{k}",
                        "table_or_long",
                        t,
                        p["lines"],
                        f"\u00a7{n} \u00b6{p['n']} part {k}",
                        **common,
                        label=f"\u00b6{p['n']}",
                        para=p["n"],
                        subsection=sub,
                    )

    story_meta = [
        {
            "story_no": s["no"],
            "title": s["title"],
            "cohort_no": (s["cohort"] or {}).get("no"),
            "cohort": (s["cohort"] or {}).get("name"),
            "page_start": s.get("page_start"),
            "page_end": s.get("page_end"),
            "chunk_ids": s.get("chunk_ids", []),
        }
        for s in stories
    ]
    return chunks, story_meta, questions, glossary


# ----------------------------------------------------------------------------- generic
def _generic(paras: list[tuple[int, str]], meta: dict, short: str):
    """paras: [(page, paragraph_text)] -> size-window chunks."""
    sid = meta["id"]
    base = {
        "source_id": sid,
        "source_title": meta.get("title"),
        "publisher": meta.get("publisher"),
        "doc_type": meta.get("doc_type"),
        "version": meta.get("version"),
        "doc_date": meta.get("doc_date"),
        "visibility": "public",
        "url": meta.get("url"),
        "kind": "generic",
    }
    chunks, buf, buf_pages, k = [], "", [], 0

    def flush():
        nonlocal buf, buf_pages, k
        if not buf.strip():
            return
        for piece in split_long(buf, WINDOW, OVERLAP):
            k += 1
            a, b = (min(buf_pages), max(buf_pages)) if buf_pages else (None, None)
            c = dict(base)
            c.update(
                {
                    "id": f"{sid}:g{k}",
                    "text": piece,
                    "label": f"chunk {k}",
                    "page_start": a,
                    "page_end": b,
                    "cite": f"{short}" + (f" ({_page_str(a, b)})" if a else f", chunk {k}"),
                    "themes": tag_themes(piece),
                }
            )
            chunks.append(c)
        buf, buf_pages = "", []

    for page, text in paras:
        text = text.strip()
        if not text:
            continue
        if buf and len(buf) + len(text) > WINDOW:
            flush()
        buf = (buf + "\n" + text).strip()
        if page:
            buf_pages.append(page)
    flush()
    return chunks


def chunk_generic_pdf(pages: list[dict], meta: dict):
    short = meta.get("short") or meta.get("title") or meta["id"]
    paras = []
    for p in pages:
        merged = re.sub(r"\n(?!\n)", " ", p["text"])
        for para in re.split(r"\n\s*\n", merged):
            paras.append((p["page"], para))
    return _generic(paras, meta, short)


def chunk_generic_html(text: str, meta: dict):
    short = meta.get("short") or meta.get("title") or meta["id"]
    paras = [(None, t) for t in text.splitlines() if t.strip()]
    return _generic(paras, meta, short)


def context_prefix(c: dict) -> str:
    """Text prepended to a chunk before embedding so that each chunk 'knows' where it came from."""
    bits = [c.get("source_title") or c["source_id"]]
    if c.get("section_title"):
        bits.append(f"Section {c.get('section')} {c['section_title']}")
    if c.get("cohort"):
        bits.append(f"Cohort: {c['cohort']}")
    return " | ".join(bits)
