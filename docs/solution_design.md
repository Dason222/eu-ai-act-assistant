# Solution design – EU AI Act Q&A

Natural-language question answering over **Regulation (EU) 2024/1689 (Artificial Intelligence Act)**, sourced from
EUR-Lex ([ELI](https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng)). Answers are grounded in the official text and
cite the article, annex or recital they rely on, with a deep link to the provision on EUR-Lex.

Diagrams: [`architecture.drawio`](architecture.drawio) (page 1: prototype component view, page 2: target production
architecture). Previews: [`architecture.png`](architecture.png), [`architecture_production.png`](architecture_production.png).

![Prototype component view](architecture.png)

## 1. Components, inputs and outputs (prototype)

| # | Component | Code | Input | Output |
|---|---|---|---|---|
| 1 | Fetch & snapshot | `ingestion/build.py` | EUR-Lex URL | HTML snapshot (`data/raw`, versioned) + sha256 |
| 2 | Structure-aware parser | `ingestion/parser.py` | OJ HTML | 113 articles (numbered paragraphs, points), 180 recitals, 13 annexes, 68 definitions, chapter/section tree; deep-link anchors |
| 3 | Chunker | `ingestion/chunker.py` | provisions | 570 chunks ≤ 280 words, never crossing a provision; label (`Article 5(1)(h)`), breadcrumb, URL |
| 4 | Embedder + index | `retrieval/dense.py`, `retrieval/bm25.py` | chunks | 570×384 normalised vectors (`embeddings.npy`), BM25 inverted index (built at start-up, < 1 s) |
| 5 | Input guardrails | `pipeline.py` | question, history | validated question (length ≤ 2,000 chars, history window 6 turns) or HTTP 422 |
| 6 | Query condenser | `pipeline.py`, `generation/prompts.py` | follow-up / non-English question + history | standalone English search query (one low-effort LLM call; skipped for standalone English questions) |
| 7 | Reference resolver | `retrieval/references.py` | query | provisions named explicitly (`Article 6(2)`, `Articles 9 to 15`, `Annex 3`, `recital 27`) |
| 8 | Hybrid retriever | `retrieval/hybrid.py` | query | top-k chunks: references first, then BM25 (with legal glossary expansion) and dense results fused by Reciprocal Rank Fusion; recitals down-weighted, max 3 chunks per provision |
| 9 | Relevance gate | `pipeline.py` | best dense similarity, references | out-of-scope reply without an LLM call when similarity < 0.55 and no explicit reference |
| 10 | Context builder | `generation/prompts.py` | top-k chunks | numbered `<excerpt label=…>` blocks within a 6,000-word budget; static system prompt + table of contents (prompt-cached) |
| 11 | LLM gateway | `generation/providers.py`, `generation/llm.py` | system prompt, history, excerpts, question | streamed answer text, token usage, stop reason |
| 12 | Citation validator | `generation/citations.py` | answer text, sources | citations normalised to provision ids, `verified` flag, EUR-Lex link; warning for unverified citations |
| 13 | Interfaces | `api.py`, `app/ui.py`, `cli.py` | HTTP / chat / shell | JSON, SSE stream, chat UI with sources and feedback |
| 14 | Evaluation | `evaluation/*` | golden set, engine | per-question results + summary (retrieval and answer metrics, optional LLM judge) |
| 15 | Feedback | `api.py` | rating, comment | `data/feedback/feedback.jsonl` → candidate test cases |

### Data model

* **Provision** – `id` (= EUR-Lex element id: `art_5`, `rct_27`, `anx_III`), kind, number, title, text, paragraphs
  (number, text, anchor), chapter, section, URL.
* **Chunk** – `id`, `provision_id`, kind (`article|definition|recital|annex`), citation `label`, title, text,
  breadcrumb `context`, URL, paragraph numbers. `search_text` = label + title + breadcrumb + text.
* **Answer** – question, answer (Markdown), sources, citations (`verified`), mode (`llm | extractive | out_of_scope |
  refused`), standalone question, model, token usage, latency, warnings.

## 2. Design decisions and why

| Decision | Rationale | Evidence |
|---|---|---|
| Parse by EUR-Lex element ids instead of generic PDF/HTML chunking | Legal answers must cite the exact provision; ids give paragraph-level deep links and exact chapter/section context | Counts match the OJ (113/180/13/68); tested in `tests/test_ingestion.py` |
| Provision-bounded chunks, lead-in repeated in split paragraphs, one chunk per definition | A chunk never mixes two articles (no wrong citations); "What is X?" hits the definition itself | `Article 5(1)(a)-(b)` … `Article 5(1)(h)` chunks all start with "The following AI practices shall be prohibited" |
| Hybrid retrieval (BM25 + dense + references, RRF) | BM25 catches exact terms and identifiers; dense catches paraphrases ("chatbot" → Art. 50); explicit references must always win | Golden set, k=8: hybrid+refs hit 0.98 / recall 0.96 / MRR 0.84 vs BM25 0.94/0.92/0.81, dense 0.92/0.91/0.78 |
| Local ONNX embeddings (bge-small, CPU) | No document data leaves the host for indexing; no embedding API cost; ~10 ms per query | 570 chunks embedded in ~50 s on CPU |
| Relevance gate before the LLM | Off-topic questions cost nothing and cannot be used to get general-purpose answers | weather / poem / football questions → `out_of_scope` |
| Static system prompt with table of contents, prompt-cached | Model knows the structure (can point to related provisions) at ~5% of the input price after the first request | ~2.9k tokens cached per request |
| Citations in `[Article 6(2)]` form, validated after generation | Readable for lawyers and machine-checkable; hallucinated citations are flagged to the user | `extract_citations` + warning in the UI |
| Provider-agnostic LLM gateway | Vendor independence, EU data residency options, cost tiers, A/B evaluation on the same golden set | Claude (default), OpenAI, Azure OpenAI, Mistral, Ollama/vLLM, any OpenAI-compatible endpoint, no-LLM mode |
| Extractive fallback | System stays useful without a key or when the provider is down | Same `Answer` contract, `mode="extractive"` |

