"""Streamlit chat UI. Run: ``aiact-qa ui`` or ``streamlit run app/ui.py``."""

from __future__ import annotations

import hmac
import html
import os
import re
import secrets

import streamlit as st

from aiact_qa.api import client_identity, record_feedback
from aiact_qa.budget import BudgetExceeded
from aiact_qa.config import ELI_URL, get_settings
from aiact_qa.generation.citations import link_citations
from aiact_qa.pipeline import QAEngine
from aiact_qa.schemas import Citation

EXAMPLES = [
    "Is a CV-screening tool a high-risk AI system?",
    "Which AI practices are prohibited?",
    "What must a chatbot tell its users?",
    "What are the fines for using a prohibited AI practice?",
    "When do the rules for general-purpose AI apply?",
    "Provider or deployer: what is the difference?",
]
USER_AVATAR, ASSISTANT_AVATAR = ":material/person:", ":material/balance:"

st.set_page_config(page_title="EU AI Act Assistant", page_icon=":material/balance:", layout="centered")
st.html("""<style>
[data-testid="stMainBlockContainer"] { max-width: 50rem; padding-top: 3.5rem; }
h1 { font-size: 1.75rem !important; font-weight: 600 !important; letter-spacing: -0.02em;
     padding-bottom: 0.25rem !important; }
.lede { color: #4B5563; font-size: 1rem; line-height: 1.6; max-width: 40rem; margin: 0 0 1.75rem; }
.muted, [data-testid="stMarkdownContainer"] p.muted { color: #6B7280; font-size: 0.8125rem; line-height: 1.5; }
[data-testid="stChatMessage"] { padding: 0.25rem 0; background: transparent; }
[data-testid="stChatMessage"] p, [data-testid="stChatMessage"] li { line-height: 1.65; }
[data-testid="stChatMessage"] a, .sources a {
  color: inherit; text-decoration: underline; text-decoration-color: #9CA3AF;
  text-decoration-thickness: 1px; text-underline-offset: 3px; transition: text-decoration-color .15s ease-out;
}
[data-testid="stChatMessage"] a:hover, .sources a:hover { text-decoration-color: #111827; }
a:focus-visible, button:focus-visible { outline: 2px solid #111827; outline-offset: 2px; border-radius: 4px; }
::selection { background: #E5E7EB; color: #111827; }
.stButton button { justify-content: flex-start; text-align: left; font-weight: 400; color: #374151;
  padding: 0.625rem 0.875rem; height: 100%; }
.stButton button p { white-space: normal; text-align: left; }
[data-testid="stColumn"] .stButton { height: 100%; }
.stButton button:hover { color: #111827; border-color: #9CA3AF; }
[data-testid="stExpander"] details { border-color: #E5E7EB; }
footer, #MainMenu { visibility: hidden; }
</style>""")


@st.cache_resource(show_spinner="Loading the AI Act index…")
def load_engine() -> QAEngine:
    return QAEngine.from_settings(get_settings())


state = st.session_state
password = os.getenv("AIACT_UI_PASSWORD", "")
if password and not state.get("authenticated"):
    st.title("EU AI Act Assistant")
    entered = st.text_input("Access code", type="password")
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
settings = get_settings()
allowed = {p.strip() for p in settings.allowed_providers.split(",")}
providers = [p for p in engine.available_providers() if p["configured"] and p["id"] in allowed]
default_id = engine.llm.provider if engine.llm else "extractive"


def provider_label(p: dict) -> str:
    if p["id"] == "extractive":
        return "No model: quote the provisions"
    model = engine.llm.model if engine.llm and engine.llm.provider == p["id"] else p["default_model"]
    return f"{p['label'].split(' (')[0]} · {model}"


with st.sidebar:
    st.markdown("**EU AI Act Assistant**")
    st.markdown(
        f'<p class="muted">Answers draw on retrieved passages from <a href="{ELI_URL}">Regulation (EU) 2024/1689</a> '
        "as published in the Official Journal. References identify supporting provisions, "
        "but do not guarantee legal accuracy.</p>",
        unsafe_allow_html=True,
    )
    if st.button("New conversation", icon=":material/add:", use_container_width=True):
        state.messages = []
        st.rerun()
    st.divider()
    ids = [p["id"] for p in providers]
    by_id = {p["id"]: p for p in providers}
    provider = st.selectbox(
        "Answer with", ids, index=ids.index(default_id) if default_id in ids else 0,
        format_func=lambda i: provider_label(by_id[i]),
    )
    # No free-text model overrides: expensive models cannot be selected by web visitors.
    model = ""
    with st.expander("Retrieval settings"):
        top_k = st.slider("Excerpts given to the model", 3, 15, settings.top_k)
        st.caption(f"Hosting: {by_id[provider]['hosting']}")
    stats = engine.stats()
    st.markdown(
        f'<p class="muted">Indexed: {stats["articles"]} articles, {stats["recitals"]} recitals, '
        f'{stats["annexes"]} annexes · hybrid search (keywords + meaning + explicit references)</p>',
        unsafe_allow_html=True,
    )


