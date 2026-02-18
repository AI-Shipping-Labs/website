# 085 - Email Service (Amazon SES)

**Status:** pending
**Tags:** `email`, `integration`
**GitHub Issue:** [#85](https://github.com/AI-Shipping-Labs/website/issues/85)
**Specs:** 10
**Depends on:** [067-user-auth](067-user-auth.md)
**Blocks:** [086-newsletter](086-newsletter.md), [087-email-campaigns](087-email-campaigns.md)

## Scope

- EmailService with send(user, template_name, context) method
- Amazon SES v2 SendEmail integration
- Transactional email templates stored as markdown, rendered with context variables
- All transactional emails: verification, welcome, payment failed, cancellation, community invite, lead magnet
- SES bounce/complaint webhook `POST /api/webhooks/ses`: on hard bounce or complaint, set user.unsubscribed = true
- EmailLog model: campaign FK (nullable), user FK, email_type, sent_at, ses_message_id
- Email template with header, footer, unsubscribe link
- SES domain identity setup for aishippinglabs.com (SPF, DKIM, DMARC)

## Acceptance Criteria

- [ ] EmailService with send(user, template_name, context) method that sends via Amazon SES v2 SendEmail API
- [ ] Transactional email templates stored as markdown files, rendered with context variables
- [ ] Templates defined for: welcome, payment_failed, cancellation, community_invite, lead_magnet_delivery, event_reminder
- [ ] `POST /api/webhooks/ses` validates SES notification signature
- [ ] On hard bounce or complaint notification: sets user.unsubscribed = true
- [ ] EmailLog model with fields: campaign FK (nullable), user FK, email_type, sent_at, ses_message_id
- [ ] Every email send creates an EmailLog record
- [ ] All emails include site header, footer, and one-click unsubscribe link
- [ ] `[HUMAN]` Sending a test email and verifying it arrives with correct formatting
