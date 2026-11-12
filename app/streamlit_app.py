"""PIR Assistant - Streamlit UI (works on desktop and mobile).

Run locally:   streamlit run app/streamlit_app.py
"""
import os
import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

from rag.agent import answer  # noqa: E402
from rag.tools import retriever  # noqa: E402

st.set_page_config(page_title="PIR Assistant (unofficial)", page_icon="\U0001F4D8", layout="centered")


def secret(name: str, default=None):
    try:
        return st.secrets[name]
    except Exception:
        return os.getenv(name, default)


API_KEY = secret("ANTHROPIC_API_KEY")
APP_PASSWORD = secret("APP_PASSWORD")
MAX_SESSION = int(secret("MAX_QUESTIONS_PER_SESSION", 30))
MAX_DAY = int(secret("MAX_QUESTIONS_PER_DAY", 200))
FAST = secret("PIR_MODEL", "claude-haiku-5-5")
DEEP = secret("PIR_DEEP_MODEL", "claude-sonnet-5-5")


@st.cache_resource
def daily_counter() -> dict:
    return {}  # {iso_date: count}; resets whenever the app restarts - a coarse global safety net


# ------------------------------------------------------------------ password gate
if APP_PASSWORD and not st.session_state.get("authed"):
    st.title("PIR Assistant")
    pw = st.text_input("Passphrase", type="password")
    if pw:
        if pw == APP_PASSWORD:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Wrong passphrase.")
    st.stop()

R = retriever()
st.session_state.setdefault("messages", [])
st.session_state.setdefault("n_asked", 0)
st.session_state.setdefault("spent", 0.0)

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Settings")
    mode = st.radio("Answer quality", ["Fast (cheap)", "Deep (slower, costlier)"], index=0)
    st.caption(f"Questions this session: {st.session_state['n_asked']}/{MAX_SESSION}")
    st.caption(f"Approx. API spend this session: ${st.session_state['spent']:.4f}")
    if st.button("Clear chat"):
        st.session_state["messages"] = []
        st.rerun()
    st.divider()
    st.caption("Unofficial personal project. Not affiliated with, endorsed by, or speaking for IRDAI or IIB. "
               "Answers come from public documents and can be wrong - check the cited source.")

st.title("\U0001F4D8 PIR Assistant")
st.caption("Ask about India's Public Insurance Registry. Unofficial \u2022 public sources only")

tab_chat, tab_sources, tab_about = st.tabs(["Chat", "Sources", "About"])

# ------------------------------------------------------------------ chat
with tab_chat:
    if not R.ready:
        st.warning("The knowledge base is empty. Owner: run `python -m scraper.fetch` and "
                   "`python -m pipeline.build_index`, then commit `data/processed/`.")
    examples = [
        "What is PIR and what are its goals?",
        "How does the paper propose consent to work?",
        "What would PIR hold centrally vs leave with insurers?",
        "What user stories are there for insurers?",
    ]
    if not st.session_state["messages"]:
        st.write("Try one:")
        for ex in examples:
            if st.button(ex, use_container_width=True, key=f"ex-{ex}"):
                st.session_state["pending"] = ex
                st.rerun()

    for m in st.session_state["messages"]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m.get("sources"):
                with st.expander(f"Sources ({len(m['sources'])})"):
                    for s in m["sources"]:
                        st.markdown(f"**{s['cite']}**")
                        st.caption(s["text"][:500] + ("..." if len(s["text"]) > 500 else ""))
                        if s.get("url"):
                            st.markdown(f"[open source]({s['url']})")

    typed = st.chat_input("Ask a question about PIR...")
    question = typed or st.session_state.pop("pending", None)
    if question:
        today = date.today().isoformat()
        counter = daily_counter()
        if st.session_state["n_asked"] >= MAX_SESSION:
            st.warning("Session question limit reached. Refresh the page to start a new session.")
        elif counter.get(today, 0) >= MAX_DAY:
            st.warning("Today's shared question limit is used up. Please try again tomorrow.")
        elif not API_KEY:
            st.error("No ANTHROPIC_API_KEY configured.")
        else:
            history = [{"role": m["role"], "content": m["content"]} for m in st.session_state["messages"]]
            st.session_state["messages"].append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.markdown(question)
            with st.chat_message("assistant"):
                with st.spinner("Searching the sources..."):
                    try:
                        res = answer(question, history, model=DEEP if mode.startswith("Deep") else FAST, api_key=API_KEY)
                    except Exception as e:
                        res = {"text": f"Sorry - the request failed ({type(e).__name__}). Please try again.",
                               "sources": [], "est_cost_usd": 0.0}
                st.markdown(res["text"])
                if res["sources"]:
                    with st.expander(f"Sources ({len(res['sources'])})"):
                        for s in res["sources"]:
                            st.markdown(f"**{s['cite']}**")
                            st.caption(s["text"][:500] + ("..." if len(s["text"]) > 500 else ""))
            st.session_state["messages"].append(
                {"role": "assistant", "content": res["text"], "sources": res["sources"]})
            st.session_state["n_asked"] += 1
            st.session_state["spent"] += res.get("est_cost_usd", 0.0)
            counter[today] = counter.get(today, 0) + 1
            st.rerun()

# ------------------------------------------------------------------ sources
with tab_sources:
    st.subheader("What is indexed")
    if R.sources:
        for s in R.sources:
            st.markdown(f"**{s['title']}**  \n{s.get('publisher', '')} \u2022 version: {s.get('version') or '-'} \u2022 "
                        f"captured: {s.get('fetched_at') or '-'} \u2022 {s['n_chunks']} passages")
            if s.get("url"):
                st.caption(s["url"])
    else:
        st.info("Nothing indexed yet.")
    if R.stories:
        st.caption(f"{len(R.stories)} user stories \u2022 {len(R.questions)} consultation questions \u2022 "
                   f"{len(R.glossary)} glossary terms")

# ------------------------------------------------------------------ about
with tab_about:
    st.markdown(
        """
**PIR Assistant** is an experimental, unofficial research aid. It retrieves passages from published
public documents (e.g. the IRDAI PIR consultation paper) and asks Claude to answer *only* from them,
with citations.

- It is **not** legal, financial or regulatory advice.
- The consultation paper is a **proposal**; details may change in final regulations.
- If something is not in the sources, it should say so. If an answer looks wrong, open the cited source.

Source code and method: see the project README on GitHub.
        """
    )
