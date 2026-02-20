# 12 - Notifications

## Overview

Notify members when new content is published. Three channels: Slack, on-platform, and optionally Telegram.

## Data Model

```
Notification:
  id: uuid
  user_id: FK -> User | null       # null for broadcast (Slack/Telegram channel posts)
  title: string
  body: string
  url: string                       # link to the content (e.g. "/blog/new-article")
  notification_type: string         # "article", "course", "event", "recording", "download", "poll"
  read: bool                        # default false
  created_at: datetime
```

## Trigger Events

When admin publishes content, the backend creates notifications and posts to channels:

| Trigger | Slack message | On-platform notification | Who receives |
|---|---|---|---|
| Article published | Post to `#announcements` channel: title + excerpt + link | Create Notification for each user where `tier.level >= article.required_level` | Users with access |
| Course published | Post to `#announcements`: title + description + link | Create Notification for eligible users | Users with access |
| Event created (upcoming) | Post to `#announcements`: title + date + link | Create Notification for eligible users | Users with access |
| Event in 24 hours | Post to `#announcements`: reminder with join link | Create Notification for registered users only | Registered users |
| Event in 1 hour | No Slack post (avoid noise) | Create Notification for registered users | Registered users |
| Recording published | Post to `#announcements`: title + link | Create Notification for eligible users | Users with access |
| New download available | Post to `#announcements`: title + link | Create Notification for eligible users | Users with access |
| New poll opened | Post to `#announcements`: title + link to vote | Create Notification for eligible users | Users with access |

## Slack Channel Post

Use Slack Web API `chat.postMessage`:

```json
{
  "channel": "#announcements",
  "text": "New article: Building RAG Pipelines",
  "blocks": [
    {
      "type": "section",
      "text": {"type": "mrkdwn", "text": "*New article:* <https://aishippinglabs.com/blog/building-rag|Building RAG Pipelines>\n\nLearn how to build production-ready RAG pipelines..."}
    }
  ]
}
```

## On-Platform Notification UI

- Bell icon in the site header. Shows unread count badge (red dot with number, max "9+").
- Clicking opens a dropdown panel with notification list, sorted by `created_at` desc.
- Each item: title, short body (truncated to 80 chars), relative time ("2h ago"), unread indicator (bold text / blue dot).
- Clicking a notification: marks it as read, navigates to `notification.url`.
- "Mark all as read" link at the top of the panel.
- Load last 20 notifications. "View all" link goes to `/notifications` (full page list, paginated).

### Polling

- Frontend polls `GET /api/notifications/unread-count` every 60 seconds to update the badge.
- Alternatively, use WebSocket/SSE for real-time. Polling is fine for MVP.

## Telegram (optional, not MVP)

- Create a Telegram channel for public announcements
- Bot posts the same content as Slack announcements via Telegram Bot API `sendMessage`
- No per-user targeting, just a public broadcast channel

## Requirements

- R-NOT-1: Create `notifications` table with schema above.
- R-NOT-2: Implement `NotificationService.notify(content_type, content_id)` that: (a) creates on-platform Notification records for eligible users, (b) posts a formatted message to the Slack `#announcements` channel.
- R-NOT-3: Call `NotificationService.notify()` from the admin publish action for articles, courses, events, recordings, downloads, and polls.
- R-NOT-4: Implement event reminders: a scheduled job runs every 15 minutes, checks for events starting in ~24 hours and ~1 hour, creates reminder notifications for registered users (deduplicated — don't send the same reminder twice).
- R-NOT-5: `GET /api/notifications` returns the current user's notifications, paginated (20/page), sorted by `created_at` desc. Each includes `read` status.
- R-NOT-6: `GET /api/notifications/unread-count` returns `{count: int}` for the badge.
- R-NOT-7: `POST /api/notifications/{id}/read` marks a single notification as read. `POST /api/notifications/read-all` marks all as read.
- R-NOT-8: Frontend polls `/api/notifications/unread-count` every 60 seconds and updates the bell icon badge.
