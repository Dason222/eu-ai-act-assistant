---
description: Do a task, then get a read-only Codex review of the diff; stop for the human on findings
argument-hint: <task>
---
Run this task through the ailoop review gate. Task: $ARGUMENTS

1. Register the task (from the repository root). Pick the repository's own check command, e.g.
   `make test && ruff check .` or `npm test`:
   `ailoop start --repo . --task "<the task, verbatim>" --check "<check command>"`
   Note the task id it prints.
2. Do the task completely: implement, test, verify. Do not commit secrets.
3. Run `ailoop review <id>`. Codex reviews the diff since the task's base commit, read-only.
4. If the verdict is PASS and the checks are OK: give a short DONE summary (what changed, review result, checks).
5. If the verdict is FAIL: STOP. Show the human every finding with your own assessment (agree / disagree and
   why) and ask what to do: fix, ignore a finding, or do it differently. Record the answer with
   `ailoop decide <id> fix|ignore|redo "<their words>"`, act on it, then go back to step 3.
6. If Claude cannot continue (usage limit), the human runs `ailoop handover <id>` and Codex
   continues the task.
