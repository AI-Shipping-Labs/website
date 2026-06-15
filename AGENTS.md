# Agent Notes

## Development Process

- Before continuing development work, read `_docs/PROCESS.md` and follow the issue pipeline documented there.
- Treat "continue where we stopped" as a prompt to check `_docs/PROCESS.md`, inspect the current issue/worktree/process state, and resume the next pipeline step.

## Production Data Access

- Production URL: `https://aishippinglabs.com`.
- Do not assume local files, SQLite, or a remote database tunnel represent production data.
- Agents cannot access production data directly. Use the authenticated production API when checking production users, email logs, SES events, or other live records.
- Do not print API tokens or other secrets in logs, comments, or final responses.
