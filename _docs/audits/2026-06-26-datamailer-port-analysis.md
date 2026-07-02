# Datamailer Port Analysis

Date: 2026-06-26

This audit checks what AI Shipping Labs would need from Datamailer, based on
the current `email_app`, `notifications`, `events`, and Studio campaign flows,
and compares that against the CMP Datamailer integration plan in
`/home/alexey/git/course-management-platform/docs/datamailer-integration.md`.

## Executive summary

AI Shipping Labs can fit the same generic Datamailer direction, but it is a
more demanding client than CMP. CMP mostly needs course workflow emails,
recipient lists, category preferences, campaigns, and a production-path capture
testbed. AI Shipping Labs also needs:

- a campaign product with draft/edit/duplicate/archive/test-send/preview/send
  behavior,
- composable audience targeting over tier, tags, Slack membership, email
  verification, and event registration,
- event lifecycle emails with calendar/iTIP MIME parts,
- per-message engagement and deliverability analytics,
- sender/reply-to selection by email type,
- DB-editable or operator-editable templates and campaign bodies,
- ad hoc recipients that may not be platform users.

The biggest design pressure is audience targeting. CMP's planned recipient-list
tree and transient send lists are enough for course workflow emails, but AI
Shipping Labs campaigns currently target users by an AND of multiple live
attributes. Datamailer either needs a small generic filter model, or AI Shipping
Labs must materialize campaign recipients into transient lists before queueing.

## Current AI Shipping Labs email surfaces

### Transactional template sends

`email_app.services.EmailService.send(user, template_name, context, cc, bcc)`
renders markdown templates from `email_app/email_templates/*.md`, applies
operator overrides from `EmailTemplateOverride`, wraps the result in
`email_app/base_email.html`, sends through SES, and writes `EmailLog`.

Current template slugs include account, payment, event, Slack, Maven, plan, and
welcome flows:

- `welcome`, `welcome_imported`, `welcome_back`
- `basic_welcome`, `premium_welcome`, `cofounder_welcome`, `maven_welcome`
- `email_verification_signup`, `email_verification_subscribe`, and reminders
- `password_reset`, `payment_failed`, `cancellation`
- `community_invite`, `lead_magnet_delivery`
- `event_registration`, `event_reminder`, `event_rescheduled`,
  `event_cancelled`, `post_event_followup`, `event_recording_ready`
- `series_registration`, `series_update`, `series_cancellation`
- `plan_shared`, `workshop_announcement`
- `staff_signup_notification`, `slack_join_notification`,
  `maven_cohort_removal_notification`

Classification is explicit in `email_app/services/email_classification.py`:
transactional emails ignore unsubscribe state; promotional emails are skipped
for globally unsubscribed users. Welcome templates are transactional but use a
dedicated sender address.

### Campaigns

`EmailCampaign` is an operator-authored campaign model with:

- subject and markdown/HTML body,
- `draft`, `sending`, `sent` status,
- archive flag,
- target minimum tier: everyone, Basic+, Main+, Premium,
- include-any tags and exclude-any tags,
- Slack membership filter: any/yes/no,
- verification filter: verified-only by default, or everyone,
- optional `target_event`, which scopes the audience to event registrants and
  then ANDs the other filters on top.

Studio supports campaign create, edit, detail preview, duplicate, test-send to
explicit addresses, delete draft, and queue send. The send task snapshots
eligible user IDs, fans out in batches, renders the campaign once per
recipient with a personalized unsubscribe URL, writes `EmailLog`, and uses a
partial unique constraint on `(campaign, user)` for idempotency.

### Event and calendar emails

Event registration and series emails are not simple HTML sends. They generate
calendar/iTIP payloads and send raw SES MIME messages where the `text/calendar`
part is a `multipart/alternative` sibling, not a normal attachment. This is
required for calendar clients to merge updates and cancellations by UID.

Examples:

- event registration confirmations with `.ics`
- event reschedule and cancellation notices
- series registration/update/cancellation emails
- post-event follow-up
- recording-ready notifications to hosts or staff fallback addresses

Some recipients may not be platform users. `EmailLog.recipient_email` exists
for non-user destinations.

### Notifications and announcements

`NotificationService` creates in-app notifications, posts Slack announcements,
and in some cases sends email:

