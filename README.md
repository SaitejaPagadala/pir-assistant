# PIR Assistant (unofficial)

A small RAG chatbot that answers questions about India's **Public Insurance Registry (PIR)** from
published public sources - the IRDAI consultation paper first, then IRDAI / IIB pages and related law.
Every answer is grounded in retrieved passages and carries a citation (section, paragraph, page).

> **Disclaimer.** Personal, experimental project. Not affiliated with, endorsed by, or speaking for IRDAI,
> IIB or any employer. Uses **public documents only**. Not legal, financial or regulatory advice. The
> consultation paper is a *proposal*; final regulations may differ. Always check the cited source.

## How it works

```
sources.yaml ──► scraper/fetch.py ──► data/raw/<id>/<timestamp>.pdf|html     (git-ignored)
                     │  robots.txt, rate limit, content hash -> data/manifest.jsonl (change history)
                     ▼
             pipeline/build_index.py
                 parse (PyMuPDF / trafilatura) ─► structure-aware chunking ─► embeddings (bge-small, local)
                     ▼
             data/processed/  chunks.jsonl · stories.json · questions.json · glossary.json
                              sources_index.json · history.json · embeddings.npy        (committed)
                     ▼
   rag/retriever.py  hybrid BM25 + dense search, fused with reciprocal-rank fusion
   rag/tools.py      search_sources · get_story · list_stories · get_question · define_term · list_sources
   rag/agent.py      Claude tool-use loop (Haiku by default), answers only from tool results, cites
                     ▼
   app/streamlit_app.py   chat UI, source viewer, password gate, per-session & daily caps
```

Why not plain vector search? The paper has 59 numbered user stories, 14 consultation questions, a glossary
and tables. Counting/listing those is a lookup problem, so they are parsed into structured records and
exposed as tools. Each chunk also knows its section, paragraph, story/cohort and page.

## Quick start (local)

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                      # then put your ANTHROPIC_API_KEY in .env

# 1. Put the consultation paper PDF at data/manual/PIR - Public Consultation Paper.pdf
#    (download it yourself from the IRDAI / IIB site)
# 2. Fetch web sources (polite, robots-aware) and build the index
python -m scraper.fetch
python -m pipeline.build_index          # add --no-embed for keyword-only search

# 3. Run the app
streamlit run app/streamlit_app.py
```

Some government sites block bots. If `scraper.fetch` reports a 403/robots refusal, download the file
by hand, drop it in `data/manual/`, and add it to `sources.yaml` with `manual: true`.

## Adding sources

Edit `sources.yaml` (one entry per document: id, title, publisher, type `pdf|html`, `url` or `file`).
Re-run fetch + build. Only add **public, published** material. For news, keep link + your own summary
rather than copying full articles.

## Tracking how PIR evolves

`data/manifest.jsonl` appends a record (timestamp + SHA-256) only when a source's content changes.
The weekly GitHub Action (`.github/workflows/refresh.yml`) re-fetches, rebuilds, and commits changes, so
git history + the manifest form a timeline. Planned: version-to-version diffs surfaced in the app.

## Deploy (free): Streamlit Community Cloud

1. Push this repo to GitHub (commit `data/processed/`, **not** `.env` or `.streamlit/secrets.toml`).
2. share.streamlit.io -> *New app* -> pick the repo, branch `main`, main file `app/streamlit_app.py`.
3. *Advanced settings -> Secrets*, paste (values are yours):
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   APP_PASSWORD = "a-passphrase"
   MAX_QUESTIONS_PER_SESSION = 30
   MAX_QUESTIONS_PER_DAY = 200
   ```
4. Deploy. Open the URL on desktop or phone.

### Cost controls
- Default model is Haiku (~$0.002 per question at current list prices); *Deep* mode uses Sonnet (~$0.04).
- Set a **monthly spend limit** in the Anthropic Console - the hard stop.
- Retrieval is capped (<=10 passages), answers <=900 tokens, history <=6 messages, <=5 tool steps.
- Optional password gate + per-session and per-day question caps (see `.env.example`).

## Evaluation

`eval/golden.yaml` holds questions with expected facts. `python -m eval.run_eval` runs them against the
live agent and prints pass/fail and cost. Run it after changing prompts, chunking or sources.
Unit tests for the chunker: `python -m pytest -q tests`.

## Known limitations
- Tables (value exchange, repositories, prioritisation) are chunked by size, not row-by-row yet.
- Exhibits (diagrams) are images; text inside them is only captured if the PDF has a text layer.
- Printed page numbers are assumed to equal PDF page numbers (`page_offset` in `sources.yaml` adjusts it).
- Dense search needs `fastembed` (downloads a ~100 MB model on first run); otherwise keyword search only.

## Roadmap
1. Row-level table chunking; exhibit descriptions via vision.
2. `what_changed` tool: diff between stored versions of a document.
3. More sources: IRDAI circulars/press releases, IIB FAQs, DPDP Act, final PIR regulations.
4. Separate private deployment for personal notes / meeting intelligence (never mixed with this public app).

## Layout
```
scraper/    fetch.py parse.py config.py          pipeline/  chunk.py build_index.py
rag/        retriever.py tools.py agent.py       app/       streamlit_app.py
eval/       golden.yaml run_eval.py              tests/     test_chunk.py
data/       manual/ raw/ processed/ manifest.jsonl
.github/    workflows/refresh.yml                sources.yaml  requirements.txt
```
