# 087 - Email Campaigns

**Status:** pending
**Tags:** `email`, `admin`
**GitHub Issue:** [#87](https://github.com/AI-Shipping-Labs/website/issues/87)
**Specs:** 10 (campaigns section)
**Depends on:** [085-email-ses](085-email-ses.md), [093-background-jobs](093-background-jobs.md)
**Blocks:** —

## Scope

- EmailCampaign model: subject, body (markdown/HTML), target_min_level (0-3), status (draft/sending/sent), sent_at, sent_count
- Admin `/admin/emails/new`: create campaign with subject, body (markdown editor with preview), target audience dropdown
- Estimated recipient count preview
- Send test email to admin
- Send campaign: background job queries eligible users (tier level >= target, not unsubscribed, email verified), sends via SES with rate limiting, creates EmailLog per send
- Campaign status tracking: draft → sending → sent
- Admin campaign list with sent counts

## Acceptance Criteria

- [ ] EmailCampaign model with fields: subject, body (markdown), target_min_level (0/10/20/30), status (draft/sending/sent), sent_at, sent_count, created_at
- [ ] Admin `/admin/emails/new`: create campaign with subject, body (markdown editor with preview), target audience dropdown
- [ ] Estimated recipient count shown based on selected target_min_level
- [ ] "Send test email" button sends campaign to admin's email only
- [ ] "Send campaign" enqueues background job; job queries users where tier.level >= target_min_level AND unsubscribed = false AND email_verified = true
- [ ] Background job sends emails via EmailService with rate limiting (respects SES sending rate)
- [ ] Creates EmailLog per send
- [ ] Campaign status transitions: draft → sending → sent
- [ ] sent_count updated as emails are sent
- [ ] Admin `/admin/emails` lists all campaigns with subject, status, sent_count, sent_at
