# Feasibility, roadmap and effort estimation

## 1. Feasibility verdict

**Feasible, with moderate risk concentrated in legal-quality assurance rather than technology.** The prototype already
covers the full path from the official EUR-Lex text to cited answers, with an evaluation harness. The remaining work
is mostly productisation (security, multi-regulation data platform, operations) and building trust in answer
quality with legal subject-matter experts.

| Dimension | Assessment | Evidence / notes |
|---|---|---|
| Data availability | ✅ High | EUR-Lex publishes structured HTML/XML (Formex) with stable element ids; Cellar offers SPARQL + REST for metadata, versions and languages. Reuse of EUR-Lex content is authorised with acknowledgement of the source (Commission Decision 2011/833/EU). |
| Parsing quality | ✅ High | Structure-aware parser reproduces the OJ structure exactly (113 articles, 180 recitals, 13 annexes, 68 definitions); one markup issue (superscript exponents) found and fixed via the notebook review. |
| Retrieval quality | ✅ Good | Golden set (49 answerable questions): hit@8 0.98, recall@8 0.96, MRR 0.84. Gaps: multi-hop questions. |
| Answer quality | ✅ Promising, ⚠️ needs SME sign-off | Measured with OpenAI `gpt-5.4-mini` on 57 questions: citation precision 1.00, expected-citation recall 0.96, must-contain facts 0.88, all out-of-scope and injection probes handled. Acceptance criteria need an SME-reviewed golden set; other providers are compared with the same harness. |
| Cost | ✅ Low | ≈ $0.02 per question with Claude Opus 5.5 (estimate); measured with `gpt-5.4-mini`: ≈ 4.5k input + 0.23k output tokens per question, i.e. well under a cent. Retrieval runs on CPU. |
| Data residency / sovereignty | ✅ Options exist | EU-hosted options: Azure OpenAI EU regions, Mistral (EU), Claude via cloud platforms with EU regions, or self-hosted open-weights models (Ollama/vLLM) – all behind the same interface. |
| Regulatory exposure | ⚠️ Manage | The assistant itself is an AI system interacting with people (AI Act Art. 50 transparency); must be positioned as information, not legal advice. |

## 2. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Wrong or incomplete legal answer relied upon | Medium | High | Grounded prompt, mandatory citations with links, unverified-citation warnings, disclaimer, SME-reviewed golden set as release gate, feedback loop |
| Law changes (amendments, corrigenda, delegated acts, guidelines) | High | Medium | Versioned ingestion from Cellar, validity dates per provision, "as of" date in answers, alerts on new acts |
| Multi-hop / cross-regulation questions | Medium | Medium | Query decomposition, multi-query retrieval, re-ranker, cross-references graph (the parser already extracts references) |
| Provider lock-in or outage | Medium | Medium | Provider-agnostic gateway, fallback provider, extractive degraded mode |
| Confidential information in questions | Medium | High | SSO, PII redaction, EU-hosted / self-hosted models, DPAs, retention policy |
| Cost overrun | Low | Medium | Relevance gate, prompt caching, per-user quotas, model tiering by question complexity |
| Low adoption / trust | Medium | Medium | Citations and deep links in every answer, source panel, pilot with legal team, feedback-driven improvements |

## 3. Roadmap

| Phase | Scope | Duration |
|---|---|---|
| 0. Prototype (done) | One regulation, hybrid RAG, multi-provider LLM, API + UI, golden set, Docker/Traefik | – |
| 1. MVP pilot | AI Act (+ consolidated versions), SSO, conversation history, SME-reviewed golden set (≈200 Q), provider selection based on eval, observability basics, pilot with 20–50 internal users | 8–10 weeks |
| 2. Production | Multi-regulation corpus (e.g. GDPR, DSA, DMA, Data Act, NIS2) with versioning, vector DB + search engine, LLM gateway with fallback & PII redaction, CI eval gate, SLAs, security review, Teams integration | 3–4 months |
| 3. Scale & differentiate | Multilingual (24 EU languages), cross-regulation reasoning, obligation checklists per role/use case, agentic workflows (e.g. "classify my system under Art. 6"), client-specific knowledge (policies, guidelines) | 3–6 months |

## 4. Effort estimate (person-days, three-point ranges)

Assumptions: experienced team familiar with RAG and the target cloud; the prototype is reused; one target cloud;
client provides SSO and a legal SME; estimates exclude procurement and security-review waiting time.

### Phase 1 – MVP pilot

