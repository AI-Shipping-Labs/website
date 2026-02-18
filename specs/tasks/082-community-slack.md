# 082 - Community (Slack Integration)

**Status:** pending
**Tags:** `community`, `integration`
**GitHub Issue:** [#82](https://github.com/AI-Shipping-Labs/website/issues/82)
**Specs:** 09
**Depends on:** [069-stripe-payments](069-stripe-payments.md), [093-background-jobs](093-background-jobs.md)
**Blocks:** [089-notifications](089-notifications.md) (for Slack announcements)

## Scope

- CommunityService with invite/remove/reactivate methods (behind abstract interface for future platform swaps)
- Approach B (channel-based): bot adds/removes users from private community channels
- On purchase (Main+): look up Slack user by email, add to channels; if not found, send invite email
- On cancellation/downgrade below Main: schedule removal at billing_period_end
- On re-subscribe: re-add to channels
- Email matcher background job: hourly, matches unlinked community members by email
- Community audit log table: user_id, action, timestamp, details
- Configuration: SLACK_BOT_TOKEN, SLACK_COMMUNITY_CHANNEL_IDS

## Acceptance Criteria

- [ ] CommunityService with invite(user), remove(user), reactivate(user) methods behind an abstract interface (swappable for future platforms)
- [ ] On checkout.session.completed for Main+ tier: looks up Slack user by email, adds to community channels; if not found, sends invite email
- [ ] On downgrade below Main or cancellation: schedules removal at billing_period_end via background job
- [ ] On re-subscribe (Main+): re-adds user to community channels
- [ ] Email matcher background job (hourly): finds users with slack_user_id = null, queries Slack API by email, links matches
- [ ] CommunityAuditLog model with fields: user FK, action (invite/remove/reactivate/link), timestamp, details (text)
- [ ] All Slack API actions logged to CommunityAuditLog
- [ ] Configuration via env vars: SLACK_BOT_TOKEN, SLACK_COMMUNITY_CHANNEL_IDS
