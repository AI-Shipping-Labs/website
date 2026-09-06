# AI Shipping Labs migration to community-base

Date: 2026-09-06. Planning issue: AI-Shipping-Labs/website#1562.
This dated audit is the reviewed execution plan for the migration. It records
planned work and its evidence gates; it is not evidence that the migration has
run.

## Outcome and authority

AISL installs the shared package while preserving its member journeys, payments,
public design, content identity, API compatibility and existing data. Shared apps
replace site implementations only after donor compatibility and a tagged release.
Each database, deployment, cache and credential set remains independent of DTC.

Canonical decisions, architecture, playbooks, phase files and STATUS live in
[community-base](https://github.com/DataTalksClub/community-base/tree/main/docs).
This document decomposes every AISL issue A0.1 through A6.4; it does not create a
second status tracker. `docs/plan/STATUS.md` remains authoritative. Parent issues
are not done until every approved child, verification and development deploy is
done. Proposed child ids below are planning labels until added to canonical phase
files with `uv run python scripts/plan.py sync` and `check` in community-base.

[`AGENTS.md`](../../AGENTS.md), [`_docs/PROCESS.md`](../PROCESS.md),
[`_docs/testing-guidelines.md`](../testing-guidelines.md) and
[`software-engineer.md`](../../.claude/agents/software-engineer.md) govern
execution. Full pipeline: issue intake, PM grooming, isolated engineer worktree
with lifecycle lease, tester, PM acceptance, engineer commit, orchestrator local
`--no-ff` merge and push to main, on-call deployment watch, attributable worktree
cleanup/retention. AISL uses no PRs; canonical references to site PRs map to this
process. Package work still uses its own PR flow. Never merge unreviewed work or
abandon unrelated local changes. The main reconciliation is owned by the
orchestrator and must preserve both local and remote commits before new worktrees.

No operator API is added by this documentation issue. Later Studio actions and
operator data retain authenticated API coverage unless a groomed issue records a
specific reason. Public design continues to use AISL's indexed partials and
[`_docs/design-system.md`](../design-system.md); Studio adopts the package shell.

## Research snapshot and release boundary

AISL donor reviewed: `baa8ea28056bad854a4f7a74dc44e42febf6823b`.
Package reviewed: `49c3a7ca13452fd2580db098c6da3ed0094b8fd0`.
Refresh both SHAs at every compatibility checkpoint; these are research inputs,
not approved production schema snapshots.

Latest adoption-safe release observed in canonical status: `v0.3.0` (C2.4).
Identity/community capabilities are on main with provisional migrations. Events
C4.1d was merged through community-base PR 110 (orchestrator update after the
research snapshot; 814 package tests). C4.2, C5.1 and C5.2 remain pending.
C3.7 and C4.3 donor compatibility remain pending. A successful package test or
presence of a source directory does not make a domain app installable in AISL.
Never pin main, an agent branch, local editable source, provisional v0.4/v0.5,
or a future v0.6 tag that has not actually been published.

Execution order:

1. Reconcile main, publish this reviewed plan and prerequisite canonical fixes.
2. A0.1 dependency/tooling only is the first safe task explicitly assigned by the
   owner; no new app registration or policy hook is needed for it.
3. A0.3 policy and A0.2 config, then local jobs/mail and Studio/content sync.
4. Wait for C5.2, then prepare AISL membership/extensions and event seams while
   DTC performs D3.1. These preparations use local site code; they do not adopt
   provisional domain apps.
5. C3.7 depends on A3.2 and D3.1; C4.3 depends on A4.1. Their compatibility work
   plus C5.2 enables C5.3, the single adoption-ready domain release v0.6.0.
6. Adopt identity, events and curriculum in separate rehearsed freezes with
   verified runtime dependencies. No domain drop is part of early preparation.
7. AISL uses django_q and ses_local until R6.1 records D13: at least four
   consecutive weeks of DTC production traffic with no P1 attributable to Relay
   and green Relay status contract throughout. Only then execute AISL Phase 6.

## Verified source map

| Surface | Existing owner and symbols | Shared target / site responsibility |
|---|---|---|
| Dependency/runtime | `pyproject.toml`, `uv.lock`, `Makefile`, `website/settings.py` | Tag pin; Python >=3.13 and Django 6 already fit; retain local runtime initially |
| CI/deploy | `.github/workflows/deploy-dev.yml`, `ci.yml`, `deploy-prod.yml`, `deploy/update_task_def.py`, `entrypoint.sh`, `scripts/watch-ci.py` | Main deploy workflow is the gate; only on-call watches |
| Configuration | `integrations/config.py:get_config,is_enabled,resolve_source,reset_local_config_cache,clear_config_cache`; `integrations/models/integration_setting.py:IntegrationSetting`; `integrations/settings_registry.py:INTEGRATION_GROUPS` | `community_base.config` with declarations and encrypted secret storage; compatible cache and fallback behavior |
| Access | `content/access.py:get_user_level,get_active_override,can_access,_resolve_required_level`; `accounts/models/tier_override.py:TierOverride` | `content/access_policy.py:TierAccessPolicy` is new; keep purchase, drip and public copy semantics in site adapters |
| Jobs | `jobs/management/commands/setup_schedules.py`, `jobs/tasks/helpers.py`, `jobs/task_history.py`, `jobs/task_entities.py`, `studio/views/worker.py` | Add cb_jobs; legacy tasks and qcluster remain until Phase 6 |
| Mail | `email_app/services/email_service.py:EmailService.send,send_rendered,prepare_rendered,send_prepared,_delivery_decision,_build_unsubscribe_url,_should_include_verify_footer`; `email_app/services/email_classification.py`, `email_app/services/preview_contexts.py` | Durable send plus site hooks; campaign path remains until Phase 6 |
| Mail history | `email_app/models/email_log.py`, `ses_event.py`, `email_campaign.py`, `campaign_delivery.py`, `email_template_override.py` | Preserve local history until verified Relay import |
| Studio | `templates/studio/base.html`, `studio/sidebar.py`, `studio/decorators.py`, `studio/templatetags/studio_filters.py`, `studio/views/users.py`, `member_notes.py`, `tags.py`, `global_search.py`, `impersonate.py`, `dashboard.py` | Shared shell and generic user views; site tier/Slack/billing panels and domain providers |
| Sync | `integrations/services/github_sync/{checkout,orchestration,lifecycle,media,parsing}.py`; `dispatchers/`; `integrations/models/content_source.py`, `webhook.py`; `studio/views/{sync,content_sources}.py` | Immutable package checkout/parser contract; site parsers for every actual content type |
| Accounts | `accounts/models/user.py:User,UserManager,BounceState`; `token.py:Token`; `member_api_key.py:MemberAPIKey`; `tier_override.py:TierOverride` | Shared identity after fields/keys move; `payments.Membership` and optional `accounts_ext.MemberExtra` are new |
| Identity/community | `accounts/views/`, `accounts/services/`, `questionnaires/`, `community/`, `notifications/`, `comments/`, `voting/` | Kept-label apps only after exact donor migration inventory and C3.7 |
| Events | `events/models/{event,event_series,registration,feedback,join_click}.py`, `events/services/`, `events/views/`, `events/urls.py`, `content/models/instructor.py` | Shared events; AISL writeup/banner hooks, paid gating, side effects and URL compatibility |
| Curriculum | `content/models/course.py:Course,Module,Unit,UserCourseProgress,CourseAccess`; `cohort.py:Cohort,CohortEnrollment`; `enrollment.py:Enrollment` | cb_curriculum; CourseAccess/Stripe product behavior stays AISL |
| Coursework | `content/models/peer_review.py:ProjectSubmission,PeerReview,CourseCertificate`; `content/models/completion.py:UserContentCompletion` | Explicit mapping to optional cb_coursework; non-course completion remains AISL |
| Instructors/workshops | `content/models/instructor.py:Instructor,CourseInstructor,WorkshopInstructor`; `content/models/workshop.py`; `integrations/services/github_sync/dispatchers/workshops.py` | Host mapping; workshops, tutorials and durable workshop URLs remain site-owned |

## Mandatory plan corrections and contract checkpoints

Core prerequisite corrections were merged in [community-base PR 111](https://github.com/DataTalksClub/community-base/pull/111), package main `1e1102a` (orchestrator-confirmed green CI and 84-issue plan check).
Refresh that evidence before A0.1 implementation; do not silently choose a different
API. The remaining later contract checkpoints below belong to their owning issue.

1. A0.1 uses v0.3.0 and stays dependency/tooling-only. The canonical policy path
   `content.access.TierAccessPolicy` does not exist; A0.3 creates
   `content.access_policy.TierAccessPolicy`. Do not configure it earlier.
2. Existing `grep ... && exit 1 || true` guard always succeeds. Validate TOML and
   lock sources with a fail-closed helper; guard main Deploy Dev before install,
   because ci.yml only triggers on pull_request. Preserve unrelated pyproject and
   lock edits during link/unlink. No automatic PR-based bump bot in AISL.
3. `community_base.config` exports get/is_enabled only. There is no public
   clear_config_cache; local get_config additionally supports use_settings.
   Inventory callers and define/release missing compatibility before switching.
   `config.service.RuntimeConfig.reset/publish` exist but using internals requires
   an explicit maintained compatibility decision. Setting has value_type/source;
   do not remove them from mapping. Encrypt secret values with the package format:
   plain source TextField values cannot be stored unchanged in secret target rows.
4. Both shared dispatch_after_commit and mail.send require transaction.atomic.
   Dispatch returns `(intent, created)`. Dedupe keys and context must survive
   retries unchanged. Mint secret verification/reset tokens only inside workers
   through documented context/link hooks; never put tokens in durable job payloads.
5. Existing content dispatchers take filesystem paths; shared Parser receives an
   immutable checkout with read_text/read_bytes. Adapt interfaces and prove source
   isolation; moving directory names does not implement this contract.
6. Current AISL event routes include `/events/<id>/<slug>`, id-based join URLs and
   legacy redirects. Shared slug mode must preserve these via a tested bridge or
   package fix before cutover. Canonical names and route styles are not parity proof.
7. Kept-label swaps may include separately approved appended migrations for new
   shared schema. Require no destructive or unexpected donor-table operations and
   explicitly enumerate approved new operations rather than claiming marker-only
   output when new tables are required.
8. Replace fixed 17-group, 35-callsite, 50-template counts with exact donor-SHA
   inventories and explicit differences. R6 contact parity is deduplicated identity
   union with reconciliation, not User count plus subscriber count when overlapping.
9. Development rehearsal uses repo-local `.tmp/` paths and exact counts. Canonical
   P14 pg_stat_user_tables estimates cannot prove row preservation. Production
   database/credential access remains prohibited; an operator provides allowed
   development evidence using the site's process.

## Common executable verification and evidence

Run in the assigned worktree. Before dispatch, replace every placeholder in a
packet with a concrete issue, path, fixture or command; never type angle
brackets as shell redirections. Newly proposed files and commands must be built
and reviewed in the specified child before a later child calls them.

- `git status --short` and `git rev-parse HEAD`: record starting dirty state and SHA.
- `uv run python scripts/affected_tests.py --json`: record exact local scope.
- `make test-affected`: run exactly the emitted Django labels and Playwright core
  or full subset. If mapping is wrong, fix scripts/affected_tests.py and its
  tests through scoped review; do not substitute a full Django run.
- `uv run python manage.py check`: no issues.
- `uv run python manage.py makemigrations --check --dry-run`: no changes detected
  for runtime/schema work. Docs-only work follows the mapper, not invented suites.
- For fresh migration verification, allocate a unique absent path below `.tmp/`,
  set DATABASE_URL to its absolute SQLite URL, run `uv run python manage.py migrate`:
  all migrations finish OK. Never delete or repurpose an existing database.
- For data changes, repeat against an authorized disposable development copy;
  record exact `COUNT(*)`, preserved primary-key sets, orphan counts, critical
  field comparisons, uniqueness and permissions before/after. New additive table
  counts are listed separately. Synthetic-only checks say
  `Not run here, needs: development-copy rehearsal`.
- Reverse the precise newly introduced migration then apply it on the disposable
  copy; either both pass or the reviewed migration explicitly documents its
  irreversible nature and restore procedure. Do not guess previous migration ids.
- A copied code surface has a donor test matrix with moved/adapted/site-owned
  classification and exact test counts; missing behavior blocks deletion.
- Tester captures/reads screenshots for changed pages using the site workflow,
  under `.tmp/screenshots`; docs/tooling-only work records no changed pages.
- Engineer and tester update issue checkboxes; PM accepts the member/admin outcome.
- On-call records exact merged SHA, Deploy Dev run id/head and terminal verdict.
  No parent phase is done before green development deploy and required evidence.

### How to judge results

A packet is green only when the exact base/head and generated test scope are
recorded, every required command exits with its stated result, and the fixture
or permitted development copy demonstrates the target behavior. Check both a
successful case and the named denial, invalid-input, duplicate, retry, outage or
rollback case. Compare counts, stable IDs, foreign keys, timestamps, auth state,
URLs and provider ownership before and after; every difference is explained.
For a changed page, the tester captures and reads desktop and mobile screenshots
for the specified route and state, and an error page or broken layout fails the
packet. A docs/tooling packet records `not_applicable` for product screenshots
only when `scripts/affected_tests.py` confirms no render impact. Reused evidence
is valid only when its inputs, envelope and artifact digest are independently
validated and unchanged; otherwise rerun it. Missing, guessed or invalid
evidence is recorded as `Not run here, needs:` with the owning issue or operator
and the exact evidence still required.

All freezes follow P13 adapted to AISL process: reviewed code committed on its
branch, published package tag, development rehearsal no older than seven days,
announced freeze and labelled affected issues, ordinary deployment workflow,
app-specific deployed smoke and owner-run production verification. Do not send
freeze announcements using tools without explicit owner authorization.

Rollback is specific to each child: before a reader switch retain old storage;
after switch preserve compatibility writes or freeze affected writers so rollback
has current data; after contraction use the tested restore/migration rollback.
Changing only a package pin is not rollback after destructive schema changes.
Stop on row loss, two failed checks after a genuine fix attempt, missing tag,
mismatched donor replaces inventory, unavailable Relay contract, unannounced
required freeze, production credential requirement or decision conflict.

## Phase 0 cards

### A0.1 Dependency and local development targets

Canonical dependency: C2.4/v0.3.0 (done); C0.5 is historical package work and is
not the release boundary. Additional gate:
this plan published, canonical correction merged and reconciled clean main.
This existing parent is small enough for one implementation issue.

1. Read pyproject.toml, Makefile, deploy-dev.yml, ci.yml and package v0.3.0
   pyproject metadata. Resolve remote tag SHA and record it without credentials.
2. Add `community-base` dependency with `[tool.uv.sources]` git/tag source and run
   `uv lock`; inspect resolver changes. Unexpected mass upgrades must be explained
   or reduced. Do not add package apps, migrations, URLs or COMMUNITY_BASE yet.
3. Implement `core-link` and `core-unlink` with bounded reversible edits. Either
   preserve unrelated dependency changes or refuse before changing anything.
   Use a disposable checkout to prove the round trip and interrupted/unrelated
   edit behavior. Current canonical blanket checkout is forbidden by plan fix.
4. Add a fail-closed source guard before install in both actual CI workflows.
   Prove tagged source passes and local path, editable or branch source fails.
5. Update existing developer instructions for pin and local link use. Run
   `uv sync --locked`, import version (0.3.0), Django check and mapper tests.

Data invariant: no installed-app, AUTH_USER_MODEL, database schema, URL or backend
change. Rollback: revert this dependency/tooling commit through site process.
Done: engineer/tester/PM accepted, main pushed, on-call green, canonical A0.1 updated.

### A0.2 Runtime settings expansion, reader switch and contraction

Canonical dependency: A0.1. Added prerequisites: package compatibility contract
for cache/fallback behavior and registered API authentication migration reviewed.
Own `integrations/config.py`, `settings_registry.py`, `models/integration_setting.py`,
`apps.py`, new `settings_keys.py`, relevant integration migrations,
`studio/views/settings.py`, `api/views/integration_settings.py`, `asl_cli/`.

1. A0.2a inventory every key, declaration metadata, default, secret flag, env and
   Django fallback and every get_config/resolve_source/cache-helper callsite.
   Record actual group count and API path/auth/response contract. Test saved,
   empty, unset, false, worker read and settings-fallback behavior with synthetic
   data. Missing package behavior becomes a package fix plus release dependency.
2. A0.2b install cb_config/cb_api only after policy/runtime setup is valid;
   declare keys from IntegrationsConfig.ready. Add target migrations and reversible
   copy using historical models, 1,000-row batches and explicit secret encryption.
   Keep source model and source readers; replay must create no duplicate keys.
3. Before the A0.2c UI switch, prove the released package settings template,
   template tags and route dependencies in an installed-tag fixture with the
   minimum shared Studio shell. If this requires the full shell, record a
   package or A2.1 prerequisite and retain the existing settings UI. After
   development counts/secret round-trip pass, switch read helpers and Studio/API
   endpoint adapters while maintaining source compatibility for rollback.
   Preserve actor audit, secret masking, source badges and legacy CLI path behavior.
4. A0.2d migrate one owning app's imports per child; update mocks at the real
   owner boundary. Do not remove helper exports until every actual use is handled.
5. A0.2e after green deploy and reader inventory empty, remove old model/shim and
   legacy views/templates in a separate contraction issue. Update AGENTS config
   rule and `_docs/configuration.md` only when new authority is effective.

Verification: `uv run python manage.py test integrations studio api --parallel 4`
is canonical focused coverage, subject to mapper governance; make test-affected,
key/group parity, synthetic secret get succeeds without printing its value, worker
cache invalidation and default behavior unchanged. Staff edits an optional key,
sees DB source badge; clearing it returns correct env/default. A bearer key without
settings scope is denied. Old/new count parity and migration reverse/reapply pass.
Rollback: source storage retained through reader switch, documented writer ownership
keeps rollback data current; no model deletion in initial copy issue.

### A0.3 Access policy hook

Dependency: A0.1. Own new content/access_policy.py plus content/access.py and
website/settings.py runtime setup. Read payments/models/tier.py,
accounts/models/tier_override.py and existing access tests.

1. Implement kernel AccessPolicy.user_level, can_access(user, required_level),
   level_label via verified helpers; configured path is
   `content.access_policy.TierAccessPolicy`. Shared package can_access may receive
   an integer or obj.required_level; do not pass richer course logic by accident.
2. Import shared level constants without changing numeric 0/5/10/20/30 values.
   Preserve anonymous/open behavior, registered sentinel, effective overrides,
   expiry and public tier wording. Keep current site callsites initially.
3. Register kernel and valid COMMUNITY_BASE only in this reviewed runtime child;
   set SITE_KEY aisl, local jobs/mail choices and Studio title. Never configure
   an undefined hook. Package app prerequisites are explicit in later cards.
4. Verify anonymous/free/Basic/Main/Premium with active and expired overrides and
   registered-only content. Test purchase/drip behavior remains site-owned.

Verification: content affected tests and all required checks pass; synthetic Main
member `can_access(member,20)` is True; expired override cannot grant access.
Rollback: revert hook/settings/constant import together; no data migration.

## Phase 1 cards: durable local jobs and mail

### A1.1 Add cb_jobs with django_q

Canonical dependency C1.5; additionally finish A0.1/A0.3 and required config keys.
Read jobs/management/commands/setup_schedules.py, jobs/tasks/helpers.py,
jobs/task_history.py, studio/views/worker.py and package jobs README at pinned tag.

1. A1.1a add django_q extra, cb_jobs and its documented prerequisites. Keep local
   jobs app, qcluster, task functions and legacy schedule identities. Apply new
   cb_ tables only. Use sync backend in isolated fixtures.
2. A1.1b register a bounded `system.noop` handler if none exists. Dispatch inside
   transaction.atomic; commit makes one intent run and rollback makes none run.
   Assert duplicate key/same payload returns same intent; conflict is rejected.
3. A1.1c integrate sync_schedules ownership with setup_schedules. Existing
   package sync_schedules creates due-intent wakeups; do not create a second
   scheduler for the same work. Inventory every cron/name before and after.
4. A1.1d expose the package intents page from local worker navigation without
   replacing worker history. Register route ownership when shell is installed.

Verification: `uv run python manage.py test jobs --parallel 4`, mapper gates,
migrate/check; development noop reaches succeeded within one minute, no duplicate
legacy task runs. Record intent id/status, not payload contents. Rollback keeps
old schedules and worker operational; disable only newly added scheduler entries.

### A1.2 Move transactional mail by owning app

Dependencies C1.5, A1.1; config compatibility and mail hooks reviewed first.
Own email_app/hooks.py (new), email_service.py, email_classification.py, template
hooks, and one sender-owning app per child. Leave campaign_dispatch.py SES path.

1. A1.2a inventory actual EmailService constructors and send/send_rendered/
   prepare_rendered/send_prepared calls, each purpose, context, recipient source,
   dedupe behavior, cc/bcc, delivery result expectation and auth token lifetime.
   Historical 35-callsite estimate does not define scope. Unsupported rendered
   sending must get an adapter or package release before its caller migrates.
2. A1.2b install cb_mail on ses_local with MAIL_TEMPLATE_DIR. Implement recorder,
   override loader, preference resolver, unsubscribe and verify URL builders with
   exact README signatures. Capture normalized free_welcome, event_registration
   and password_reset parity fixtures. Every suppressed case remains suppressed.
3. A1.2c migrate accounts senders as a bounded child: password reset and verification
   token generation occurs in worker via documented hooks. Wrap the domain write
   and durable send in transaction.atomic; stable key/context replay and no
   token leakage to durable payloads. Do not interpret queued as already sent.
4. A1.2d repeat one issue per actual remaining app in inventory (events,
   community, content and other discovered owners), never a global replacement.
   Preserve related-object ids, preferences, sender kind and cc/bcc semantics.
5. A1.2e once remaining production caller count is zero, remove compatibility shim
   in its own issue. Campaign transport remains explicitly listed as exempt.
   Update configuration docs to shared send and site hooks.

Verification each child: `uv run python manage.py test email_app <owner> --parallel 4`
with owner resolved by groomer, mapper checks; rollback of caller transaction
creates no mail, duplicate send creates one delivery, suppression writes no SES
request. Development password reset writes EmailLog plus provider_accepted
EmailDelivery and member can complete reset. Compare rendered result with source
fixtures. Rollback switches only migrated caller ownership and drains/fences
pending durable work first to prevent duplicate sends.

## Phase 2 cards: Studio and content synchronization

### A2.1 Adopt the Studio shell

Dependency C2.4, runtime/config setup; install documented shared prerequisites.
Own templates/studio/base.html, studio/sidebar.py, decorators.py,
templatetags/studio_filters.py, views/global_search.py, impersonate.py,
dashboard.py, each site AppConfig, assets/css/tailwind.css and tailwind.config.js.

1. A2.1a export every section/destination/route name plus section-only/unlisted
   routes from actual sidebar; capture sidebar/deep page screenshots for staff
   and superuser. Record permissions and current section count/order.
2. A2.1b register one section per bounded issue behind retained local shell;
   move search/dashboard providers and audit hook with superuser-only impersonation.
   Generic tags move; AISL tier/SES/UTM helpers retain their site owner.
3. A2.1c compile CSS from package preset plus both template trees. Switch base
   ownership and delete local shell only after route and visual parity passes.
   Preserve existing template blocks and public base.html.
4. A2.1d remove old sidebar and generic helper duplicates; install
   `uv run python manage.py studio_routes --check` in real main CI. Update
   `_docs/studio-conventions.md` and owning design index references.

Verification: studio_routes -> OK, studio affected tests and mapper-selected
Playwright; staff finds a deep event page with correct active navigation,
non-superuser cannot impersonate, public pages retain AISL styling. Screenshot
review confirms intended section ordering and page usability. Rollback restores
old shell/registry routing as a unit, preserving new additive data tables.

### A2.2 Shared user pages with AISL panels

Dependency A2.1. Own studio/views/{users,member_notes,tags}.py,
templates/studio/users and site registration hooks.

1. A2.2a inventory list/detail/export/tag/note contracts and model owning notes;
   create column/badge/panel adapters for tier, Slack, billing and bounce status.
2. A2.2b copy notes into cb_studio.MemberNote, preserving user/author/time/body
   using explicit historical-model mapping. Replay is idempotent, note count and
   attribution match; retain source for rollback until switch proved.
3. A2.2c switch list/detail/export/tags/notes, preserving filters, pagination,
   CSV semantics and role restrictions. Keep new/import/merge locally until Phase 3.
4. A2.2d remove replaced functions/templates after green development; remove old
   note storage separately only after data contract and rollback agreed.

Verification: existing user list/export/import/merge Playwright owners remain
valid; staff can search member, see tier/bounce, add note and export filtered
users. No extra personal information in exported columns. Counts and migration
gates pass. Rollback keeps notes synchronized through switch or freezes note writes.

### A2.3 Shared content-sync engine with site parsers

Dependency C2.4; practical prerequisites A1.1 and A2.1 for jobs/Studio surfaces.
Own integrations/models/{content_source,webhook}.py, github_sync/ engine,
new content/sync_parsers/, management commands and current webhook/API mounts.

1. A2.3a inventory source fields/locks/logs and each real dispatcher: articles,
   courses, curated_links, downloads, events, hosts, instructors,
   interview_questions, marketing_pages, projects, tiers, workshops. Record
   provenance UUID, source path, commit, visibility and soft-delete behavior.
2. A2.3b install cb_content_sync and reversible copy ContentSource/SyncLog/
   WebhookLog. Preserve source identity, statuses, timestamps and delivery dedupe.
   Keep exactly one active engine per source during gradual adoption.
3. A2.3c build adapters one parser per issue against immutable checkout API;
   fixture import, repeat no-op, rename, missing file, invalid YAML and partial
   failure cases must pass. Do not reopen filesystem paths or delete rows from
   another source. Courses/workshops need independent larger child breakdowns.
4. A2.3d switch source command/webhook/Studio/API only for proven parsers; preserve
   GitHub signature rejection and duplicate delivery behavior. All remote/media
   side effects occur under documented job/service boundaries.
5. A2.3e after all types have baseline parity, delete obsolete engine modules and
   replace entrypoints with package owners. Later Phase 5 replaces course parser
   with curriculum parser. Keep events ownership conflict with D7 explicitly
   resolved before replacing its parser; never broaden GitHub authoring silently.

Verification: on clean synthetic fixture checkout, `uv run python manage.py
sync_content --from-disk <approved-checkout>` produces recorded old-engine counts;
second run no changes; removed item soft-deletes correctly, failed file does not
purge good content. Actual private content rehearsal is `Not run here, needs:`
until permitted input supplied. Mapper tests pass. Rollback restores per-source
engine ownership; no simultaneous old/new sync and no destructive source removal.

## Phase 3 cards: membership preparation and identity adoption

### A3.1 Extract payments.Membership before shared identity

Dependency C5.2 (currently pending). Additional runtime foundations A0-A2.
Own new payments/models/membership.py/export, payments migrations,
accounts/models/user.py, content/access_policy.py and one reader app per child.

1. A3.1a inventory all five fields and writers, including Stripe webhook/race
   handling and subscription reconciliation, related_name/queryset filters,
   templates, serializers, commands and tests. Baseline user and paid-tier counts.
2. A3.1b expand Membership(user OneToOne, tier, pending_tier, billing_period_end,
   stripe_customer_id, subscription_id) with explicit constraints and reversible
   1,000-row historical-model copy. `for_user` creates a free row lazily without
   changing a paid member; race-safe uniqueness. Keep source fields.
3. A3.1c establish one writer authority/compatibility writes during transition.
   Migrate payments first, then accounts, content/access, events, Studio/API,
   community/jobs and every additional inventory owner, one issue per owner.
   A partial rollout must not show different paid access in old versus new reads.
4. A3.1d after all readers and filters use membership and development evidence
   passes, contract five User fields in a separate issue. No property shim may
   hide a remaining queryset `user__tier` use.

Verification: payments/accounts/content/events canonical focused coverage and
mapper gates; membership count equals users; paid-tier distribution, Stripe ids,
pending changes and billing timestamps match; repeated webhook gives one final
membership state; overrides retain precedence. Rehearse reverse/forward. Rollback
requires retained source and synchronized writes until contract, then tested restore.

### A3.2 Remaining identity extensions and API keys

Dependency A3.1. Own accounts/models remaining nonshared fields/models,
payments TierOverride target, optional accounts_ext and site API adapters.

1. A3.2a compare concrete fields, M2M, indexes, constraints and defaults against
   C3.1 shared schema at exact package SHA; ignore computed reverse relations when
   reporting actual columns. Record every mismatch. Create MemberExtra only for
   real site-owned fields, not hypothetical future attributes.
2. A3.2b move TierOverride to payments with ids/user links/expiry/audit retained;
   migrate readers then contract separately. Cross-app migration dependencies must
   allow both historical and fresh databases.
3. A3.2c inventory Token and MemberAPIKey auth/scopes/revocation/expiry/hash format.
   Build cb_api compatibility mapping; stop if current credential digest cannot
   be preserved. Never expose raw tokens or silently invalidate existing clients.
   Data copy, API reader switch and source contraction are separate children.
4. A3.2d refresh donor schema/migration inventory and test ownership matrix for
   accounts, questionnaires, community, notifications, comments and voting.
   Hand exact donor SHA to C3.7 and stop schema churn for its checked inventory.

Verification: `uv run python manage.py test accounts payments api member_api
--parallel 4`, mapper, no schema drift, synthetic credential success and insufficient
scope rejection, no lost override or identity row. Donor field/state comparison
must explain every difference. Rollback before contraction uses old auth adapters;
after contraction uses approved backward mapping, never credential guessing.

### A3.3 Freeze: adopt identity/community and configure onboarding

Dependencies C5.3, C3.7, A3.2; package v0.6.0 actually published, DTC D3.1 donor
preparation proved, freeze announced and recent development rehearsal complete.

1. A3.3a assemble reviewed swap with exact six kept-label migration inventories,
   replaces lists, copied/adapted/site-owned tests and public/Studio route map.
   Preserve accounts_user PKs, password hashes, sessions, aliases, OAuth links,
   groups/permissions, content types, generic references and member API behavior.
2. A3.3b wire site domain signal consumers and hooks in retained apps: paid-member
   onboarding eligibility, plans step, CALENDLY true, notification sources for
   plans/bookclub/workshops; configure default/assignment/resume behavior from
   current accounts/views/onboarding.py and onboarding_ai.py. Register custom
   public style hooks without altering member-facing design.
3. A3.3c rehearsal replaces six local apps with package apps, AUTH_USER_MODEL stays
   accounts.User, adds cb_onboarding as required and separately lists approved new
   schema. No unexpected donor-table operation; no fake shortcuts to hide drift.
4. A3.3d during authorized freeze merge/deploy through normal workflow, perform
   site smoke, remove old local apps/templates only where overrides are explicitly
   absent. Keep replaces markers permanently. Close only after required evidence.

Verification: get_user_model().__module__ == community_base.accounts.models;
accounts_user exact count/PK/hash/permission parity; email login, owner-verified
Google login, reset via ses_local, paid onboarding start/resume, notification bell,
Studio tier panels and private member routes. Any unverified production action is
HUMAN pending. Rollback restores prior image/pin plus tested schema state, with
writes frozen for any irreversible transition; retain previous release artifact.

## Phase 4 cards: events

### A4.1 Cut events seams before compatibility

Dependencies C5.2 and A3.2; practical prerequisites A0.2/A0.3/A1.2/A2.1.
Own events/ production imports and matching site consumers; one row below is one
reviewable issue, split again where the call inventory spans multiple behaviors.

| Child | Source/ownership | Required behavior and proof |
|---|---|---|
| A4.1a | events writeup/recording imports; new content/hooks.py | EVENT_WRITEUP_RESOLVER returns URL/title or None; workshop and standalone recording routes unchanged |
| A4.1b | content/models/instructor.py plus EventInstructor references | Map Instructor to events.Host(kind=instructor), preserve assignment rows; WorkshopInstructor stays local and references Host; ids/uniqueness/card links survive |
| A4.1c | events payment checks | kernel access through effective Membership/override policy; free/paid/expired override cases unchanged |
| A4.1d | events imports plans/bookclub/analytics | Emit committed domain signals; consumers live in those apps; rollback emits none and retry does not duplicate downstream actions |
| A4.1e | community/mail references | Shared import APIs only when already adopted and tagged; no direct EmailService or site tier dependency remains |
| A4.1f | integrations Zoom/calendar/observability/banner calls | Shared client/ICS boundaries, AISL banner hook; no network calls in model save/signals or request transaction |
| A4.1g | studio helper imports and final inventory | Shared shell/kernel helpers and recorded import/test matrix; all forbidden remaining imports explicitly resolved |

Verification each child: `uv run python manage.py test events --parallel 4` plus
mapper; final `rg -n '^from (content|payments|plans|bookclub|analytics|integrations)'
events --glob '*.py'` has no forbidden production hits (tests/migrations classified).
Pass exact donor SHA/models/migrations/tests to C4.3. Rollback is per seam; data
mapping child retains old instructor state until backward mapping is verified.

### A4.2 Freeze: adopt shared events

Dependencies C5.3, C4.3, A4.1 and shared identity runtime; freeze/P14 requirements.

1. Inventory every events/urls.py name and path including events/<id>/<slug>,
   join, ICS, calendar, series, legacy recordings and API endpoints. Record
   compatibility bridge ownership; shared slug mode alone is insufficient.
2. Stage tagged app swap; preserve Event, EventSeries, registration, feedback,
   join-click, host and series opt-out rows and timestamps. Enumerate approved
   appended schema, no unexpected donor-table changes. Rehearse all migration gates.
3. During freeze deploy, verify public list/detail and gated join window, series
   registration/occurrence opt-out, ICS, staff Zoom event creation and reminder
   window. External provider checks follow site operator process.
4. Delete local events implementation and only obsolete templates/tests, retaining
   documented site overrides and package replaces markers. Update route/ownership docs.

Verification: data invariants and migration plan, events mapper tests, production
checks recorded by authorized operator, reminder creates one EmailDelivery per
logical send. Rollback image/pin and rehearsed migration reversal; fence pending
integration/reminder work so old/new runners cannot duplicate work.

## Phase 5 cards: courses and coursework

### A5.1 Explicit AISL curriculum mapping

Dependency C5.3 (tagged adoption-ready v0.6.0); runtime accounts/events prerequisites
must be installed first because models refer to shared User/Host.

1. A5.1a produce per-field mapping for Course, Module, Unit, Cohort,
   CohortEnrollment, Enrollment, UserCourseProgress, CourseCertificate,
   ProjectSubmission, PeerReview, CourseInstructor and all FK/M2M references.
   Existing CourseCertificate is in peer_review.py; UserContentCompletion in
   completion.py is a separate behavior and cannot be casually deleted.
2. A5.1b add cb_curriculum/cb_coursework tables; map courses without cohorts to
   one self_paced cohort; reconcile course-level and cohort-level enrollment
   overlap explicitly before insert. Preserve original pk mapping or a durable
   old/new id map, timestamps, progress, certificates and published identities.
3. A5.1c map light project/peer review to Project per cohort and shared review
   graph with criteria text/assignments preserved. Handle uniqueness collisions
   explicitly; score/certificate decisions remain unchanged for existing rows.
4. A5.1d implement COURSE_ACCESS_GRANTS from site CourseAccess and purchase state;
   keep Stripe products, orders and entitlement writes in AISL. Rewire workshop
   instructor references to Host without moving workshop pages/models.
5. A5.1e register shared curriculum parser and replay same immutable source
   snapshot: zero unexplained changes. Switch catalog/detail/unit/progress/
   enroll/Studio/API owner one surface per child with route compatibility tests.
6. A5.1f after development parity and cutover coordination, remove old course
   dispatcher, models and replaced views/templates/API only in separate contract
   issue. Do not drop source data before A5.2 freeze evidence.

Verification: exact course/module/unit/enrollment/progress/certificate counts with
explained enrollment dedup mapping; orphan count zero; same slugs/certificate URLs,
purchased entitlements, drip locks and tier gating; mapper tests. Staff imports a
course, Free member hits upgrade path, eligible paid/purchased member accesses unit,
progress persists, existing certificate downloads. Rollback keeps source plus id
map and writer compatibility until final contraction; schema drops require tested
restore, not a version pin alone.

### A5.2 Freeze: courses cutover

Dependency A5.1 and authorized freeze. Rehearsal includes post-copy content sync and
access graph on development copy no older than seven days.

1. Freeze affected writers and take permitted rollback artifact with exact counts.
2. Deploy through standard AISL workflow and run course catalog/unit/enrollment/
   progress/certificate smoke plus public route compatibility.
3. Authorized production checks cover Basic allowed versus Free paywall, purchase
   grant, persisted progress and drip boundary; preserve all workshop flows.
4. Complete source contraction only at its separately reviewed safe point, record
   passed checks, remove freeze and update canonical status after deploy green.

Rollback restores whole tested schema/application state while writes are frozen.
Any missing course/progress/certificate row stops cutover immediately.

## Phase 6 cards: Relay after D13

All four AISL cards additionally depend on R6.1 gate evidence, even where a canonical
Depends on line omits it. R6.1 needs D1.3, D5.2 and four clean consecutive weeks of
actual DTC production Relay operation. Development traffic or FakeRelay is not proof.

### A6.1 Publish templates to Relay

Dependencies R6.1 and R1.3. Own email_app/email_templates, override mapping,
preview_contexts.py and new root email_templates source/deploy integration.

1. Inventory actual template key set and active overrides; override content wins.
   Operator exports permitted authored content to approved input; do not inspect
   production DB/credentials. Preserve subject/footer/context requirements.
2. Build deterministic source export and synthetic preview comparison; classify
   each whitespace-normalized difference with disposition. Count actual keys,
   not historical fixed 50. No member-specific context in committed templates.
3. Authorized operator imports/publishes through existing Relay contract and
   reports key/version inventory; add deploy draft synchronization with clear
   publish authority. Missing endpoint or context mismatch stops this child.

Verification: all expected keys published exactly once, same rendered semantics,
no secret values in reports; missing keys, unsupported context or a secret-bearing
field fails the import with zero unintended publication. Retry import is
idempotent and cannot create a second version. Rollback retains old
published versions and ses_local source until final cutover.

### A6.2 Reconcile contacts/preferences

Dependencies R6.3, R1.5 plus R6.1/D13. Own new sync_contacts_to_relay command,
preference handlers/callback bridge and subscription UI adapter.

1. Define deduplicated identity union for users/newsletter records; preserve global
   unsubscribe, category state, bounce suppression, verification state, Studio
   tags and tier tags. Record duplicate/conflict disposition before any upload.
2. Implement dry-run/resumable idempotent batch sync using synthetic fixtures;
   secrets and recipient addresses never in logs. Cursor/progress is opaque.
3. Switch ongoing preference synchronization to durable handlers; callbacks
   deduplicate stable event ids and never re-enable hard-bounced/complained member.
4. Move /subscribe double opt-in through Relay link bridge; preserve site-visible
   confirmation, expiration and unsubscribe behavior. Test outage/retry explicitly.

Verification: remote count equals reconciled union, repeat upload creates zero,
preference change converges within one minute in permitted development, unsubscribe
wins over promotional sends. Rollback preserves local preference authority until
both directions reconciled; no dual subscriptions or unsolicited resubscribe.

### A6.3 Switch transport, schedules and history in separate children

Dependencies A6.1, A6.2, R6.2 and full D13 evidence.
Own remaining jobs/tasks schedules, deploy/update_task_def.py, entrypoint.sh,
email_app history/campaign/SES code and site hooks. AWS changes belong in aws-infra.

1. A6.3a inventory every remaining django-q task and cron. Register one handler
   at a time with opaque payload, dedupe, retry/fencing and bounded execution;
   development FakeRelay and real permitted conformance both pass.
2. A6.3b switch development jobs/mail backend to relay, reconcile schedule dry-run
   to zero diff. Drain/fence old queue before removing qcluster. Rollback window
   defines exactly one scheduler/transport owner at any moment.
3. A6.3c replace campaign/SES Studio and API surfaces with package proxy while
   preserving recount, recipient disposition, retry and assume-sent authority.
   Infra operator changes SNS ingress separately through aws-infra process.
4. A6.3d implement idempotent history export/import manifest for EmailLog, SesEvent,
   EmailCampaign, CampaignDelivery, EmailTemplateOverride. Preserve timestamps,
   disposition, template, relationships and per-user order. Import twice, second
   pass zero new rows. Reconcile counts/checksums and sampled synthetic chains.
5. A6.3e only after verified complete history and rollback rehearsal, contract
   old tables and remove email_app in a separate issue; remove hooks only once
   every reader and archive surface uses Relay. No local history is retained as
   permanent substitute for failed migration.

Verification: development reset reaches delivered through callbacks; 3-recipient
synthetic campaign reaches correct dispositions; scheduled job succeeds; historical
and fresh sends appear together in Relay. No unmapped history rows, no duplicate
schedules/sends. `rg` for EmailLog/SesEvent/EmailCampaign outside migrations has only
explicitly approved transient references before final contraction, none afterward.
Rollback before contract restores sole old transport/scheduler owner and pending
work reconciliation; after contract requires verified history/state restore.

### A6.4 Freeze: AISL production on Relay

Dependency A6.3, D13 and announced freeze with recent P14 rehearsal.

1. Authorized operator confirms history/contact/template manifest, old queue
   drain and rollback readiness; standard workflow deploys the reviewed pin/config.
2. Verify signed jobs ingress selftest, 15-minute health schedule, welcome,
   reminder and reset delivery, click/open bridge and unsubscribe through allowed
   operator controls; no production token/database access by agents.
3. Studio campaigns/history show historical and fresh entries; running task
   definition has no qcluster. Record exact run/head, redacted evidence and dates.
4. Remove freeze only after checks pass; open C6.1 transitional-backend removal
   and aws-infra SES identity retirement follow-ups through their own processes.
   C6.1 must not remove backends earlier; its eventual 1.0 release is separate.

Rollback is the practiced transport/schedule ownership transition and compatible
schema restore, never simultaneously running Relay and qcluster for same logical
work. Any delivery/history discrepancy stops and retains previous working identity.

## Luna max handoff contract

One agent gets one groomed child with one behavior family. Explicit model request
from owner permits `gpt-5.6-luna` with maximum reasoning; keep roles independent.
Readiness is never inferred from the calendar, package source directories or
phase numbers. A missing field below means grooming is incomplete.

```text
Repository / issue URL / canonical parent and registered child id:
Role: engineer | tester | PM acceptance | on-call
Worktree path / branch / active lifecycle lease:
Starting main SHA / donor SHA / tagged package SHA:
Read first: AGENTS, PROCESS, role file, testing guidelines, exact card and source files
Verified dependencies with closed issue/merged PR/tag/dev-run evidence:
Owned files and exact symbols; new planned files clearly marked:
Scope: one behavior family and ordered edits
Before/after behavior and non-goals:
Field/route/API/auth/idempotency contract and preserved data invariants:
Synthetic fixtures and exact permitted development inputs:
Commands from affected_tests.py plus filled canonical verification commands:
Expected counts/statuses/error cases and screenshot flows:
Rollback checkpoints and exact stop conditions:
Package plan/docs/status updates required:
Handoff: changed files, command output/counts, screenshots, unresolved evidence
Not run here, needs: precise external dependency/operator evidence
No commit until tester and PM accept; after both approvals the engineer commits
with `Closes #issue`.
Orchestrator merges locally and pushes main; on-call observes Deploy Dev.
```

Ready task: [A0.1 / issue 1563](https://github.com/AI-Shipping-Labs/website/issues/1563),
dependency/tooling only after this document, canonical corrections
and main reconciliation. Subsequent work follows dependency order and the issue
pipeline; no production cutover is implied by starting the first task.
