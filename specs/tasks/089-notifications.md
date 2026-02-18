# 089 - Notifications

**Status:** pending
**Tags:** `community`, `integration`, `frontend`
**GitHub Issue:** [#89](https://github.com/AI-Shipping-Labs/website/issues/89)
**Specs:** 12
**Depends on:** [082-community-slack](082-community-slack.md), [093-background-jobs](093-background-jobs.md), [072-articles](072-articles.md), [074-recordings](074-recordings.md), [075-projects](075-projects.md), [077-downloads](077-downloads.md), [078-course-models-catalog](078-course-models-catalog.md), [083-events](083-events.md), [088-voting](088-voting.md)
**Blocks:** —

## Scope

- Notification model: user FK (nullable for broadcasts), title, body, url, notification_type, read (bool)
- NotificationService.notify(content_type, content_id): creates on-platform notifications for eligible users AND posts to Slack #announcements
- Triggered from admin publish actions for: articles, courses, events, recordings, downloads, polls
- Event reminders: scheduled job every 15 min, checks for events starting in ~24h and ~1h, creates reminder notifications for registered users (deduplicated)
- Bell icon in header: unread count badge (red dot, max "9+")
- Notification dropdown: last 20 notifications, click to mark read and navigate
- "Mark all as read" action
- `/notifications` full page list (paginated)
- Frontend polls `/api/notifications/unread-count` every 60 seconds
- Slack channel posting with Block Kit formatted messages

## Acceptance Criteria

- [ ] Notification model with fields: user FK (nullable for broadcasts), title, body, url, notification_type (new_content/event_reminder/announcement), read (bool, default false), created_at
- [ ] NotificationService.notify(content_type, content_id): creates Notification for each eligible user AND posts to Slack #announcements
- [ ] Triggered from admin publish action for: articles, courses, events, recordings, downloads, polls
- [ ] Event reminders: background job every 15 min checks events starting in ~24h and ~1h; creates reminder notifications for registered users; deduplicated (no duplicate reminder per event + user + interval)
- [ ] Bell icon in site header shows red dot with unread count (max "9+")
- [ ] Clicking bell shows dropdown with last 20 notifications; clicking a notification marks it read and navigates to url
- [ ] "Mark all as read" button in dropdown
- [ ] `GET /notifications` shows full paginated list of notifications
- [ ] Frontend polls `GET /api/notifications/unread-count` every 60 seconds
- [ ] Slack messages formatted with Block Kit (title, description, link button)
