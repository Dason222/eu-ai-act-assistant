"""Optional LLM-as-judge: faithfulness and completeness of an answer (1-5) with Claude."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from aiact_qa.evaluation.dataset import EvalItem
from aiact_qa.schemas import Answer

log = logging.getLogger(__name__)

JUDGE_PROMPT = """\
You grade answers of a question-answering assistant about the EU AI Act.

<question>{question}</question>
<reference_answer>{reference}</reference_answer>
<excerpts_given_to_the_assistant>
{excerpts}
</excerpts_given_to_the_assistant>
<assistant_answer>{answer}</assistant_answer>

Score two criteria from 1 (poor) to 5 (excellent):
- faithfulness: every claim in the assistant answer is supported by the excerpts (no invented facts).
- completeness: the answer covers the key facts of the reference answer.
Reply with JSON only: {{"faithfulness": <1-5>, "completeness": <1-5>, "reasoning": "<one sentence>"}}"""


class Judge:
    def __init__(self, model: str = "claude-opus-5-5") -> None:
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model

    def score(self, item: EvalItem, answer: Answer) -> dict[str, Any] | None:
        excerpts = "\n\n".join(f"[{s.label}] {s.text}" for s in answer.sources) or "(none)"
        prompt = JUDGE_PROMPT.format(
            question=item.question, reference=item.expected_answer, excerpts=excerpts, answer=answer.answer
        )
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in response.content if b.type == "text")
            match = re.search(r"\{.*\}", text, re.S)
            data = json.loads(match.group(0)) if match else None
            if not data:
                return None
            return {k: data.get(k) for k in ("faithfulness", "completeness", "reasoning")}
        except Exception as exc:  # the judge must never break an evaluation run
            log.warning("judge failed for %s: %s", item.id, exc)
            return None