| Work package | AI eng. | Backend | Frontend | DevOps | Legal SME | PM/QA | Total (min–max) |
|---|---|---|---|---|---|---|---|
| Discovery, requirements, acceptance criteria | 2 | 1 | 1 | 1 | 2 | 3 | 8–12 |
| Ingestion hardening (Cellar, versions, corrigenda) | 8 | 4 | – | – | 1 | 1 | 12–18 |
| Retrieval tuning (re-ranker, multi-query, eval) | 12 | – | – | – | 2 | 1 | 13–20 |
| Generation, guardrails, provider selection | 10 | 3 | – | – | 2 | 1 | 14–20 |
| Web UI (SSO, history, feedback, sources) | – | 3 | 14 | – | – | 2 | 17–24 |
| API, auth, logging, audit | – | 10 | – | 2 | – | 1 | 12–17 |
| Infrastructure, CI/CD, monitoring | – | 2 | – | 12 | – | 1 | 13–18 |
| Golden set (≈200 Q) & UAT | 3 | – | – | – | 12 | 4 | 17–24 |
| Project management & QA | – | – | – | – | – | 12 | 10–15 |
| **Total** | | | | | | | **≈ 115–170 PD** |

Team: 1 AI engineer (lead), 1 backend, 1 frontend (part-time), 0.5 DevOps, 0.3 legal SME, 0.5 PM/QA →
**8–10 weeks**.

### Phase 2 – Production

| Work package | Total (min–max) |
|---|---|
| Multi-regulation data platform (Cellar sync, versioning, lineage, 5–10 acts) | 40–60 |
| Vector DB + search engine, re-ranking, filters by act/version | 25–35 |
| LLM gateway (routing, fallback, PII redaction, budgets, caching) | 25–35 |
| Security (threat model, pen-test fixes, DPIA support), SSO/RBAC | 20–30 |
| Observability (tracing, cost dashboards), continuous evaluation in CI | 20–30 |
| UI/UX hardening, Teams bot | 30–40 |
| Golden sets per regulation + legal review | 30–45 |
| Performance/load testing, SLAs, runbooks, hypercare | 20–30 |
| PM, QA, documentation | 30–45 |
| **Total** | **≈ 240–350 PD** (team of 4–5, 3–4 months) |

### Phase 3 – Scale & differentiate
Multilingual, cross-regulation reasoning and agentic features: **≈ 200–300 PD**, depending on scope chosen after the
pilot.

## 5. Running cost model (monthly, indicative)

LLM cost per question, estimated from prompt sizes in the notebook (2.9k cached system tokens, ~2–5k context tokens, ~600 output
tokens; Anthropic list prices: Opus 5.5 $4/$20 per MTok input/output, cached input $0.20; Sonnet 5.5 $2/$10;
Haiku 5.5 $0.10/$0.50). Follow-up questions add one small rewriting call.

| Questions / month | Claude Opus 5.5 (~$0.02–0.03/Q) | Claude Sonnet 5.5 (~$0.01–0.015/Q) | Claude Haiku 5.5 (~$0.0005–0.001/Q) |
|---|---|---|---|
| 1,000 | $20–30 | $10–15 | < $1 |
| 10,000 | $200–300 | $100–150 | $5–10 |
| 100,000 | $2,000–3,000 | $1,000–1,500 | $50–100 |

| Other providers | Hosting | Indicative price tier | Notes |
|---|---|---|---|
| OpenAI / Azure OpenAI | US/EU (Azure EU regions) | check current price list | Azure common in enterprise landscapes |
| Mistral AI | EU (France) | check current price list | European provider |
| Self-hosted open-weights (Ollama/vLLM: Mistral, EuroLLM, Teuken, Llama…) | own servers / EU VPS | GPU cost instead of tokens | full data control; quality must be validated on the golden set |
| OpenAI-compatible EU clouds (Scaleway, OVHcloud, IONOS) | EU | check current price list | sovereignty without running GPUs |

Infrastructure: prototype runs on a single small VM (≈ €20–60/month). Production (2–3 app containers, managed
Postgres/pgvector or Qdrant, search engine, observability): ≈ €800–2,500/month depending on cloud and SLAs.

## 6. Open decisions for the client

1. **Model/provider**: quality vs cost vs residency – decide from `aiact-qa eval --provider …` results on the
   SME-reviewed golden set.
2. **Data residency**: EU-only processing required? (drives Azure EU / Mistral / self-hosted choice).
3. **Scope of corpus**: which regulations, guidelines and internal documents; how often to refresh.
4. **Channel**: standalone web app vs Teams integration vs embedded in existing compliance tooling.
5. **Build vs buy**: commercial legal-AI tools exist; the differentiator here is control over sources, citations,
   evaluation and integration with client-specific content.
