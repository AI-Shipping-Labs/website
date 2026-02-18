# 093 - Background Job Infrastructure

**Status:** pending
**Tags:** `infra`
**GitHub Issue:** [#93](https://github.com/AI-Shipping-Labs/website/issues/93)
**Specs:** 09 (email matcher), 10 (campaign sends), 12 (event reminders), 14 (sync jobs)
**Depends on:** [001-scaffold](001-scaffold.md)
**Blocks:** [082-community-slack](082-community-slack.md), [087-email-campaigns](087-email-campaigns.md), [089-notifications](089-notifications.md), [092-github-content-sync](092-github-content-sync.md)

## Scope

- Set up a task queue (Django-Q, Celery, or management commands + cron — pick one)
- Provide a way to enqueue async jobs from application code
- Provide a way to schedule recurring jobs (hourly, every 15 min, etc.)
- Admin visibility: view queued/running/failed jobs and their results
- Jobs needed by other tasks:
  - **082**: Slack email matcher (hourly)
  - **087**: Email campaign bulk send (on-demand, enqueued from admin)
  - **089**: Event reminder checks (every 15 min)
  - **092**: GitHub content sync (on-demand, triggered by webhook)
  - **082**: Scheduled Slack removal at billing_period_end

## Acceptance Criteria

- [ ] Task queue library installed and configured (Django-Q2, Celery, or management commands + cron)
- [ ] `async_task(func, *args)` function available to enqueue a job from any Django view or service
- [ ] `schedule(func, cron_expression)` function available to register recurring jobs
- [ ] Failed jobs logged with: function name, arguments, error traceback, timestamp
- [ ] Admin interface shows: queued jobs, running jobs, failed jobs with error details, job history
- [ ] Recurring job schedule viewable in admin
- [ ] At least one example job (e.g., a no-op health check) runs end-to-end in tests
- [ ] Worker process startup documented in README or deployment docs
