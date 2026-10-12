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
| Unit/integration tests | `pytest` (32 tests): parser, chunker, references, BM25, citations, engine, Claude and OpenAI-compatible request shapes (mocked clients), API (fake engine), evaluation | all passing |
| Golden set integrity | `scripts/verify_golden_set.py`: every expected provision exists and every key fact occurs verbatim | 57/57 valid |
| Retrieval quality | `compare_retrieval_strategies` on the golden set | hybrid + references best: hit@8 0.98, recall@8 0.96, MRR 0.84 |
| End-to-end | Executed notebook, CLI and REST API (`/health`, `/ask`, `/ask/stream`, `/provisions`) | working in extractive and LLM mode |
| Live LLM generation | `aiact-qa eval --provider openai --model gpt-5.4-mini` on all 57 questions | 54 LLM answers, 3 refusals, 0 withheld; citation precision 1.00, must-contain 0.91 ([report](../reports/openai-gpt-5.4-mini)) |
| Static analysis | `ruff` (E, F, W, I, UP, B, SIM) on `src/`, `app/`, `tests/`, `scripts/` (notebook excluded as demo code) | clean |
| Docker image | Built and run as a single container behind Traefik | live demo deployment |

## What the review of AI-generated code caught

Running the evaluation with a real model and reading individual answers (not only aggregate scores) found
defects that the unit tests and the extractive-mode metrics did not:

* **Answer validator rejected correct answers.** 23 of 57 LLM answers were withheld: a citation like
  `[Article 50(5)]` failed against an excerpt labelled `Article 50(4)-(6)` (only range endpoints were parsed), and
  `[Article 18(1)(a)]` failed against a whole-article excerpt. Fixed and covered by a test; 0 answers withheld after.
* **Definition boost outranked obligations.** "What are the obligations of providers…?" was boosted towards the
  Article 3 definition of *provider*; the boost now applies only to "what is X" questions.
* **Glossary matched substrings**, so "define" triggered the expansion for "fine" (penalties).
* **Lay wording missed Annex III.** "CV-screening tool" did not retrieve the employment use case; the golden set
  only passed because it says "screen job applications". Hiring terms were added to the glossary.
* **Quota counted local evaluation runs**, so an eval stopped after 40 questions with quota errors that looked
  like retrieval misses.

Human review remains required for: the legal correctness of the golden-set reference answers (SME review), prompt
wording, and the effort/cost assumptions in `feasibility_and_effort.md`.
