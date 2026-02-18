# 088 - Voting and Polls

**Status:** pending
**Tags:** `community`, `frontend`
**GitHub Issue:** [#88](https://github.com/AI-Shipping-Labs/website/issues/88)
**Specs:** 11
**Depends on:** [071-access-control](071-access-control.md)
**Blocks:** —

## Scope

- Poll model: title, description, poll_type (topic/course), required_level (auto-set: 2 for topic, 3 for course), status (open/closed), allow_proposals, max_votes_per_user, closes_at
- PollOption model: poll FK, title, description, proposed_by (user FK, nullable)
- PollVote model: poll FK, option FK, user FK (unique together)
- `/vote` page: lists open polls user has access to
- `/vote/{id}` detail: options sorted by vote count, vote/unvote toggle, max votes enforced, proposal form if allowed, results shown when closed
- Admin CRUD: create polls with pre-defined options, edit, close, delete

## Acceptance Criteria

- [ ] Poll model with fields: title, description, poll_type (topic/course), required_level (auto-set: 20 for topic, 30 for course), status (open/closed), allow_proposals (bool), max_votes_per_user, closes_at (nullable), created_at
- [ ] PollOption model: poll FK, title, description, proposed_by (user FK, nullable)
- [ ] PollVote model: poll FK, option FK, user FK; unique together (poll, user, option)
- [ ] `GET /vote` shows open polls the user has access to (tier.level >= required_level)
- [ ] `GET /vote/{id}`: options sorted by vote count descending; vote/unvote toggle per option
- [ ] max_votes_per_user enforced — API returns 400 if user tries to exceed limit
- [ ] If allow_proposals = true, authenticated users can submit new options via form
- [ ] Closed polls (status = closed or closes_at in the past) show read-only results
- [ ] Admin can create polls with pre-defined options, set allow_proposals, edit, close, and delete
