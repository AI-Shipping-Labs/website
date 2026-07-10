# Agent Notes

## Development Process

- Before continuing development work, read `_docs/PROCESS.md` and follow the issue pipeline documented there.
- Treat feature requests for this repo as permission to launch the role subagents required by `_docs/PROCESS.md` (PM, software engineer, tester, PM acceptance, on-call) unless the user explicitly asks not to.
- Treat "continue where we stopped" as a prompt to check `_docs/PROCESS.md`, inspect the current issue/worktree/process state, and resume the next pipeline step.
- When launching Codex subagents for this workflow, use `gpt-5.5` with `reasoning_effort: "high"` and `service_tier: "priority"` by default unless the user explicitly asks for a cheaper or lower-reasoning run. Do not fall back to `gpt-5.4`; retry `gpt-5.5` later or keep the work local if `gpt-5.5` is unavailable. When launching Claude subagents, use Opus 4.8 by default.

## Production Data Access

- Production URL: `https://aishippinglabs.com`.
- Do not assume local files, SQLite, or a remote database tunnel represent production data.
- Agents cannot access production data directly. Use the authenticated production API when checking production users, email logs, SES events, or other live records.
- Do not print API tokens or other secrets in logs, comments, or final responses.