- workshop announcements are promotional email sends,
- event reminders are transactional and deduped by `EventReminderLog`,
- shared plans send `plan_shared` transactional emails,
- content/course/event/recording/download/poll notifications currently remain
  in-app/Slack unless configured otherwise.

### Deliverability and engagement

AI Shipping Labs currently owns SES event intake:

- `SesEvent` stores raw SNS payloads and classified events.
- Events include permanent/transient/other bounce, complaint, delivery, open,
  click, subscription confirmation, unsubscribe confirmation, and other.
- Incoming bounces/complaints correlate back to `EmailLog` by SES message id.
- `EmailLog` stores first open/click timestamps and counters, bounce details,
  complaint timestamp, and SES message id.
- Permanent bounces and complaints set `User.unsubscribed=True`; soft bounces
  increment `soft_bounce_count` and can eventually mark a permanent bounce.

This is more analytics-heavy than CMP's current documented callback set.

### User email state

Relevant `User` fields include:

- unique email,
- `email_verified`,
- global `unsubscribed`,
- `email_preferences` JSON,
- tier and effective tier overrides,
- `slack_member`,
- `preferred_timezone`,
- contact `tags`,
- `soft_bounce_count`, `bounce_state`, `bounce_recorded_at`,
  `last_bounce_diagnostic`,
- import/source fields.

Unsubscribe is global today. Some email-specific opt-outs live in
`email_preferences`, such as workshop emails.

## Datamailer requirements for AI Shipping Labs

### 1. Generic client model still works

The CMP plan's generic client model is still the right direction. AI Shipping
Labs should be another Datamailer client/audience, not a special case in the
Datamailer core.

Datamailer must allow client-defined:

- category tags,
- contact metadata,
- recipient-list keys,
- templates,
- campaign metadata,
- sender/reply-to rules.

AI-specific ideas such as tier, Slack membership, Maven, event series, and plan
sharing should live in AI Shipping Labs' config, metadata, template variables,
and list keys, not in Datamailer core tables as first-class product concepts.

### 2. Audience targeting needs a decision

AI campaigns need an AND of:

- contact is not globally unsubscribed,
- contact is verified unless campaign says everyone,
- effective tier is at least a selected level,
- include tags overlap,
- exclude tags do not overlap,
- Slack membership is yes/no/any,
- optional event registration membership.

There are two viable approaches.

Option A: Datamailer supports a generic, limited filter model over contact
attributes, tags, and list membership. Example:

```json
{
  "all": [
    {"attr": "tier_level", "gte": 20},
    {"tag_any": ["early-adopter"]},
    {"tag_none": ["bounced"]},
    {"attr": "slack_member", "eq": true},
    {"list": "events:workshop-123:@registered"}
  ]
}
```

Option B: AI Shipping Labs computes the recipient set and uploads a transient
send list before queueing the campaign. Datamailer then snapshots and sends
that list.

Recommendation: start with Option B for the port. It keeps Datamailer generic
and avoids designing a query engine too early. Add Option A later only if
multiple clients need operator-defined filtering inside Datamailer.

This is the main conflict with the CMP plan: CMP's campaign API currently names
`<all>`, a recipient-list key, or tag filters. AI Shipping Labs needs
intersections, numeric tier predicates, boolean Slack predicates, and optional
event list membership.

### 3. Campaign API must be richer than CMP's first use case

The CMP plan already has `PUT /api/campaigns/{external_key}`,
`queue`, `cancel`, `preview`, and `test-send`. AI Shipping Labs also needs:

- campaign draft/edit lifecycle,
- duplicate/copy behavior,
- archive/unarchive or hidden-from-default-list behavior,
- recipient count before send,
- per-recipient send history after send,
- test send to explicit addresses, including addresses that are not contacts,
- preview with the same production renderer,
- immutable recipient snapshot at queue time,
- idempotency per campaign recipient.

If Datamailer owns the campaign object, AI Shipping Labs should stop doing
Django-Q campaign fanout after the port. AI Shipping Labs can keep a local
operator UI that calls Datamailer, but the delivery queue and per-recipient
message history should be Datamailer-owned.

### 4. Templates need markdown and operator overrides

