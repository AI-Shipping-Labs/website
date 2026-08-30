# Adversarial Code Audit — 2026-08-30

## Executive summary

Five independent `gpt-5.6-sol` reviewers at `xhigh` reasoning audited the
current `main` tree from different angles: backend correctness and security,
performance and data access, frontend behavior and accessibility,
architecture and duplication, and tests and operations. The audit examined
commit `0972df7e3256a46c04af6563b3509091faee2a53`.

After consolidating overlapping reports, the audit found 48 actionable items.

GitHub tracking: [issue #1546](https://github.com/AI-Shipping-Labs/website/issues/1546)
links every finding to its implementation issue. Findings C-01 through M-18
are issues #1499 through #1532, M-19 reuses existing issue #1384, and M-20
through L-05 are issues #1533 through #1545.

| Severity | Count |
|---|---:|
| Critical | 1 |
| High | 15 |
| Medium | 27 |
| Low | 5 |

The most urgent risks are campaign email idempotency, content-sync symlink
traversal, credential lifecycle gaps, reusable password-reset links, worker
memory exhaustion during recording uploads, misleading event-registration
success state, privacy-deletion overmatching, and gaps in the deployment
pipeline.

No production data was accessed. No full Django or Playwright suite was run.
The repository's mandatory Ruff profile, Django system check, and migration
drift check passed. The broader advisory Ruff profile reported 997 items,
including 209 over-complex functions and 118 broad exception catches; those
counts are diagnostic context, not 997 additional findings.

## Severity definitions

| Severity | Meaning |
|---|---|
| Critical | Can cause broad irreversible external side effects or a severe production incident through an ordinary retry or concurrency path. |
| High | Concrete security, privacy, data-integrity, availability, or deployment risk with meaningful impact. |
| Medium | Confirmed correctness, performance, accessibility, recovery, or maintainability problem with bounded impact. |
| Low | Cleanup or simplification with modest direct impact. |

## Critical finding

### C-01 — Campaign idempotency is recorded after the email side effect

References: `studio/views/campaigns.py:808-829`,
`email_app/tasks/send_campaign.py:116-148`,
`email_app/tasks/send_campaign.py:252-313`, and
`email_app/models/email_log.py:147-158`.

The Studio action checks `draft` and enqueues work without atomically claiming
the campaign. Two submissions can enqueue two parent tasks, both of which can
observe `draft`. Each batch sends through SES before creating the unique
`EmailLog`. The uniqueness constraint prevents a second log row only after a
duplicate external send has already happened. A worker crash after SES accepts
the message but before the log insert creates the same ambiguity on retry.

Impact: duplicate mass email, sender-reputation damage, and unsubscribe or
compliance consequences.

Remediation: atomically claim the campaign before enqueueing, create durable
per-recipient delivery records before calling SES, claim those records with
explicit delivery states, and define how ambiguous sends are reconciled.

## High-severity findings

### H-01 — Content-repository symlinks can expose worker files through the CDN

References: `integrations/services/github_sync/orchestration.py:294-312`,
`integrations/services/github_sync/media.py:145-153`, and
`integrations/services/github_sync/media.py:253-280`.

Image selection trusts the filename extension and follows file symlinks during
hashing and S3 upload. A malicious content commit can name a symlink
`secrets.png` while pointing it at `/proc/self/environ`, an application `.env`,
or another readable worker file. The target bytes can then be uploaded to the
public content bucket.

Remediation: reject symlinks and non-regular files using `lstat`, enforce
resolved-path containment within the checkout, and open files with
`O_NOFOLLOW`. Apply the boundary to every content parser, not only images.

### H-02 — Account merge transfers live API credentials to the canonical user

References: `accounts/services/account_merge.py:543-581`,
`accounts/services/account_merge.py:824-854`,
`accounts/models/member_api_key.py:47-58`,
`accounts/models/member_api_key.py:104-124`, and
`accounts/models/token.py:44-59`.

The generic reverse-relation merge repoints `MemberAPIKey` and `Token` rows.
A key issued to the secondary user remains valid but authenticates as the
canonical user after the merge.

Remediation: treat credentials as non-transferable authentication state and
revoke or delete all secondary-user credentials inside the merge transaction.
Add a test proving old plaintext credentials fail after a merge.

### H-03 — Password-reset links remain reusable after a successful reset

References: `accounts/utils/tokens.py:13-37`,
`accounts/views/auth.py:163-177`, `accounts/views/auth.py:717-748`, and
`accounts/tests/test_email_auth.py:1034-1043`.

The reset JWT contains a user ID, action, and expiry but is not tied to password
state and has no single-use identifier. A copied link remains usable for its
full one-hour lifetime even after the owner successfully resets the password.

Remediation: use Django's `PasswordResetTokenGenerator` or atomically consume a
persisted hashed `jti`. Test replay after a successful reset.

### H-04 — Deactivated users retain API access

References: `accounts/utils/user_checks.py:6-15`, `accounts/auth.py:56-113`,
`accounts/auth.py:167-204`, `accounts/models/token.py:143-153`, and
`accounts/models/member_api_key.py:104-124`.

Credential authentication checks hashes and revocation state but not
`user.is_active`. Staff authorization checks `is_staff` and authentication but
also omits `is_active`. Existing staff tokens and member keys therefore remain
usable after account deactivation.

Remediation: require `user__is_active=True` at credential lookup and retain a
defensive active-user check in decorators. Revoke credentials during
deactivation and deletion.

### H-05 — Database-backed cache hits add many SQL queries to rendered requests

References: `website/settings.py:672-686`, `integrations/config.py:72-81`,
`integrations/config.py:278-319`, `website/context_processors.py:377-436`,
`integrations/middleware.py:69-138`, and
`content/nav_availability.py:19-125`.

The shared cache is Django `DatabaseCache`. Every shared-cache hit is therefore
SQL, and every warm `get_config()` call checks a database-backed invalidation
stamp. A local homepage request executed 36 queries, 20 against
`django_q_cache`, plus an `IntegrationSetting` read. The exact latency is
environment-dependent, but the query amplification is inherent.

Remediation: use Redis or Memcached for high-frequency shared state, memoize
configuration per request, batch public settings, and apply a bounded local
TTL to invalidation-stamp checks.

### H-06 — Zoom recordings can exhaust workers and become permanently stuck

References: `jobs/tasks/recording_upload.py:24-85`,
`jobs/tasks/recording_upload.py:147-166`, `jobs/tasks/recordings_s3.py:53-63`,
`website/settings.py:697-703`, and
`integrations/views/zoom_webhook.py:139-183`.

The downloader stores all 8 KiB chunks in a list and joins them into a second
contiguous buffer before wrapping the result in `BytesIO`. A 1 GB recording can
temporarily consume about 2 GB per worker. The HTTP read timeout is 600 seconds
while the queue task timeout is 300 seconds. The webhook sets
`recording_upload_enqueued_at`, but no production recovery path clears or
reclaims a failed marker.

Remediation: stream to a bounded temporary file or multipart S3 upload, align
network and task timeouts, close responses explicitly, and persist recoverable
upload states with expired-claim recovery.

### H-07 — Campaign failures and audience drift can leave campaigns in `sending`

References: `email_app/tasks/send_campaign.py:135-148`,
`email_app/tasks/send_campaign.py:317-323`, and
`email_app/tasks/send_campaign.py:348-389`.

The parent snapshots recipient IDs, but completion later recomputes the current
dynamic eligible audience. Per-recipient `EmailServiceError` is logged and
swallowed, so the queue regards the batch as successful and does not retry it.
A failed recipient or a newly eligible user can leave the campaign permanently
in `sending`; users becoming ineligible can shrink the target silently.

Remediation: persist the original audience and per-recipient outcome, reconcile
only that snapshot, add bounded retries, and provide a terminal
`partially_failed` state.

### H-08 — Member event serialization performs registration-state N+1 queries

References: `member_api/views/events.py:176-187`,
`member_api/views/events.py:250-283`,
`member_api/serializers/events.py:29-41`, and
`member_api/serializers/events.py:88-95`.

Serialization performs per-event `EventRegistration.exists()` calls and, for
series, additional series-registration and opt-out queries. A local 19-event
page executed 19 event-registration and eight series-registration existence
queries. A 20-item all-series page can add up to 60 existence queries.

Remediation: annotate the request-user booleans with `Exists`, or bulk-fetch
the three relevant ID sets and pass them into the serializer.

### H-09 — Event registration leaks email addresses in URLs and trusts spoofable state

References: `static/js/events/event_detail.js:174-180`,
`events/views/pages.py:781-805`,
`templates/events/_event_registration_card.html:47-80`, and
`events/tests/test_events.py:2260-2275`.

Anonymous registration redirects to a URL containing `registered=<email>` and
`account_created=1`. The page trusts those arbitrary query parameters to render
success, the supplied email, verification copy, calendar links, and management
UI. Any visitor can spoof the success state, while real email addresses remain
in browser history, access logs, copied links, screenshots, and potential
referrer surfaces.

Remediation: use server-side flash/session state or a signed opaque token bound
to the event and completed registration, then redirect to a clean URL.

### H-10 — Privacy deletion can redact unrelated webhook records

References: `accounts/services/privacy.py:308-317`,
`accounts/services/privacy.py:1703-1757`, and
`accounts/services/privacy.py:1770-1774`.

Deletion treats names, including common first names, as identifying substrings,
scans every non-empty payment and Calendly payload, and recursively replaces
matching text anywhere in JSON. Deleting a member named `Alex` can therefore
alter unrelated records containing that text. All scans and rewrites occur
inside the account-deletion transaction.

Remediation: match stable identifiers only in known provider fields, persist
and index correlation IDs during ingestion, and scrub known JSON paths rather
than applying unrestricted substring replacement.

### H-11 — `docker-compose.yml` service commands are ignored

References: `Dockerfile:43-48`, `entrypoint.sh:1-12`,
`scripts/entrypoint_init.py:487-526`, and
`docker-compose.yml:29-33`, `docker-compose.yml:48-51`,
`docker-compose.yml:62`, and `docker-compose.yml:75`.

Compose appends each service `command` as entrypoint arguments, but
`entrypoint.sh` ignores its arguments and always executes
`python -m scripts.entrypoint_init`. Without the expected boot environment,
setup, web, worker, and watcher services fall into the same worker path. Setup
does not migrate or seed, web does not start Gunicorn, and watcher does not
watch.

Remediation: execute `"$@"` when arguments are supplied, or configure explicit
boot modes per service and deliberately bypass the entrypoint for setup and
watcher. Add a real container-dispatch smoke test.

### H-12 — Main-push deployment does not run the blocking Ruff gate

References: `_docs/PROCESS.md:125-141`, `.github/workflows/ci.yml:3-5`,
`.github/workflows/ci.yml:55-60`, and
`.github/workflows/deploy-dev.yml:11-150`.

The repository deliberately uses local merges and direct pushes rather than
pull requests. Ruff runs only in the pull-request workflow, while `Deploy Dev`
is the actual main-push gate and does not run `ruff check`. Errors caught by the
mandatory profile can therefore deploy when no test imports the affected path.

Remediation: add `uv run ruff check .` to the one-time `Deploy Dev` checks job
and add a workflow contract test.

### H-13 — The documented 85% coverage gate runs in no CI workflow

References: `_docs/testing-guidelines.md:655-676`, `Makefile:100-104`, and
`tests/test_coverage_config.py:66-67`.

Documentation says coverage is CI-only and must pass, but no workflow invokes
the target. The existing test checks only that the configured number equals
85, so it passes even if actual coverage is far lower.

Remediation: collect coverage across deterministic Django shards, combine the
artifacts, and enforce `coverage report --fail-under=85`. Add a workflow-wiring
test rather than only pinning the constant.

### H-14 — Concurrent manual production deploys are not serialized

References: `.github/workflows/deploy-prod.yml:1-21`,
`.github/workflows/deploy-prod.yml:89-147`, and
`deploy/deploy_dev.sh:639-680`.

The production workflow has no concurrency group. Two confirmed dispatches can
interleave web and worker rollouts for different tags and race while updating
`.prod-versions`.

Remediation: introduce one production-mutation concurrency group with
`cancel-in-progress: false`, shared with rollback workflows, and re-read active
service revisions before each mutation.

### H-15 — Emergency production rollback omits the worker service

References: `deploy/deploy_dev.sh:639-680` and
`.github/workflows/prod-emergency-web-rollback.yml:20-123`.

Normal production deployment rolls both web and worker ECS services. The
emergency workflow restores and verifies only the web service. A worker-only
regression involving queues, duplicate sends, or scheduled jobs has no reviewed
rollback path.

Remediation: add an exact worker rollback workflow or a tightly constrained
service-role input, verify the primary task ARN and running container state,
and serialize rollback with deployment.

## Medium-severity findings

### M-01 — Custom password flows bypass configured Django validators

References: `website/settings.py:317-323`, `accounts/views/auth.py:371-398`,
`accounts/views/auth.py:702-736`, and `accounts/views/auth.py:774-799`.

Registration, reset, and change flows enforce only an eight-character minimum.
They never call `validate_password`, so common, numeric, and user-similar
passwords prohibited by settings are accepted.

Remediation: call `validate_password(password, user)` in all three flows and
return consistent validation errors.

### M-02 — Public authentication and mail endpoints lack shared throttling

References: `accounts/views/auth.py:324-344`,
`accounts/views/auth.py:534-640`, and
`email_app/views/newsletter.py:119-191`.

Login, reset-mail, signup, and newsletter-verification paths lack a shared
cross-worker limiter. Automated callers can perform password guesses, consume
password-hashing CPU, harass inboxes, create unverified users, and consume SES
capacity.

Remediation: add IP and normalized-email buckets, preserve generic responses,
and emit security metrics. WAF rules should be an additional layer.

### M-03 — Sensitive account responses are missing `private, no-store`

References: `studio/middleware.py:1-48`, `accounts/urls.py:107-145`,
`accounts/views/account.py:453-557`, and
`studio/tests/test_cache_headers.py:70-88`.

The middleware protects `/accounts/` but not the actual `/account/` member
routes. Account HTML, personal-data export, newly displayed API-key plaintext,
and the token-bearing password-reset page can be cached by browser history or
other local layers.

Remediation: cover `/account/` and password-reset action routes, or apply
`never_cache` and explicit private no-store headers to each sensitive response.

### M-04 — Event registration is check-then-create under concurrency

References: `events/models/registration.py:26-28`,
`events/models/registration.py:150-177`, `events/views/api.py:182-217`,
`events/views/api.py:345-369`, `events/services/series_registration.py:89-116`,
and `events/services/series_registration.py:336-365`.

Concurrent requests can both observe no registration and race on the unique
insert. One request returns an unhandled `IntegrityError`. Series fan-out uses
multiple autocommitted inserts, so a later collision can leave a partial
registration and activity trail.

Remediation: use conflict-safe creation, control fan-out transactionally, and
emit side effects only for database-confirmed new rows.

### M-05 — Concurrent signup and newsletter subscription can return 500

References: `accounts/models/user.py:96-103`,
`accounts/views/auth.py:380-398`, and
`email_app/views/newsletter.py:148-177`.

Both paths check for an existing normalized email before creating the unique
user row. Concurrent requests can both pass the check; one then receives an
unhandled unique-constraint error.

Remediation: perform atomic account acquisition and handle the unique conflict
by fetching the winner and returning the existing-user path.

### M-06 — Member plan APIs are unbounded and bypass their own prefetches

References: `member_api/views/plans.py:42-55`,
`member_api/views/plans.py:107-109`,
`member_api/serializers/plans.py:16-31`,
`member_api/serializers/plans.py:71-144`, and `plans/models.py:1180-1305`.

The collection has no pagination, prefetches full nested detail data that the
summary does not emit, and runs a progress aggregate per plan. Detail
serialization applies `.order_by()` to prefetched managers, creating new
querysets and bypassing the cache. A local one-plan collection used eight
queries and one-plan detail used 20.

Remediation: separate list and detail querysets, paginate, annotate progress,
and order within `Prefetch` or rely on model ordering.

### M-07 — Database sessions grow indefinitely and deletion scans all sessions

References: `accounts/services/privacy.py:310-331`,
`accounts/services/privacy.py:1511-1521`, and
`jobs/management/commands/setup_schedules.py:1-290`.

The default database session backend is used, but no recurring `clearsessions`
job exists. Account deletion decodes every session and individually removes
matches inside a broad transaction.

Remediation: schedule daily expired-session cleanup and maintain a direct
user-to-session mapping. At minimum, exclude expired sessions and iterate in
bounded chunks outside the broad deletion transaction.

### M-08 — Unverified-user purge is an unbounded relation-query storm

References: `accounts/tasks/purge_unverified_users.py:88-126`,
`accounts/tasks/purge_unverified_users.py:182-264`,
`jobs/management/commands/setup_schedules.py:218-230`, and
`website/settings.py:697-703`.

For every candidate, the purge walks reverse relations and issues `.exists()`
until it finds a blocker. Candidate passes are unbounded. A bot-created backlog
can exceed the five-minute worker timeout and retry the same oversized pass.

Remediation: process bounded chunks and compute blocked user sets
relation-by-relation while preserving fail-closed deletion semantics.

### M-09 — `EmailLog.ses_message_id` is not indexed

References: `email_app/models/email_log.py:72-77` and
`api/views/ses_events.py:624-638`, `api/views/ses_events.py:724-737`.

Bounce, complaint, open, and click processing correlate inbound SES events by
exact `ses_message_id`, but the growing email log has no index on that field.

Remediation: add a non-blocking index. Evaluate uniqueness only after checking
legacy blanks and duplicates.

### M-10 — Tag operations full-scan users and duplicate helper ownership

References: `accounts/utils/tags.py:97-235`,
`studio/views/users.py:323-372`, `studio/views/users.py:983-997`, and
`studio/views/campaigns.py:244-258`.

Search and tag filters inspect JSON arrays in Python across all users, exact
matches build potentially large `pk__in` lists, and rename/delete saves users
one at a time in one transaction. `_all_known_contact_tags()` is duplicated in
two Studio modules despite a shared tag utility.

Remediation: normalize tags into an indexed relation or use supported indexed
JSON queries, centralize the helper, and chunk bulk mutations.

### M-11 — Stripe webhook attempt allocation is concurrency-unsafe

References: `payments/services/webhook_dispatch.py:143-168`,
`payments/services/webhook_dispatch.py:305-347`,
`payments/services/webhook_dispatch.py:442-446`, and
`payments/models/stripe_webhook_delivery.py:104-121`.

Attempt number is computed as `Max + 1` and inserted separately, without a lock
or uniqueness constraint on event and attempt. Concurrent delivery or replay
can assign duplicate attempt numbers. The dispatcher also checks idempotency
before handling and records completion afterward, so handler safety varies.

Remediation: atomically allocate attempts, add a uniqueness constraint, and
claim durable processing state before invoking handlers.

### M-12 — Homepage event cards issue one attendee-count query each

References: `content/views/home.py:911-922`, `templates/home.html:248-250`,
`templates/events/_upcoming_event_card_body.html:31-33`, and
`events/models/event.py:801-817`.

The homepage does not annotate attendee counts, so each of its at most three
cards falls back to `registrations.count()`. This produced three extra queries
in a local anonymous request.

Remediation: apply the attendee-count annotation already used by other event
listing services.

### M-13 — Observability configuration reads the database during app startup

References: `integrations/apps.py:7-14`,
`integrations/services/observability.py:33-75`, and
`integrations/config.py:72-73`, `integrations/config.py:296-319`.

`AppConfig.ready()` resolves the Logfire token through `get_config`, which
reads the database-backed stamp and settings table before Django finishes app
initialization. `manage.py check` emits Django's apps-not-ready database-access
warning. This couples every process and management command to early database
state and adds boot latency.

Remediation: initialize boot observability from static settings or environment,
or defer one-time initialization until after startup.

### M-14 — Retrying an ambiguous task `DELETE` can restore a ghost card

References: `static/js/plans/checkpoint_task_board.js:141-211`,
`static/js/plans/checkpoint_task_board.js:645-667`, and
`static/js/plans/member_plan.js:38-69`.

The generic write helper retries every failure. If the first delete commits but
its response is lost, the retry returns 404 and the UI restores the optimistically
removed card even though the task no longer exists. Deterministic 400, 403, and
404 responses also incur an unnecessary retry delay.

Remediation: retry only network and appropriate 5xx failures, treat a repeated
delete's 404 as success, or refetch canonical state after ambiguity.

### M-15 — Inline confirmation JavaScript breaks on ordinary apostrophes

References: `templates/studio/courses/enrollments_list.html:98-156`,
`templates/studio/courses/access_list.html:82-133`,
`templates/studio/courses/form.html:189`, and
`templates/studio/email_templates/edit.html:61`.

Dynamic values are inserted into single-quoted inline JavaScript. HTML entity
encoding does not make the resulting JavaScript safe: the browser decodes an
apostrophe before compiling the handler. A normal title such as `Beginner's AI`
invalidates the confirmation and can allow destructive submission without the
warning.

Remediation: move confirmation text into an escaped data attribute and use one
shared event listener. At minimum use `escapejs`.

### M-16 — Four account preference toggles hide state from assistive technology

References: `templates/accounts/account.html:222-317` and
`templates/accounts/account.html:734-918`.

The Maven toggle exposes `role="switch"` and `aria-checked`; newsletter,
workshop, sprint, and book-club toggles expose only visual state. The five
implementations are largely duplicated, which allowed accessibility behavior
to diverge.

Remediation: use one data-driven toggle helper and consistently render and
update switch semantics.

### M-17 — Questionnaire groups and conditional fields are not labelled

References: `templates/questionnaires/_response_form.html:25-83`,
`templates/studio/questionnaires/form.html:19-51`,
`templates/studio/questionnaires/question_form.html:28-86`, and
`templates/studio/questionnaires/response_question_form.html:32-88`.

Choice groups use outer labels whose `for` targets do not exist rather than
`fieldset` and `legend`. Conditional text inputs rely on placeholders, and
validation errors are not associated with affected controls.

Remediation: use semantic groups, explicit labels and IDs, and
`aria-describedby`/`aria-invalid` for help and errors.

### M-18 — Several typeaheads are mouse-only duplicates

References: `static/js/studio/typeahead_lifecycle.js:8-81`,
`templates/studio/courses/enrollments_list.html:169-223`,
`templates/studio/courses/access_list.html:146-200`,
`templates/studio/users/merge.html:241-291`, and
`templates/studio/includes/_people_picker.html:31-247`.

Course enrollment, access, and user merge suggestions are clickable list items
without keyboard selection or combobox semantics. The existing shared people
picker already implements keyboard navigation and option roles.

Remediation: consolidate these pages on one accessible shared picker.

### M-19 — Mutable blocking icon CDN can disable unrelated theme controls

References: `templates/base.html:193-195`, `templates/base.html:262-339`, and
`templates/studio/base.html:203`.

Every page synchronously loads `lucide@latest` without integrity protection.
The following block calls `lucide.createIcons()` unconditionally before it
defines `window.themeToggle`. A blocked, unavailable, or incompatible CDN
script aborts the block and breaks both icons and unrelated theme controls.

Remediation: vendor and pin Lucide, defer it, guard icon initialization, and
keep critical theme setup independent.

### M-20 — Three runtime settings bypass `IntegrationSetting`

References: `website/settings.py:517-526`, `studio/views/sync.py:72-90`,
`studio/worker_health.py:21-28`, and `integrations/settings_registry.py`.

`SYNC_QUEUED_THRESHOLD_MINUTES`, `SYNC_RUNNING_THRESHOLD_MINUTES`, and
`EXPECT_WORKER` are runtime/operator settings read from Django settings or raw
environment and are absent from the registry. Operators cannot change them in
Studio without a redeploy, contrary to the repository configuration contract.

Remediation: register the keys, resolve them through `get_config` or
`is_enabled`, and validate their integer or boolean types centrally.

### M-21 — API views import duplicated private Studio helpers

References: `studio/views/sprints.py:113-143`, `studio/views/books.py:81-102`,
`api/views/sprints.py:57`, `api/views/sprints.py:271-299`,
`api/views/books.py:47`, and `api/views/books.py:208-228`.

Two Studio modules contain exact duplicate `_parse_event_series()` functions.
API modules import those private view helpers and add nearly identical HTTP
error adapters. The automation API therefore depends on HTML-view internals.

Remediation: move ID/slug resolution into an `events` domain service and let
Studio and API views adapt its typed result. Delete both local copies.

### M-22 — CRM aggregation depends on private endpoint helpers

References: `api/views/crm_export.py:60-80`,
`api/views/questionnaire_responses.py:12-15`, and
`api/views/onboarding.py:49-55`.

CRM export and questionnaire views import private serializers, pagination
parsers, and persona helpers from other large endpoint modules. This creates a
fragile view-to-view dependency graph and circular-import risk.

Remediation: move serializers into `api/serializers`, query parsing into a
shared request module, and persona resolution into a domain service.

### M-23 — Three duplicate GitHub-sync helpers are dead implementations

References: `integrations/services/github_sync/parsing.py:14-42`,
`integrations/services/github_sync/repo.py:6`,
`integrations/services/github_sync/repo.py:57-86`, and
`integrations/services/github.py:92-103`.

`repo.py` contains byte-for-byte AST-equivalent copies of three parsing helpers.
Runtime callers and the facade use the canonical `parsing.py` versions; no
production or test caller imports the private copies.

Remediation: delete `repo.py:57-86` and its now-unused `uuid` import.

### M-24 — Worker task formatting has two active authorities

References: `api/serializers/worker.py:40-74`,
`studio/views/worker.py:389-415`, and `studio/views/worker.py:49`.

`_format_task_value()` and `_looks_like_traceback()` are exact duplicate active
implementations. Comments promise API and Studio lock-step behavior, but the
Studio view imports only a third shared helper.

Remediation: move all task-formatting helpers to one neutral `jobs` module and
delete the Studio copies.

### M-25 — Nullable datetime serialization is implemented at least 18 times

References include `api/serializers/worker.py:33`,
`api/serializers/users.py:41`, `api/serializers/plans.py:19`,
`api/serializers/onboarding.py:39`, `api/views/sprints.py:325`,
`api/views/events.py:256`, `api/views/campaigns.py:164`, and
`member_api/serializers/plans.py:10`.

At least 18 `_isoformat_or_none` or `_iso` helpers implement the same nullable
`isoformat()` operation across serializers and views. This is small code, but
it demonstrates fragmented serialization ownership and multiplies any future
normalization change.

Remediation: provide one nullable datetime serializer in a neutral shared
module and remove local copies.

### M-26 — Studio Logfire edits do not apply as documented

References: `_docs/integrations/observability.md:8-14`,
`_docs/integrations/observability.md:73-101`, `integrations/apps.py:7-14`, and
`integrations/services/observability.py:33-75`.

Documentation says DB-backed Studio changes take effect without redeploy, but
Logfire initializes only once in `AppConfig.ready()`. Enabling or adding a token
after a process starts does not initialize it, and disabling cannot remove
already-installed instrumentation.

Remediation: mark these settings restart-required and correct the documentation,
or implement an explicit tested process-wide reconfiguration mechanism.

### M-27 — Recurring schedule reconciliation is partial and silently fail-open

References: `scripts/entrypoint_init.py:174-193`,
`jobs/management/commands/setup_schedules.py:24-290`, and
`jobs/tests/test_entrypoint_schedules.py:157-191`.

Schedule registration performs many separate autocommit `update_or_create`
calls. Entry-point code catches every command failure and only logs it. A bad
entry or mid-command database error can leave mixed old and new schedules with
no durable degraded-health signal or retry independent of the next boot.

Remediation: validate the declarative schedule set first, apply it atomically,
publish a durable failure state and alert, and add periodic reconciliation.

## Low-severity findings

### L-01 — Search filters hand-build query strings without URL encoding

References: `templates/studio/users/list.html:135-170`,
`templates/studio/crm/list.html:13-38`, `studio/views/users.py:653-669`, and
`studio/views/crm.py:158-163`.

Filter links preserve `q={{ search }}` directly. HTML escaping is not URL
encoding, so `+` becomes a space and `&` can split the query when a filter is
clicked.

Remediation: construct query strings through `QueryDict.urlencode()` or one
query-parameter template helper.

### L-02 — Course list pages render every record twice

References: `templates/studio/courses/enrollments_list.html:63-167`,
`templates/studio/courses/access_list.html:50-144`, and
`templates/studio/base.html:21-147`.

Separate desktop rows and mobile cards duplicate values, permissions, forms,
and the unsafe confirmation handlers. Studio already has a responsive table
component that converts one semantic table for mobile layouts.

Remediation: render each record once through the responsive table system or a
shared row/action partial.

### L-03 — Several templates are unreachable legacy UI

References: `templates/events/_timeline_event_card.html:1`,
`templates/events/_timeline_past_card.html:1`,
`templates/events/_timeline_series_card.html:1`,
`templates/content/_workshop_topic_facet_body.html:1-39`,
`templates/content/_workshop_technology_facet_body.html:1-36`,
`templates/studio/events/_past_pager.html:1-39`,
`templates/studio/plans/note_form.html:1-63`, and
`studio/views/plans.py:494-524`.

Repository-wide reference searches found no callers for the timeline wrappers,
old workshop facets, or past pager. Legacy plan-note views redirect to newer
member-scoped routes, leaving the old form unreachable.

Remediation: delete the files after focused route and template checks, and
consider a template reachability check aware of render/include/extends tags.

### L-04 — Plan pages ship two handwritten Markdown renderers

References: `static/js/plans/checkpoint_task_board.js:21-102` and
`static/js/plans/member_plan.js:173-250`.

Both files independently implement escaping, inline Markdown, links, and block
rendering, and they have already diverged slightly in URL escaping.

Remediation: use one shared tested module or the existing canonical server-side
sanitizer.

### L-05 — Several iframes have no accessible name

References: `templates/includes/video_player.html:10-15`,
`templates/studio/email_templates/edit.html:77-82`, and
`templates/studio/campaigns/detail.html:97-103`.

The Loom video and two preview frames have no `title`, so screen readers cannot
distinguish their purpose.

Remediation: add contextual iframe titles.

## Hypothesis requiring a controlled workflow test

Scheduled Playwright incident-closing expressions at
`.github/workflows/scheduled-playwright.yml:288-312` and
`.github/workflows/scheduled-playwright-dev.yml:191-212` treat any dependency
result other than literal `failure` as recovery. A cancellation or timeout may
therefore close an open incident without a green suite. GitHub's cancellation
scheduling semantics need a controlled workflow test before this is promoted
to a confirmed finding. The safer condition is exact required-job success plus
current-run SHA evidence.

## Investigated suspicions ruled out

- GitHub, Zoom, and Stripe primary webhook signature verification occurs before
  state-changing work.
- Git checkout uses an argument list rather than `shell=True`; the confirmed
  content-sync issue is filesystem symlink traversal, not shell injection.
- Password-reset and login responses do not expose account existence through
  their response shape. Reset replay and missing throttling remain valid.
- Member API keys and staff tokens store hashed secrets, not reusable plaintext.
- Plan Markdown escapes output and allowlists URL schemes; no practical XSS path
  was established there.
- Current callers of content-card safe attribute fragments provide fixed or
  normalized values; the component API is risky, but no current exploit was
  established.
- CRM export already enforces pagination and batches plan/note loading; the
  suspected export N+1 was not present.
- Member book serialization bulk-fetches chapter-read state.
- Checkout fulfillment has row locking and terminal-state guards, so the Stripe
  dispatcher race alone does not prove duplicate entitlement grants.
- Django-Q completed task retention is bounded with `save_limit=250`.
- Scheduled full Playwright duration balancing, deploy Playwright sharding,
  timing instrumentation, and prior sync/SES/Zoom/GitHub test gaps from the
  2026-08-13 audit have been addressed and were not repeated.
- `/ping` is intentionally an ALB liveness and version probe rather than a
  database-readiness endpoint.
- `questionnaires/onboarding_ai.py` and
  `questionnaires/services_onboarding_ai.py` are intentionally separated pure
  and ORM layers.
- `payments/stripe_links.py` uses `get_config`; its Django setting is a fallback,
  not a configuration-framework bypass.
- The old `content/workshop_facets.py` dead-module finding is stale because the
  module is gone.

## Recommended order of work

1. Stop external-impact risks: C-01, H-01, H-02, H-03, and H-04.
2. Fix worker and campaign recovery: H-06, H-07, M-11, and M-27.
3. Close deployment safety gaps: H-11 through H-15.
4. Correct privacy and user-trust failures: H-09, H-10, M-01 through M-05.
5. Reduce hot-path query amplification: H-05, H-08, and M-06 through M-12.
6. Address accessibility and client correctness: M-14 through M-19 and L-01.
7. Consolidate ownership and remove dead code: M-20 through M-26 and L-02
   through L-05.

Each remediation should enter the normal issue pipeline independently or in a
small group of tightly coupled changes. Concurrency, credential, email, and
privacy fixes need focused regression tests before refactoring adjacent code.