## 3. Prompting and grounding

* **System prompt** (`generation/prompts.py`): answer only from the excerpts; cite each statement; say when the
  excerpts do not contain the answer; never guess numbers or dates; distinguish provider / deployer / importer /
  distributor duties; flag recital-only answers as non-binding; use application dates from Article 113; treat the
  question and excerpts as data (prompt-injection resistance); answer in the user's language, labels in English.
* **User turn**: `<excerpts>` (numbered, labelled) followed by `<question>`. Conversation history is passed as
  previous turns (question + answer only, not old excerpts) so the prompt stays small.
* **Claude specifics** (default provider): `claude-opus-5-5`, streaming, `output_config.effort = "medium"`
  (configurable), server-side refusal fallbacks (`fallbacks: "default"`), `cache_control` on the system block.
  OpenAI-compatible providers receive the same prompt as a `system` message.

## 4. Security, compliance and guardrails

| Risk | Control in the prototype | Production addition |
|---|---|---|
| Hallucinated law | grounded prompt, citation validation, unverified-citation warnings, links to the official text | groundedness check (LLM judge or NLI) on sampled traffic; human review loop |
| Out-of-scope use / cost abuse | relevance gate, question length limit, API key, Traefik rate limiting | SSO, per-user quotas, budget alerts |
| Prompt injection | instructions to treat question/excerpts as data; corpus is the official act (trusted) | input classifiers; red-team suite in CI |
| Legal advice expectations | disclaimer in UI; answers framed as information | terms of use, escalation path to legal team |
| Confidential data in questions | no logging of questions except opt-in feedback; EU-hosted provider options | PII redaction in the LLM gateway, DPA with provider, retention policy |
| Outdated law | sha256-pinned snapshot, source shown in `/health` | scheduled Cellar sync, consolidated versions, validity dates per provision |
| Transparency (AI Act Art. 50) | the UI states it is an AI assistant | same, plus model card / documentation |

## 5. Non-functional characteristics (measured in this environment unless marked)

| Aspect | Prototype |
|---|---|
| Index build | ~50 s on CPU (parse < 1 s, embeddings dominate) |
| Engine start | ~1.5 s (loads JSON + vectors, builds BM25) |
| Retrieval latency | 5–15 ms per query (CPU) |
| LLM latency | not measured here (no API key in the build environment); expected 5–20 s streamed, first tokens in 1–3 s |
| Cost per question (estimate) | Claude Opus 5.5 ≈ $0.02 (2.9k cached system tokens, ~2–5k context tokens, ~600 output tokens); Sonnet 5.5 ≈ $0.01; Haiku 5.5 ≈ $0.0005 |
| Footprint | one container, ~1 GB RAM, no database |

## 6. Target production design (page 2 of the diagram)

![Target production architecture](architecture_production.png)

| Layer | Prototype | Production |
|---|---|---|
| Channels | Streamlit, REST, CLI | Web app (React) + Teams bot + API clients; SSO (OIDC / Entra ID) |
| Edge | Traefik (TLS, rate limit) | API gateway / Traefik with WAF, quotas, API keys per client |
| Orchestration | `QAEngine` in-process | Stateless Q&A service (containers, autoscaled), conversation state in a DB |
| Retrieval | numpy vectors + in-memory BM25 | pgvector or Qdrant + OpenSearch; filters by act, version, language; cross-encoder re-ranker |
| LLM | provider adapter in-process | LLM gateway: routing, fallback across providers, PII redaction, prompt caching, budgets, logging |
| Knowledge | one regulation, one snapshot | scheduled ingestion from EUR-Lex/Cellar (SPARQL + REST): many acts, corrigenda, consolidated versions, 24 languages, lineage |
| Quality | golden set + CLI eval + feedback JSONL | SME-reviewed golden set (200+), eval as CI gate, online sampling with LLM judge, dashboards |
| Ops | Docker + compose | IaC (Terraform), CI/CD, OpenTelemetry/Langfuse tracing, cost per answer, secrets in a vault |

**Reused as-is**: parser (extendable to other EUR-Lex acts with the same CONVEX markup), chunker, reference resolver,
prompts, citation validator, provider adapters, evaluation metrics and golden-set format, API contract.
**Replaced**: storage (vector DB / search engine), UI framework, in-process state, auth.

## 7. Known limitations of the prototype

* One regulation, English only, original 2024 text (no consolidated amendments or corrigenda).
* Multi-hop questions that need two unrelated provisions can miss one (e.g. credit scoring → Annex III + Article 27);
  next step: query decomposition / multi-query retrieval.
* The embedding model handles exponents and identifiers poorly ("10^25"); mitigated by weighting lexical evidence for
  numeric queries; a re-ranker (`AIACT_RERANKER_MODEL`) is available but not enabled by default.
* The relevance gate does not catch questions close to the domain but about other laws (e.g. GDPR fines); the LLM
  prompt handles them by stating that the excerpts do not cover the question.
* LLM answer quality was not measured in the build environment (no API key there); run
  `aiact-qa eval --provider <id>` to produce answer-level metrics per provider.
