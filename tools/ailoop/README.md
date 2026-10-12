# ailoop

Cross-model review gate for Claude Code. Claude does the task; OpenAI Codex reviews the Git diff read-only.

| File | Purpose |
|---|---|
| `ailoop` | Manager (Python, standard library only): `start`, `review`, `decide`, `handover`, `status`, `log`. State in `~/.ailoop/state.db`. |
| `ailoop.md` | Claude Code command `/ailoop <task>`: stops for the human when Codex reports findings. |
| `ailoop-nohuman.md` | Claude Code command `/ailoop-nohuman <task>`: fixes findings automatically, at most four cycles. |

Install: put `ailoop` on `PATH`, copy the two command files to `~/.claude/commands/`, and log in to the Codex CLI.
Run log of the first task: [`docs/ailoop/task-1.md`](../../docs/ailoop/task-1.md).
