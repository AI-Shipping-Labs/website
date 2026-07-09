# AI Shipping Labs Plans API Skill

This directory is a downloadable Codex skill for working with the AI Shipping Labs member Plans API.

Use `SKILL.md` with a local agent to update a member's own sprint plan. It is focused on the practical workflow: keep the key local, identify the target plan, fetch current IDs, apply the smallest safe set of edits, sync progress, and verify the final state.

For the full API endpoint surface and request shapes, use the member API docs:

```text
https://aishippinglabs.com/member-api/docs
```

The skill expects the member API key to come from the user, a local `.env` file, or the current process environment as `AI_SHIPPING_LABS_MEMBER_API_KEY`. Do not commit `.env`.

PRs are welcome against `skills/ai-shipping-labs-plans-api/` in the website repository.
