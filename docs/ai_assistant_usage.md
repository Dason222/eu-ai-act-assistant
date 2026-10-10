# Use of AI coding assistants

The prototype was built with **Claude Code** (Anthropic) as an AI pair-programmer in a cloud sandbox, under human
direction. This note lists the key prompts and how the output was validated.

## Key prompts

**1. Task prompt (human → Claude Code)**
> *"Create a natural language interface prototype that allows users to ask questions about a specific EU regulation
> (AI Act) from EUR-Lex … outputs: solution architecture (draw.io) describing components with inputs and outputs;
> prototype code in Python (ipynb and/or .py). Create the prototype so it would be close to the MVP."*

Follow-up instructions from the human during the session:
> *"Can you use sub-agent development?"* – parallel sub-agents were started for interfaces, evaluation and docs (they
> were later stopped and the work was completed in the main session).
> *"There should be not only Claude, but ChatGPT, European AI models etc."* – led to the provider-agnostic LLM layer
> (OpenAI, Azure OpenAI, Mistral, self-hosted open-weights via Ollama/vLLM, OpenAI-compatible EU endpoints).

**2. Representative engineering prompts used inside the session**
* "Inspect the EUR-Lex HTML of OJ L 2024/1689 and write a structure-aware parser keyed on the element ids
  (`art_N`, `NNN.MMM` paragraphs, `rct_N`, `anx_X`, `cpt_X.sct_N`); render enumerated points as indented lines;
  verify counts against the Official Journal."
* "Chunk without crossing provision boundaries, max 280 words, repeat the paragraph lead-in in split pieces, one chunk
  per Article 3 definition."
* "Hybrid retrieval: BM25 + local ONNX embeddings + explicit legal-reference resolver, fused with RRF; compare
  strategies on a golden set."
* "Grounded answers with inline `[Article X(Y)]` citations, validated against the retrieved excerpts; extractive
  fallback without an LLM."
* "Write a golden Q&A set from the regulation text and a script that verifies every expected provision and key fact
  against the parsed text."

**3. Prompts that are part of the product** (see `src/aiact_qa/generation/prompts.py` and `evaluation/judge.py`):
the grounded-answer system prompt, the follow-up condensing prompt and the LLM-as-judge prompt.

## How the output was validated

| Check | What | Result |
|---|---|---|
| Structural fidelity | Parsed counts vs the Official Journal | 113 articles, 180 recitals, 13 annexes, 68 definitions, 13 chapters – exact |
| Text fidelity | Manual spot checks of Articles 1, 3, 4, 5, 9, 50, 51, 99, Annexes I, III, VII against EUR-Lex | Two defects found and fixed: 3-column tables in Annexes I/VII were dropped; superscript exponent rendered "1025" instead of "10^25" |
| Unit/integration tests | `pytest` (23 tests): parser, chunker, references, BM25, citations, engine, Claude and OpenAI-compatible request shapes (mocked clients), API (fake engine), evaluation | all passing |
| Golden set integrity | `scripts/verify_golden_set.py`: every expected provision exists and every key fact occurs verbatim | 57/57 valid |
| Retrieval quality | `compare_retrieval_strategies` on the golden set | hybrid + references best: hit@8 0.98, recall@8 0.96, MRR 0.83 |
| End-to-end | Executed notebook, CLI and REST API (`/health`, `/ask`, `/ask/stream`, `/provisions`) | working in extractive mode |
| Static analysis | `ruff` (E, F, W, I, UP, B, SIM) | clean |
| Not validated here | Live LLM generation (no API key in the build sandbox); Docker image build (no Docker daemon) | run `aiact-qa eval --provider …` and `docker compose build` in the target environment |

Human review remains required for: the legal correctness of the golden-set reference answers (SME review), prompt
wording, and the effort/cost assumptions in `feasibility_and_effort.md`.
