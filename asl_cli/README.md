# asl-cli

Command-line client for the AI Shipping Labs production API. Admin/operator tool wrapping the full `/api` surface with typed subcommands.

## Install

```bash
uv sync
uv run asl --help
```

## Auth

The credential is resolved from `ASL_API_TOKEN` env var -> `API_SHIPPING_LABS_API_TOKEN` in `.env` -> prompt. Existing `/api` commands send the legacy `Token` header. The `integrations` commands use the package `/api/v1/settings` routes and send the same credential as a `Bearer` API key; create a `community_base` staff key with the required `settings.read` and `settings.write` scopes for those commands.
Override base URL with `ASL_BASE_URL` (default `https://aishippinglabs.com`).

## Rate limits

The server throttles each staff token with a rate limit and a concurrency cap. A throttled call gets `429` with `Retry-After` and `code` `rate_limited` or `too_many_concurrent_requests` before the endpoint runs. The client waits and retries those up to 5 times with exponential backoff (capped at 30 seconds), printing `asl: throttled (...)` to stderr. Other 429s are returned as errors. Run commands sequentially; do not fan out parallel calls against production.

## Usage

Commands are organized into groups, max 2 levels: `asl <group> <command>`. Use `--help` at any level:

```bash
uv run asl events --help
uv run asl events create --help
uv run asl events invite-guest --help
```

### Flags instead of JSON

Create/update commands use individual `--flags`:

```bash
uv run asl events create \
  --title "Office Hours" \
  --start-datetime "2026-08-01T17:00:00+02:00" \
  --required-level open --create-zoom
```

Tier levels accept names: `open` (0), `registered` (5), `basic` (10), `main` (20), `premium` (30). Also integers.
Lists accept comma-separated values: `--tags sprint:aug-2026,workshop`, `--host-ids 1,2`.

### Output formats

```bash
uv run asl events list --format table    # aligned table
uv run asl events list --format raw      # compact JSON (for piping)
uv run asl events list                   # pretty JSON (default)
```

A few commands add `csv` to the same `-f` / `--format` option, but only where the
endpoint itself serves `text/csv`. `asl contacts export` is the one today:

```bash
uv run asl contacts export -f csv > contacts.csv       # or --format csv
```

The server's CSV body is written to stdout verbatim — no JSON quoting, original
line terminators, one trailing newline, and nothing else on stdout — so the
redirected file parses directly with `csv.reader` (first row is the export
header). `json` (default), `raw`, and `table` still return the
`{"contacts": [...]}` payload; `table` renders one row per contact.

### Shared comments

List discussions with generic filters or concise owner shortcuts:

```bash
uv run asl comments list --course aihero --module day-1 --unit frontmatter
uv run asl comments list --content-type course_unit --unanswered --limit 100 --format raw
```

JSON and raw formats preserve the complete API response. Table output uses the
stable `id`, `kind`, `content_type`, `author_email`, `created_at`,
`reply_count`, `context`, and `body` columns and may truncate long display
cells.

