# Expand/contract release checklist

Use this checklist for issue #1266 and any later rolling deployment where old
and new web/worker images overlap. Production baseline for this release is SHA
`524153b6f43a03a392f33590be83ff3dcc04a330`, immutable tag
`20260711-201520-524153b`. The unmodified target `1e5ab5b5` is not promotable.

## Migration inventory

| Migration | R1 classification and treatment |
| --- | --- |
| `accounts.0023_tieroverride_source` | Expand; persistent empty-string database default marks legacy writes. |
| `accounts.0023_user_verification_resend_claim` | Expand; nullable fields are old-image safe. |
| `accounts.0024_merge_*` | Graph merge only. |
| `accounts.0025_alter_user_signup_source` | Choice/state expansion only; no database restriction. |
| `community.0015_alter_communityauditlog_action` | Choice/state expansion only. |
| `community.0016_*` | Expand `last_event_at` only; retain the production-compatible non-null/CASCADE host invariant. |
| `community.0017_unmatchedbookedcall` | Additive R1 staging for unmatched calls; already-null dev rows move here non-lossily before host nullability is tightened. |
| `content.0054_download_private_storage` | Expand/new table; persistent defaults on three fields used by legacy inserts. |
| `crm.0008_slack_ingest_lease_and_refresh_count` | Expand/defaults; defer partial running-ingest uniqueness and destructive duplicate terminalization. |
| `email_app.0019_emaillog_dedupe_key` | Expand; nullable unique key is legacy safe. |
| `events.0040_event_calendar_uid` | Expand/backfill; retain nullable unique UID so multiple legacy inserts receive `NULL`. |
| `events.0041_event_recording_upload_enqueued_at` | Expand; nullable marker is legacy safe. |
| `events.0042_*` | Expand/new table; retain nullable host access version for legacy inserts. |
| `integrations.0024_mavenenrollmentevent_*` | Expand/defaults; DB lifecycle default `legacy`; defer partial active-identity uniqueness. |
| `integrations.0025_webhooklog_delivery_state` | Expand/index; persistent attempt/error defaults and nullable remaining fields. |
| `payments.0009_*` | Choice expansion and new tables only. |
| `plans.0029_sprint_*` | Expand; persistent empty-string defaults for three legacy-omitted fields. |
| `questionnaires.0007_onboarding_turn_attempt` | Expand/new table; persistent conversation-version default. |
| `triggers.0002_secure_delivery_state` | Expand/backfill/new tables; retain hidden plaintext `secret`, dual-write it, and defer physical removal. |

`triggers.0003_r1_expand_reconciliation` is the convergence migration for
development databases that applied the original target migration bytes. It
restores the compatibility column/defaults/nullability, removes deferred
constraints, stages unmatched Calendly rows outside `BookedCall`, and is forward-only.

## Work-vocabulary manifest

R1 must delete and must not register these R2-only schedules:

- `cleanup-calendly-webhook-logs`
- `retry-calendly-webhooks`
- `resume-webhook-deliveries`
- `redact-maven-enrollment-pii`
- `retry-maven-enrollment-steps`
- `purge-plan-sprints-raw-text`
- `onboarding-staff-notification-recovery`

R1 direct-producer rules:

- Trigger emission publishes the legacy `triggers.tasks.deliver_webhook`
  arguments/retry vocabulary; it does not create or wake durable delivery jobs.
- Completed onboarding invokes the legacy synchronous staff notification; it
  does not enqueue the R2 outbox task.
- Existing event cancellation/reschedule and Zoom recording-upload task names
  and arguments remain understood by the production worker and may continue.
- `website.release_phase` is compile-time artifact state. Never replace the R1
  gate with an IntegrationSetting or environment/operator toggle.

## R1 — expand and compatibility

- [ ] `makemigrations --check --dry-run`, system checks, OpenAPI drift,
  collectstatic, four Django shards, PostgreSQL migration compatibility, and
  Playwright Core pass.
- [x] Frozen 24-leaf `524153b6` migration matrix passes historical reads and
  writes, including duplicate running Slack ingests and multiple Maven rows.
- [x] Original-`1e5ab5b5` physical-drift fixture converges through the forward
  reconciliation migration.
- [x] No migration removes `secret`, tightens deferred nullable fields, or adds
  the deferred CRM/Maven constraints.
- [x] Web rolls first, serves the exact immutable `/ping` tag, then worker
  rolls; a web failure produces an executable prior-task-definition recovery
  command and no worker update.
- [x] Seven R2 schedules are absent and both incompatible direct producers use
  their production-compatible behavior.
- [ ] Record dev tag/run, tester report, PM acceptance, production run/tag, and
  old-task drain/soak evidence in issue #1266.
- [ ] During R1, rollback only changes both images to
  `20260711-201520-524153b`; never reverse migrations. Redeploy the exact R1
  image to forward-recover and run reconciliation before worker consumption.

## R2 — activate without contract DDL

- [ ] Confirm R1 web and worker tasks fully replaced `524153b6` and old queue
  work drained.
- [ ] Build a separately reviewed immutable artifact that enables the seven
  schedules and durable trigger/onboarding producers.
- [ ] Switch trigger runtime to encrypted-only and stop mapping/shadow-writing
  compatibility columns, while leaving their physical columns/defaults intact.
- [ ] Reconcile every compatibility/default row and attach or explicitly retain
  every staged unmatched call before enabling producers.
- [ ] Soak representative minute/five-minute/daily tasks and webhook,
  onboarding, Maven, Calendly, download, and event flows.
- [ ] R2 rollback target is R1, never `524153b6`.

## R3 — contract

- [ ] Confirm all R2 tasks run code that ignores compatibility columns and
  temporary database defaults/nullability.
- [ ] Fail closed if plaintext/envelope/UID/access-version/Maven compatibility rows,
  overlapping Slack ingests, or compatibility backlogs remain.
- [ ] In a separately reviewed artifact, remove plaintext trigger `secret`,
  temporary database defaults, unmatched-call staging, and deferred
  nullability; add final CRM/Maven and UID constraints.
- [ ] Prove the R2 artifact runs against the contracted schema before R3 is
  eligible for production.
- [ ] R3 rollback target is R2; never reverse destructive data migrations and
  never select R1 or `524153b6` after contract DDL.
