# AI Shipping Labs Member API Skill

This directory is a downloadable Codex skill catalog for the AI Shipping Labs
member API — the API a member uses to act on their own data with a
member-owned API key.

`SKILL.md` is the catalog: it holds the shared auth and key setup and points to
one sub-skill per API family. Load the catalog first, then follow the family
that matches the task:

- `plans/SKILL.md` — the member Plans API: list, fetch, download, and edit the
  member's own sprint plans (weeks, checkpoints, deliverables, next steps,
  resources, week notes, progress).
- `books/SKILL.md` — the member Book Club API: per-chapter read state, the
  member's own chapter notes, reading progress for a book, and the reader
  profile.

For the full API endpoint surface and request shapes across every family, use
the member API docs:

```text
https://aishippinglabs.com/member-api/docs
```

The skill expects the member API key to come from the user, a local `.env`
file, or the current process environment as `AI_SHIPPING_LABS_MEMBER_API_KEY`.
Do not commit `.env`. Member API keys act only on the key owner's own data and
cannot reach Studio, staff, CRM, onboarding, or other members' data.

PRs are welcome against `skills/ai-shipping-labs-member-api/` in the website repository.
