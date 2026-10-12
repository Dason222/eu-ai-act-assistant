"""Streamlit chat UI. Run: ``aiact-qa ui`` or ``streamlit run app/ui.py``."""

from __future__ import annotations

import hmac
import os
import secrets

import streamlit as st

from aiact_qa.api import client_identity, record_feedback
from aiact_qa.budget import BudgetExceeded
from aiact_qa.config import ELI_URL, get_settings
from aiact_qa.pipeline import QAEngine

EXAMPLES = [
    "Which AI practices are prohibited?",
    "Is a CV-screening tool a high-risk AI system?",
    "What must a chatbot disclose to its users?",
    "What are the fines for using a prohibited AI practice?",
    "When do the obligations for general-purpose AI models apply?",
    "What is a deployer?",
]

st.set_page_config(page_title="EU AI Act Assistant", page_icon="⚖️", layout="wide")


@st.cache_resource(show_spinner="Loading the AI Act index…")
def load_engine() -> QAEngine:
    return QAEngine.from_settings(get_settings())


state = st.session_state
password = os.getenv("AIACT_UI_PASSWORD", "")
if password and not state.get("authenticated"):
    st.title("EU AI Act Assistant")
    entered = st.text_input("Demo access password", type="password")
    if st.button("Unlock") and hmac.compare_digest(entered, password):
        state.authenticated = True
        st.rerun()
    st.stop()
# Keyed on the visitor's IP (via Traefik), so reloading the page does not reset the quota.
state.setdefault("budget_identity", "ui-" + client_identity(st.context.headers, secrets.token_hex(8)))
engine = load_engine()
state.setdefault("messages", [])  # [{"role", "content", "answer"?}]
state.setdefault("pending", None)

# --------------------------------------------------------------------------------------------- #
with st.sidebar:
    st.header("EU AI Act Assistant")
    st.caption(
        f"Answers questions about [Regulation (EU) 2024/1689]({ELI_URL}) using only the official text, "
        "with citations to the articles, annexes and recitals used."
    )
    allowed = {p.strip() for p in get_settings().allowed_providers.split(",")}
    providers = [p for p in engine.available_providers() if p["configured"] and p["id"] in allowed]
    labels = {p["id"]: f"{p['label']}" for p in providers}
    default_id = engine.llm.provider if engine.llm else "extractive"
    ids = list(labels)
    provider = st.selectbox(
        "Language model", ids, index=ids.index(default_id) if default_id in ids else 0,
        format_func=lambda i: labels[i],
    )
    info = next(p for p in providers if p["id"] == provider)
    # No free-text model overrides: expensive models cannot be selected by web visitors.
    model = ""
    st.caption(f"Hosting: {info['hosting']}")
    top_k = st.slider("Excerpts sent to the model", 3, 15, get_settings().top_k)
    st.subheader("Try")
    for example in EXAMPLES:
        if st.button(example, use_container_width=True):
            state.pending = example
    if st.button("Clear conversation", type="secondary"):
        state.messages = []
    stats = engine.stats()
    st.caption(
        f"Index: {stats['articles']} articles, {stats['recitals']} recitals, {stats['annexes']} annexes, "
        f"{stats['chunks']} chunks · retrieval: {stats['retrieval']}"
    )


def render_answer(answer: dict, key: str) -> None:
    for warning in answer.get("warnings", []):
        st.warning(warning)
    if answer["sources"]:
        cited = {c["provision_id"] for c in answer["citations"]}
        with st.expander(f"Sources ({len(answer['sources'])})"):
            for s in answer["sources"]:
                mark = "✅ cited" if s["provision_id"] in cited else ""
                st.markdown(f"**[{s['n']}] [{s['label']}]({s['url']})** {s['title']} {mark}")
                st.caption(" ".join(s["text"].split()[:70]) + " …")
    meta = f"{answer['mode']}" + (f" · {answer['model']}" if answer.get("model") else "")
    meta += f" · {answer['latency_ms']} ms"
    if answer.get("standalone_question") and answer["standalone_question"] != answer["question"]:
        meta += f" · searched for: “{answer['standalone_question']}”"
    st.caption(meta)
    up, down, _ = st.columns([1, 1, 10])
    for col, rating, icon in ((up, "up", "👍"), (down, "down", "👎")):
        if col.button(icon, key=f"{key}-{rating}"):
            record_feedback(get_settings(), {
                "question": answer["question"], "answer": answer["answer"], "rating": rating,
                "sources": [s["chunk_id"] for s in answer["sources"]], "provider": provider,
            })
            st.toast("Thanks for the feedback")


st.title("⚖️ EU AI Act Assistant")
for i, message in enumerate(state.messages):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("answer"):
            render_answer(message["answer"], key=f"m{i}")

question = st.chat_input("Ask a question about the EU AI Act…") or state.pending
state.pending = None
if question:
    history = [{"role": m["role"], "content": m["content"]} for m in state.messages]
    state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            active = engine.for_provider(provider, model or None)
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
        final: dict = {}

        def deltas():
            for event in active.ask_stream(
                question, history=history, top_k=top_k, identity=state.budget_identity
            ):
                if event["type"] == "delta":
                    yield event["text"]
                elif event["type"] == "done":
                    final.update(event["answer"])

        try:
            with st.spinner("Searching and verifying sources…"):
                st.write_stream(deltas())
        except BudgetExceeded as exc:
            st.warning(str(exc))
            st.stop()
        if final:
            render_answer(final, key=f"m{len(state.messages)}")
    if final:
        state.messages.append({"role": "assistant", "content": final["answer"], "answer": final})

st.caption(
    "Informational prototype - not legal advice. Answers are generated from the text of Regulation (EU) "
    "2024/1689 as published in the Official Journal; verify against the official text via the links."
)