AI Shipping Labs templates are markdown with YAML frontmatter and Django-style
template variables. Operators can override subject/body/footer note in the DB.
Campaigns are also markdown/HTML bodies authored in Studio.

Datamailer needs at least:

- markdown-to-HTML rendering,
- subject and body variables,
- base HTML/text layout,
- template preview with supplied sample context,
- ad hoc campaign content, not only named templates,
- a way to store operator-edited overrides or imported template revisions,
- render parity between preview/testbed and real send.

Open decision: whether Datamailer implements Django-template compatibility, or
AI Shipping Labs rewrites templates to Datamailer's template language during the
port. Because Datamailer is greenfield, compatibility is not required forever,
but the migration plan should include a template conversion pass.

### 5. Calendar/iTIP sending is a hard requirement

Datamailer must support raw MIME or a structured attachment/alternative API
sufficient for event lifecycle emails:

- `text/calendar` as a `multipart/alternative` part,
- `method=REQUEST` and `method=CANCEL`,
- stable calendar UID/sequence behavior supplied by AI Shipping Labs,
- HTML body plus calendar alternative in one message,
- no accidental conversion into a normal attachment for clients that need iTIP.

This is not covered in the CMP plan today. CMP can live with subject/text/html;
AI Shipping Labs cannot.

### 6. Preferences and unsubscribe can converge, but source of truth changes

The CMP target says Datamailer owns preferences and CMP stores none. That still
works for AI Shipping Labs, but it is a real rewrite:

- `User.unsubscribed` cannot remain the authoritative global opt-out after the
  port.
- `User.email_preferences` cannot remain the authoritative category store.
- AI Shipping Labs should proxy preference reads/writes to Datamailer or keep
  a read-only/support mirror updated by callbacks.
- Promotional-vs-transactional classification must move into Datamailer config
  or be passed as category/suppression behavior on each send.
- One-click unsubscribe and `List-Unsubscribe` headers must stay supported for
  promotional sends.

This aligns with the CMP "no backwards compatibility" principle: the port
should rewrite to the target model instead of supporting two preference stores.

### 7. Deliverability callbacks need more event types

CMP currently documents callbacks mainly around unsubscribe, bounce, complaint,
and failure. AI Shipping Labs needs Datamailer to expose or callback:

- delivery,
- open,
- click,
- bounce with type/subtype/diagnostic,
- complaint,
- unsubscribe/resubscribe,
- provider message id,
- Datamailer message id,
- original client/campaign/template/list/event metadata.

AI Shipping Labs may not need to store raw SES/SNS payloads locally after the
port, but it needs enough event history to answer support and operator
questions that `SesEvent` and `EmailLog` answer today.

### 8. Message history is product-facing, not just audit

Datamailer should expose message history by contact and by campaign:

- sent timestamp,
- template/campaign/email type,
- event or content metadata,
- recipient address,
- delivery state,
- opens/clicks counters,
- bounce/complaint fields,
- suppression/skipped reason,
- provider ids.

AI Shipping Labs Studio currently surfaces SES events and campaign engagement.
If Datamailer owns those events, Studio either needs Datamailer-backed views or
local mirrors populated by callbacks.

### 9. Non-user recipients, cc/bcc, reply-to, and sender rules

AI Shipping Labs sends some messages to addresses that are not platform users
and supports `cc`/`bcc` in `EmailService.send`. It also picks From addresses by
email kind and welcome status.

Datamailer needs:

- direct send to an email address without requiring a stored contact,
- optional contact lookup/metadata when the recipient is a known user,
- `cc` and `bcc`,
- per-template or per-category sender selection,
- reply-to configuration,
- internal/staff notification emails that are transactional and have no
  unsubscribe footer.

The CMP plan has direct sends and sender config, but should explicitly cover
cc/bcc/reply-to and non-contact recipients if AI Shipping Labs is in scope.

### 10. Verification footer and timezone formatting need template context

AI Shipping Labs templates conditionally include email-verification calls to
action and format event times in the recipient's preferred timezone.

Datamailer can support this without AI-specific logic if AI Shipping Labs passes:

- `verify_email_url`,
- `email_verified`,
- `preferred_timezone`,
- preformatted datetime strings, or raw datetimes plus a rendering rule.

