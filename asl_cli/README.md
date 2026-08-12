# asl-cli

Command-line client for the AI Shipping Labs production API. Admin/operator tool wrapping the full `/api` surface with typed subcommands.

## Install

```bash
uv sync
uv run asl --help
```

## Auth

Staff token resolved from: `ASL_API_TOKEN` env var -> `API_SHIPPING_LABS_API_TOKEN` in `.env` -> prompt.
Override base URL with `ASL_BASE_URL` (default `https://aishippinglabs.com`).

## Usage

Commands are organized into groups, max 2 levels: `asl <group> <command>`. Use `--help` at any level:

```bash
uv run asl events --help
uv run asl events create --help
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

### Escape hatch

```bash
uv run asl raw GET /api/events -p status=upcoming
uv run asl raw POST /api/integrations/settings --data '{"updates":[...]}'
```

## Command groups

`events`, `event-series`, `users`, `sprints`, `plans`, `contacts`, `tier-overrides`, `campaigns`, `integrations`, `sync`, `worker`, `triggers`, `onboarding`, `redirects`, `utm-campaigns`, `hosts`, `articles`, `tier-reconcile`, `ses-events`, `crm-export`, `cleanup-gates`, `openapi`, `raw`