def short_model(name: str | None) -> str:
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", name or "")


def describe(answer: dict) -> str:
    seconds = f"{answer['latency_ms'] / 1000:.1f} s"
    if answer["mode"] == "llm":
        return f"Answered by {short_model(answer.get('model'))} · {seconds}"
    if answer["mode"] == "extractive":
        return f"Quoted from the regulation, no language model · {seconds}"
    if answer["mode"] == "out_of_scope":
        return "Outside the scope of the AI Act"
    return f"The model declined to answer · {seconds}"


def render_answer(answer: dict, key: str) -> None:
    for warning in answer.get("warnings", []):
        st.warning(warning, icon=":material/info:")
    meta = describe(answer)
    if answer.get("standalone_question") and answer["standalone_question"] != answer["question"]:
        meta += f" · searched for “{answer['standalone_question']}”"
    st.markdown(f'<p class="muted">{html.escape(meta)}</p>', unsafe_allow_html=True)
    if answer["sources"] and answer["mode"] != "out_of_scope":
        cited = {c["provision_id"] for c in answer["citations"]}
        sources = sorted(answer["sources"], key=lambda s: s["provision_id"] not in cited)
        n_cited = sum(s["provision_id"] in cited for s in answer["sources"])
        label = f"Sources · {n_cited} cited, {len(sources) - n_cited} also retrieved" if n_cited else (
            f"Sources · {len(sources)} retrieved")
        with st.expander(label, icon=":material/menu_book:"):
            for s in sources:
                tag = " · cited" if s["provision_id"] in cited else ""
                st.markdown(
                    f'<div class="sources"><a href="{s["url"]}" target="_blank">{html.escape(s["label"])}</a>'
                    f' <span class="muted">{html.escape(s["title"] or "")}{tag}</span></div>',
                    unsafe_allow_html=True,
                )
                st.caption(" ".join(s["text"].split()[:60]) + " …")
    rating = st.feedback("thumbs", key=f"{key}-rating")
    if rating is not None and f"{key}-rated" not in state:
        state[f"{key}-rated"] = True
        record_feedback(settings, {
            "question": answer["question"], "answer": answer["answer"], "rating": "up" if rating else "down",
            "sources": [s["chunk_id"] for s in answer["sources"]], "provider": provider,
        })
        st.toast("Thanks, feedback saved")


def show(answer: dict) -> str:
    """Answer markdown with each [Article X(Y)] citation linked to its paragraph on EUR-Lex."""
    return link_citations(answer["answer"], [Citation(**c) for c in answer["citations"]])


question = st.chat_input("Ask a question about the EU AI Act") or state.pending
state.pending = None

st.title("EU AI Act Assistant")
if not state.messages and not question:
    st.markdown(
        '<p class="lede">Ask about obligations, prohibited practices, high-risk systems or penalties under '
        "Regulation (EU) 2024/1689. Answers draw on retrieved provisions and link to the official EUR-Lex text.</p>",
        unsafe_allow_html=True,
    )
    left, right = st.columns(2)
    for i, example in enumerate(EXAMPLES):
        # on_click runs before the rerun, so the question is picked up at the top of the script
        (left if i % 2 == 0 else right).button(
            example, key=f"ex{i}", use_container_width=True, on_click=state.update, kwargs={"pending": example}
        )

for i, message in enumerate(state.messages):
    with st.chat_message(message["role"], avatar=USER_AVATAR if message["role"] == "user" else ASSISTANT_AVATAR):
        if message.get("answer"):
            st.markdown(show(message["answer"]))
            render_answer(message["answer"], key=f"m{i}")
        else:
            st.markdown(message["content"])

if question:
    history = [{"role": m["role"], "content": m["content"]} for m in state.messages]
    state.messages.append({"role": "user", "content": question})
    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(question)
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        try:
            active = engine.for_provider(provider, model or None)
            with st.spinner("Searching the regulation and checking citations…"):
                final = active.ask(question, history=history, top_k=top_k, identity=state.budget_identity).to_dict()
        except BudgetExceeded as exc:
            st.warning(f"{exc} The quota protects the demo's API budget.", icon=":material/hourglass:")
            state.messages.pop()
            st.stop()
        except ValueError as exc:
            st.error(str(exc))
            state.messages.pop()
            st.stop()
        st.markdown(show(final))
        render_answer(final, key=f"m{len(state.messages)}")
    state.messages.append({"role": "assistant", "content": final["answer"], "answer": final})

st.markdown(
    '<p class="muted" style="margin-top:2rem">Informational prototype, not legal advice. '
    "Verify against the official text through the links.</p>",
    unsafe_allow_html=True,
)
