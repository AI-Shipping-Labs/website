# Expand/contract release checklist

Use this checklist for issue #1266 and any later rolling deployment where old
and new web/worker images overlap. The current production baseline is exact SHA
`dc07564604f3b2e329a19ab4e11375e6c7813480`, immutable tag
`20260716-162837-dc07564`. The post-R1 starting target was exact SHA
`cb4eb3d36e496fdca2e0072cabb3849ff1f1f388`; its original migration bytes are
not promotable. The earlier `524153b6` floor remains covered by the same serial
matrix rather than being discarded.

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
| `community.0018_questionnaire_response_audit_actions` | Choice/state expansion only; no database choice constraint. Historical values and new review values remain readable. |
| `content.0055_workshop_preview_token` + `0056` | Keep the unique token physically nullable for R1 overlap. Current creates get UUIDs, legacy `NULL` rows are non-previewable, and reconciliation assigns distinct tokens idempotently. |
| `email_app.0020_emaillog_recipient_subject_snapshots` + `0021` | Keep non-null `subject` with persistent PostgreSQL `''` default. Recover authoritative campaign subjects; retain explicit empty sentinels when no subject can be derived. |
| `integrations.0026_synclog_observability_indexes` + `0027` | Add both exact observability indexes concurrently on PostgreSQL and retry with `IF NOT EXISTS`; retain portable SQLite migration support. |
| `questionnaires.0008_response_review_queue` | Nullable review columns plus checks/index. Old-image submissions with omitted review fields enter the pending queue safely. |

`triggers.0003_r1_expand_reconciliation` is the convergence migration for
development databases that applied the original target migration bytes. It
restores the compatibility column/defaults/nullability, removes deferred
constraints, stages unmatched Calendly rows outside `BookedCall`, and is forward-only.
The post-R1 `0056`/`0021`/`0027` migrations likewise converge databases that
already applied the original `cb4eb3d3` bytes. Startup reconciliation repairs
workshop/email sentinels written by an overlapping or rolled-back R1 image.

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
  `20260716-162837-dc07564`; never reverse migrations. Redeploy the exact
  accepted candidate
  image to forward-recover and run reconciliation before worker consumption.

### Promotion evidence record

Record all of the following in the release issue before declaring promotion
complete: candidate source SHA; immutable image tag and digest; Deploy Dev run;
all four Django shard results; PostgreSQL 16 matrix and exact-image recovery
rehearsal; Playwright Core; exact-candidate scheduled full Playwright run; dev
`/ping`; Manual Production Deployment run; web and worker task revisions and
readiness; production `/ping`; and the `.prod-versions` bookkeeping commit.

The recovery boundary is image-only. Keep the expanded schema, restore both
services to exact `20260716-162837-dc07564` if necessary, allow its compatible
writes, then redeploy the exact candidate and run reconciliation. Never reverse
migrations, substitute a predeploy task for overlap proof, use a database
tunnel/manual SQL, or require direct provider-console access.

The Deploy Dev exact-R1 image rehearsal is intentionally R1-scoped. Its script
checks the candidate's compile-time phase before pulling the old image and
self-skips once `R1_EXPAND_COMPATIBILITY` becomes false. Remove the workflow
step and `R1_PRODUCTION_TAG` in the separately reviewed R2 activation commit;
do not carry the dc075646 artifact dependency beyond its rollback window.

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