Recommendation: keep timezone formatting in AI Shipping Labs at first and pass
final strings. That avoids making Datamailer understand every client's timezone
display policy.

### 11. Capture testbed maps very well

The CMP capture-mode plan is exactly what AI Shipping Labs needs. Today AI
Shipping Labs has `SES_ENABLED=False`, which prevents real SES delivery and
returns synthetic ids, but it does not provide a full inspectable render store.

For the port, local/E2E mode should be:

- same AI Shipping Labs code path,
- same Datamailer API request as real delivery,
- Datamailer configured with delivery mode `capture`,
- no real provider send,
- captured subject/text/html/MIME parts/headers,
- captured suppression decisions and unsubscribe/preference behavior,
- queryable by run, message, recipient, campaign, template, and source event.

For calendar emails, the capture UI/API must expose the raw MIME or at least the
calendar part. HTML/text alone is insufficient.

## Conflicts or gaps versus the CMP plan

1. Audience targeting is underspecified for AI Shipping Labs campaigns.
   Recipient-list trees plus simple tag filters are not enough unless AI
   Shipping Labs materializes campaign recipient lists before queueing.

2. Calendar/iTIP emails are missing from the Datamailer target. AI Shipping
   Labs needs MIME alternatives for calendar clients.

3. Engagement analytics are broader than CMP's callback list. AI Shipping Labs
   needs delivery/open/click history in addition to bounce/complaint/failure.

4. Preferences source of truth changes are larger for AI Shipping Labs because
   the app already has `User.unsubscribed` and `email_preferences`. The port
   should rewrite those flows to Datamailer instead of preserving dual writes.

5. Campaign UI ownership needs a decision. Either Datamailer provides enough
   campaign APIs for Studio to remain the UI, or Datamailer ships its own
   operator UI. A half split where both own status/fanout/history will create
   reconciliation bugs.

6. Raw test-send behavior needs direct-address support. AI Shipping Labs test
   sends are not always to existing contacts.

7. Sender/reply-to selection should be explicit. AI Shipping Labs currently has
   transactional, promotional, and welcome senders, plus reply-to behavior.

8. Template migration is not just copy/paste. The current markdown/YAML/Django
   template stack either needs compatibility or a conversion plan.

## Recommended port sequence

1. Implement Datamailer contact upsert and preference proxy for AI Shipping
   Labs, but do not switch delivery yet.

2. Port simple transactional HTML/text template sends first: verification,
   password reset, welcome, payment, plan shared, lead magnet.

3. Add capture-mode E2E tests for those sends and compare rendered output,
   headers, unsubscribe decisions, and preference behavior.

4. Add calendar/iTIP send support, then port event registration, event
   reschedule/cancellation, and series lifecycle emails.

5. Port campaigns using transient materialized recipient lists. Keep Studio as
   the UI initially, but make Datamailer own queueing, snapshots, per-message
   history, and delivery events.

6. Move SES event ownership to Datamailer and replace local `SesEvent` writes
   with Datamailer callbacks or Datamailer-backed Studio views.

7. Remove local authoritative unsubscribe/preference gating from AI Shipping
   Labs once Datamailer is the source of truth.

## Suggested additions to the CMP Datamailer doc

Add Datamailer requirements that are generic but motivated by AI Shipping Labs:

- Direct sends may include `cc`, `bcc`, `reply_to`, and non-contact recipients.
- Message content can include structured MIME parts, especially calendar/iTIP
  alternatives.
- Callback/event history should include delivered/opened/clicked, not only
  bounce/complaint/unsubscribe/failure.
- Campaign recipient sources should support either a materialized transient list
  or a small generic filter/intersection model.
- Campaign APIs should expose draft/edit/archive/duplicate/test-send/preview
  behavior if Datamailer is expected to replace existing client campaign tools.
- Capture mode should store raw MIME/headers/attachments/alternatives in
  addition to subject/text/html.

## Bottom line

Nothing found in AI Shipping Labs invalidates the planned generic Datamailer
architecture. The plan should stay generic and multi-client.

The main additions are: materialized campaign audiences or generic audience
filters, calendar/iTIP MIME support, richer event callbacks, non-contact
recipient support, and a campaign/message-history API robust enough for Studio.