Every row carries `moderation_state` (`visible` or `hidden`, issue #1894).
Hidden rows left the public thread but keep their votes and replies. Filter
one state with `--moderation-state`; the default returns both so operators can
find what to restore:

```bash
uv run asl comments list --moderation-state hidden --limit 100
```

A homework unit with the homework stepper enabled binds Q&A to the stepper
page (issues #1897 and #1925): `intro` and each question step own a thread,
`learning-in-public` has no Q&A, and `review` shows the unit thread read-only
as the `Earlier homework discussion` archive. A unit filter returns the unit
thread and every homework step thread of that unit; each homework-step row's
`context` names the `homework_step` slug (`review` for the unit thread of a
stepper unit) and links to that step plus `#qa-section`. Isolate one step with
`--homework-step` and a public step slug: `intro` or an authored question id
such as `q2-reflect` returns that step's thread, `review` returns the unit
thread, and `learning-in-public` returns nothing. It requires `--course`,
`--module`, and `--unit`:

```bash
uv run asl comments list --course ai-buildcamp --module foundation \
  --unit homework --homework-step q2-reflect
```

Post one direct plain-text reply from an inline body or a UTF-8 file:

```bash
uv run asl comments reply 412 \
  --body "Here is the answer." \
  --idempotency-key agent-run-20260908-412
uv run asl comments reply 412 \
  --body-file answer.txt \
  --idempotency-key agent-run-20260908-412
```

Supply exactly one body source. The CLI sends one POST and never retries an
ambiguous network failure. Rerun explicitly with the same key to recover: the
server returns the original reply without repeating notifications. The command
uses the standard `ASL_API_TOKEN` / `.env` / hidden-prompt credential resolution
and `ASL_BASE_URL`; it has no token or actor override flag.

Moderate a thread with in-place edit, soft hide, and restore (issue #1894).
`edit` replaces one comment body from `--body` or `--body-file` — no new row,
no notification, and hidden rows are editable (for example to strip a spoiler
before restoring). `hide` removes the comment from every public listing
without deleting the row, its votes, or its replies; hiding a top-level
comment also removes its replies from the public thread until the parent is
restored. `restore` makes a hidden comment visible again; replies that were
not independently hidden come back with it. `hide` and `restore` are
idempotent. None of the three uses `Idempotency-Key`:

```bash
uv run asl comments edit 412 \
  --body "Please discuss the approach without posting the numbers."
uv run asl comments hide 412
uv run asl comments restore 412
```

Invite one ordinary attendee to one event by numeric ID. The command performs
the required event read, invitation write, and invitation read-back itself:

```bash
uv run asl events invite-guest 49 --email guest@example.com
uv run asl events invite-guest 49 --email guest@example.com --dry-run
```

Repeats after a successful send return `already_registered` / `already_sent`
without sending again. `failed_retryable` exits non-zero; rerun the same command
to retry the existing registration.

Announce a verified recap to everyone interested in an event by numeric ID:
registrants/attendees, members of cohorts linked to the event's series, and
readers of a linked book club. Preview first; the dry run sends nothing and
lists each recipient with the reasons they are included:

```bash
uv run asl events notify-recap 58 --dry-run
uv run asl events notify-recap 58
```

Each person gets one email per event; rerunning only reaches people not yet
emailed. A non-zero `failed` count exits non-zero; rerun to retry.

### Sharing one sprint plan with its member

A plan is a draft until it is shared. Until `shared_at` is set, the member's
dashboard keeps showing the "Your plan is being prepared" card, no matter how
complete the content is. Creating, importing, or patching plan content never
shares it.

One command performs the default delivery for exactly one plan:

```bash
uv run asl plans send-ready 116 --dry-run   # preview, zero writes
uv run asl plans send-ready 116             # share + notify once
uv run asl plans send-ready 116 --format table
```

It calls `POST /api/plans/<id>/send-ready-email`, which sets `shared_at`,
creates the `plan_shared` bell notification, sends the transactional
`plan_shared` email, and records the durable ready-email log — all exactly
once. `visibility` is never changed.

The `ready_email.status` field is one of:

| Status | Meaning |
|---|---|
| `eligible` | Preview only. A live run would send. |
| `sent` | This run completed the default delivery. |
| `already_sent` | A previous default delivery succeeded. Nothing was sent. |
| `already_shared` | The plan was shared through Studio Re-share or the legacy path without a ready log. Nothing was sent. |
| `failed_retryable` | Delivery failed. The plan is still unshared; run the same command again. |
| `in_progress` | Another send for this plan is mid-flight. Nothing was sent. |

`sent`, `already_sent`, `already_shared`, `in_progress`, and previews exit `0`.
`failed_retryable` prints the structured result and exits `1`, alongside the
usual API/auth/not-found exit `1`.

There is no `--force`, `--resend`, or bulk fallback. Default delivery is
idempotent and can never notify the same member twice. To notify a member again
on purpose, use the confirmed `Re-share with member` action on
`/studio/plans/<id>/`; that is the only path that deliberately fires a second
bell notification and email. `asl sprints send-plan-emails <slug>` remains the
separate sprint-wide bulk action.

### Stripe tier reconciliation reports

The primary paying-users-versus-Stripe check is one read-only command:

```bash
uv run asl tier-reconcile run
```

It enqueues the existing full-cohort reconciliation API and waits for the run by
default. It polls every 2 seconds for up to 15 minutes, then prints the persisted
summary and non-OK findings. `run`, `list`, `show`, and `wait` only report data.
They can't change website access or call Stripe directly from the CLI.
Their default format is `table` for operator review.

Use these commands to start, resume, filter, and export reports:

```bash
# Start a long run without waiting, then resume it later
uv run asl tier-reconcile run --no-wait
uv run asl tier-reconcile wait <run-id>

# Show one run without polling, or browse newest-first history
uv run asl tier-reconcile show <run-id>
uv run asl tier-reconcile list --page 1 --page-size 100
uv run asl tier-reconcile list --all-pages

# Narrow findings using server-owned filters (combined with AND)
uv run asl tier-reconcile show <run-id> \
  --filter actionable --tier main \
  --classification ended_subscription_still_entitled

# Fetch one bounded findings page; otherwise all next_cursor pages are followed
uv run asl tier-reconcile show <run-id> --page 2 --page-size 100

# Automation receives one JSON document even when pages are combined
uv run asl tier-reconcile wait <run-id> --format json
uv run asl tier-reconcile show <run-id> --format raw > report.json
```

`show`, `wait`, and completed `run` accept `--filter
all|actionable|scheduled|warnings`, `--tier basic|main|premium|free`, and any
server-supported `--classification` value. The CLI doesn't fix the
classification set in the client. Findings auto-page by following each returned
`next_cursor`. `--page N` selects one bounded page instead, and page sizes must
be 1–500. `list` defaults to page 1 with 100 rows. It follows every cursor only
when you pass `--all-pages`. Don't combine an explicit `--page` with
`--all-pages`.

Polling messages go to stderr, so JSON/raw stdout remains one parseable
document. Override waiting with positive `--poll-interval SECONDS` and
`--timeout SECONDS`. A timeout or Ctrl-C stops only local polling. The
server-side run continues, and the printed `asl tier-reconcile wait <run-id>`
command resumes it. The client never retries the enqueue POST after an ambiguous
network error.

The default output protects member data. Table output masks the email local part
and omits Stripe customer/subscription IDs. JSON/raw output redacts `email`,
`stripe_customer_id`, `current_subscription_id`, and `stripe_subscription_id`.
It also adds `pii_redacted: true`. Use `--include-pii` only when you need full
member emails and Stripe identifiers, and keep that output in an approved
location.

Use exit `0` for a successful report, even when it has findings. API, auth,
network, not-found, and failed-run errors use exit `1`. Invalid CLI usage uses
exit `2`, and a local wait timeout uses exit `3`. You can opt into exit `4`
after the report prints with `--fail-on actionable|warning|any`; the default is
`--fail-on never`.

The older synchronous/email-targeted `tier-reconcile diagnostics` command
remains read-only. Use the separate `tier-reconcile apply --data ...` command
only for guarded writes. The server still requires explicit `dry_run=false`
plus `confirm=apply_stripe_truth`, and report commands never invoke it. The
deprecated `scripts/tier_reconcile_prod.sh` path delegates only to
`uv run asl tier-reconcile run`.

Interpret canonical Stripe values literally. `past_due` and `unpaid` remain
dunning states and aren't relabelled canceled, non-paying, downgraded, or
churned. Members with scheduled cancellation keep paid access through Stripe's
period end. The report doesn't derive effective-tier/override state,
notification state, or a grace deadline. Issue #1413 owns the future seven-day
failed-payment grace policy. We can add new canonical server fields later
without moving that policy into the CLI.

### Content lookup by content_id

Resolve any synced row (article, project, tutorial, download, workshop,
workshop page, marketing page, event, course, course module, course unit) by
its frontmatter `content_id` UUID. Staff see drafts and gated lessons in full.

```bash
# Full JSON: metadata, source repo/path/commit, stored markdown and HTML
uv run asl content get 7c9e6679-7425-40de-944b-e07fc1f90ae7

# Metadata and body lengths only (sends include_body=false)
uv run asl content get 7c9e6679-7425-40de-944b-e07fc1f90ae7 --no-body

# One-row summary: type, title, url, is_public, short commit, updated_at
uv run asl content get 7c9e6679-7425-40de-944b-e07fc1f90ae7 -f table
```

Verify a content-repo edit reached prod: after pushing to, for example,
`AI-Shipping-Labs/ai-buildcamp-course`, print the stored text of the edited
lesson and compare `source.commit` with the pushed commit.

```bash
uv run asl content get <lesson-content-id> --body markdown | grep "New paragraph"
uv run asl content get <lesson-content-id> --no-body | jq -r .source.commit
uv run asl content get <lesson-content-id> --body html | grep -o '<h2 id="[^"]*"'
```

`--body markdown|html` writes only that raw string plus one newline, so it
pipes cleanly into `grep` or `diff`; it ignores `--format` and cannot be
combined with `--no-body`. Downloads store no HTML, so `--body html` exits
non-zero for them. A non-UUID argument fails locally without an HTTP call.

### Course cohorts and their event series

A dated cohort's session units show the event at their `session_position` in
the cohort's linked event series. List a course's cohorts with their series,
enrollment counts, and `warnings` (`no_event_series`, `empty_event_series`,
`missing_session_positions`), then relink a cohort by series slug or id. `KEY`
is the cohort's external key (the `?cohort=` value).

```bash
uv run asl sprints course-cohorts ai-buildcamp
uv run asl sprints course-cohort-update ai-buildcamp 4 --event-series buildcamp-office-hours-cohort-4
uv run asl sprints course-cohort-update ai-buildcamp 4 --start-date 2026-09-14 --end-date 2026-11-09
```

### Course coursework inventory

Count a course's legacy project attempts, submissions, peer reviews, pooled
batches and certificates (read-only). This is the baseline for moving projects
onto `community_base.coursework`.

```bash
uv run asl sprints course-coursework-inventory ai-buildcamp
```

### Escape hatch

```bash
uv run asl raw GET /api/events -p status=upcoming
uv run asl raw POST /api/v1/settings/import --data '{"settings":{"SITE_BASE_URL":"https://aishippinglabs.com"}}'
```

## Command groups

`events`, `event-series`, `users`, `sprints`, `plans`, `comments`, `contacts`, `content`, `tier-overrides`, `campaigns`, `integrations`, `sync`, `worker`, `triggers`, `onboarding`, `redirects`, `utm-campaigns`, `hosts`, `articles`, `tier-reconcile`, `ses-events`, `crm-export`, `cleanup-gates`, `openapi`, `raw`
