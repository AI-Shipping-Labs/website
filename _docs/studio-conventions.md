# Studio Display Conventions

## Pills vs Plain Text

Pills are rounded background chips with coloured text. Use them for short,
enumerated state values where the operator's eye should land on the value
first. Use plain text for free-form data such as email, name, IDs, and dates.

A field renders as a pill when all of the following are true:

1. It belongs to a closed enumeration, such as Free / Basic / Main / Premium; Active / Inactive / Staff; Subscribed / Unsubscribed; Member / Not in Slack / Never checked; Override / From Stripe / Default.
2. The set of values is small enough to colour-code distinctly.
3. The value is a state, not an identifier.

Provenance and qualifier badges, such as Override, From Stripe, Default, and
Trial, render as separate small pills next to the primary value pill. They do
not render as parenthesised suffixes inside the value.

Example screenshot description: in a Studio membership row, the tier appears as
a coloured `Premium` pill. If a temporary override is active, an amber
`Override` pill with the shield icon sits beside it. The row never reads
`Premium (override)`.

## Canonical Pill Colours

| Field | Value | Tailwind classes |
|---|---|---|
| Tier | Free | `bg-muted text-muted-foreground` |
| Tier | Basic | `bg-blue-500/20 text-blue-700 dark:text-blue-300` |
| Tier | Main | `bg-accent/20 text-accent` |
| Tier | Premium | `bg-amber-500/20 text-amber-700 dark:text-amber-300` |
| Tier source | Override | `bg-amber-500/20 text-amber-700 dark:text-amber-300 border border-amber-500/30` plus the shield icon |
| Tier source | From Stripe | `bg-muted text-muted-foreground` |
| Tier source | Default | `bg-muted text-muted-foreground` |
| User status | Active | `bg-green-500/15 text-green-700 dark:text-green-300` |
| User status | Staff | `bg-blue-500/15 text-blue-700 dark:text-blue-300` |
| User status | Inactive | `bg-red-500/15 text-red-700 dark:text-red-300` |

Newsletter and Slack pills keep their existing colours.

## Operations surfaces

Operations is the sidebar group that hosts read-only audit logs and
infrastructure-facing tooling. Current entries: Content sync, Worker,
SES events (`/studio/ses-events/` — read-only browser over
`email_app.SesEvent`, see issue #763), Redirects, Settings, API tokens.

## List Page Baseline

Studio model-list pages use the shared table, filter, action, empty-state,
and pager helpers unless a page documents a specific operational exception.

Default baseline:

- Server-side pagination uses 25 rows per page for normal Studio tables.
  Pager links preserve active query parameters and clamp invalid or
  out-of-range `page` values to a valid page.
- The primary search/status row uses `{% studio_list_filter %}`. Search-only
  lists pass `status_kind=None`; publication, event, campaign, and project
  lists use the shared status dropdown options.
- Filtered zero-result states render `{% studio_empty_state 'filter' %}` with
  a clear-filters URL. Truly empty fresh states render
  `{% studio_empty_state 'fresh' %}` and keep a create/navigation CTA where
  that page has one.
- Source-managed content rows where Studio is mostly review/publish use
  `View` or `Review` as the primary row action. Studio-native editable
  operational rows use `Edit` or the main management task as the primary row
  action. Sensitive actions, including impersonation/Login as, stay secondary
  or form-only actions and are never promoted as the primary list action.
- Date/time cells use the operator vocabulary (`operator_date`,
  `operator_datetime`, or `operator_datetime_seconds`) and apply
  `whitespace-nowrap` where a date, time, or age token must stay readable.
- Empty diagnostic columns should be populated with useful values when data
  exists, or removed. Current retained diagnostic columns: SES Bounce type
  shows SES `bounce_type` / `bounce_subtype` for bounce rows, SES Email log
  links to the campaign when correlated, and Event series Cadence falls back
  to `No occurrences scheduled` when no honest cadence can be derived.

Deliberate exceptions:

- `/studio/plans/` keeps its sprint/member/search filter panel because it has
  two relational filters in addition to text search. It still uses the shared
  table actions, empty states, and pager.
- `/studio/imports/` keeps source and dry-run select filters without a text
  search field. It uses the shared empty states and pager, and preserves those
  filters across pager links.
- `/studio/ses-events/` keeps type chips plus a secondary date/bounce filter
  panel because SES triage needs those facets visible. It still uses the
  shared table, empty states, row action styling, and pager.
- `/studio/worker/` is an operations dashboard rather than one canonical model
  list. The pending queue table paginates because it can grow unbounded; recent
  and failed task sections remain capped operational snapshots.
- API scope is unchanged for this baseline pass. No production API endpoints,
  schemas, CLI commands, or authentication behavior are added or modified by
  list-page presentation cleanup.
