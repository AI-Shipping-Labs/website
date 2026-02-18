# 10 - Email

## Overview

Newsletter signup, tier-granular sending via Amazon SES, and lead magnet delivery.

## Data Model

```
User (email-related fields):
  email: string (unique)
  email_verified: bool
  unsubscribed: bool              # true = user opted out of all emails
  email_preferences: jsonb        # {newsletter: true, events: true, courses: true} — future use

EmailCampaign:
  id: uuid
  subject: string
  body: text                      # markdown or HTML
  target_min_level: int           # 0 = everyone (incl free), 1 = Basic+, 2 = Main+, 3 = Premium
  status: enum                    # "draft", "sending", "sent"
  sent_at: datetime | null
  sent_count: int                 # number of recipients
  created_at: datetime

EmailLog:
  id: uuid
  campaign_id: FK -> EmailCampaign | null  # null for transactional emails
  user_id: FK -> User
  email_type: string              # "campaign", "welcome", "payment_failed", "community_invite", etc.
  sent_at: datetime
  ses_message_id: string          # for tracking delivery
```

## Newsletter Signup

### Signup Form

- Appears in: site footer, dedicated `/subscribe` page, article CTAs, lead magnet forms
- Fields: email address only. No name required.
- On submit: `POST /api/subscribe` with `{email}`
  1. If email already exists in `users` table: return success (idempotent, don't reveal whether email exists)
  2. If new: create user with `tier_slug = "free"`, `email_verified = false`
  3. Send verification email with a one-time link: `GET /api/verify-email?token={jwt}`
  4. On verification: set `email_verified = true`
  5. Return success message: "Check your email to confirm your subscription"

### Unsubscribe

- Every email includes a footer with an unsubscribe link: `GET /api/unsubscribe?token={jwt}`
- Clicking sets `user.unsubscribed = true`
- Unsubscribed users are excluded from all campaign sends
- The `/account` page (for logged-in users) has a toggle to re-subscribe

## Sending Campaigns

### Admin Flow

1. Admin goes to `/admin/emails/new`
2. Fills in: subject, body (markdown editor with preview), target audience dropdown:
   - "Everyone (including free)" → `target_min_level = 0`
   - "Basic and above" → `target_min_level = 1`
   - "Main and above" → `target_min_level = 2`
   - "Premium only" → `target_min_level = 3`
3. Preview: admin sees estimated recipient count and can send a test email to themselves
4. Send: creates `EmailCampaign` with `status = "sending"`, enqueues a background job

### Send Job

1. Query users where `tier.level >= campaign.target_min_level` AND `unsubscribed = false` AND `email_verified = true`
2. For each user, send via Amazon SES:
   - From: `community@aishippinglabs.com`
   - Subject: campaign subject
   - Body: render markdown to HTML, wrap in email template with header/footer/unsubscribe link
3. Create `EmailLog` record for each send
4. After all sent: set `campaign.status = "sent"`, `campaign.sent_at = now()`, `campaign.sent_count = count`

### Sending via Amazon SES

- Use SES v2 `SendEmail` API
- Configure SES domain identity for `aishippinglabs.com` (SPF, DKIM, DMARC)
- Rate limit: SES has sending limits. Batch sends with 50ms delay between emails (adjust based on SES quota).
- Handle bounces and complaints via SES SNS notifications → `POST /api/webhooks/ses`. On hard bounce or complaint: set `user.unsubscribed = true`.

## Lead Magnet Flow

1. User sees a download CTA (in article or on `/downloads`) for a resource with `required_level = 0`
2. If not logged in: CTA shows an email input field + "Get free access" button
3. On submit: `POST /api/subscribe` (same as newsletter signup) with an additional `redirect_to` param: the download URL
4. Verification email includes the download link: "Confirm your email and get your download: [link]"
5. On verification: `email_verified = true`, redirect to `GET /api/downloads/{slug}/file` which streams the file

## Transactional Emails

Sent immediately (not via campaigns) using SES:

| Trigger | Subject | Content |
|---|---|---|
| Newsletter signup | "Confirm your email" | Verification link |
| Successful purchase | "Welcome to {tier_name}!" | Tier details, next steps, Slack invite if Main+ |
| Payment failed | "Payment issue with your membership" | Link to update payment method |
| Cancellation confirmed | "Your membership has been cancelled" | Access-until date, re-subscribe link |
| Community invite | "Welcome to the community!" | Slack workspace join link |
| Lead magnet | "Your download is ready" | Verification + download link |

## Requirements

- R-EML-1: Create `email_campaigns` and `email_logs` tables with schemas above. Add `unsubscribed` and `email_verified` boolean fields to `users` table.
- R-EML-2: Implement `POST /api/subscribe` that creates a free user (or is idempotent for existing), sends verification email. Supports optional `redirect_to` param for lead magnet flow.
- R-EML-3: Implement `GET /api/verify-email?token={jwt}` that sets `email_verified = true`. Token is a JWT containing `user_id`, expires in 24 hours.
- R-EML-4: Implement `GET /api/unsubscribe?token={jwt}` that sets `unsubscribed = true`. Token is a JWT containing `user_id`, does not expire.
- R-EML-5: Implement campaign send job: query eligible users, send via SES with rate limiting, log each send, update campaign status.
- R-EML-6: Admin endpoints: `POST /api/admin/emails` (create campaign), `POST /api/admin/emails/{id}/test` (send test to admin), `POST /api/admin/emails/{id}/send` (enqueue send job), `GET /api/admin/emails` (list campaigns with sent_count).
- R-EML-7: Implement `POST /api/webhooks/ses` to handle bounce and complaint SNS notifications. On hard bounce or complaint, set `user.unsubscribed = true`.
- R-EML-8: Implement transactional email sending as a service: `EmailService.send(user, template_name, context)`. Templates are stored as markdown files rendered with context variables.
- R-EML-9: Configure SES domain identity for `aishippinglabs.com` with SPF, DKIM, DMARC records.
