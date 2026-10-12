---
description: Do a task; Codex reviews the diff read-only; fix its findings automatically until PASS (max 4 cycles)
argument-hint: <task>
---
Run this task through the ailoop review gate without waiting for the human. Task: $ARGUMENTS

1. `ailoop start --repo . --nohuman --max-cycles 4 --task "<the task, verbatim>" --check "<the repository's check command>"`
   and note the task id.
2. Do the task completely: implement, test, verify.
3. Run `ailoop review <id>`.
4. Status `done`: give a short DONE summary. Status `fixing`: fix every high and medium finding (if you judge a
   finding wrong, record why with `ailoop decide <id> ignore "<reason>"` instead), then go back to step 3.
5. Status `waiting_human` (cycle limit reached) or `checks_failed`: stop and report the open findings.
