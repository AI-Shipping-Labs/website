# Short-lived audits, analyses, and plans

This folder holds point-in-time documents: audits, remediation plans, one-off
analyses, and dated reports. These are working documents with a short shelf
life, kept separate from the permanent reference docs in `_docs/` so operators
can tell at a glance which is which.

## What goes here

- Point-in-time audits of the codebase, tests, infra, or content
- Remediation plans tied to a specific audit
- One-off analyses (e.g. perf, accessibility, lint, dependency review)
- Dated status reports

Examples of filenames that belong here (taken from the files already in this folder, all in the canonical `YYYY-MM-DD-<topic>.md` form):

- `2026-04-20-audit.md`
- `2026-05-12-code-smell-audit.md`
- `2026-05-12-test-suite-audit.md`
- `2026-05-13-lint-advisory.md`
- `2026-06-26-datamailer-port-analysis.md`
- `2026-07-09-guest-ui-design-audit.md`

## What does NOT go here

Permanent references that should stay current live at the `_docs/` root, not
in this folder. Specifically:

- `_docs/PROCESS.md`
- `_docs/product.md`
- `_docs/configuration.md`
- `_docs/testing-guidelines.md`
- `_docs/design-system.md`
- `_docs/integrations/` and other long-lived reference subfolders

If a document needs to be kept up to date over time, it is a permanent
reference and belongs at the `_docs/` root.

## Lifecycle

Documents in this folder have an expected expiry of roughly six months. After
that, each file should be:

- Deleted, if its content is no longer useful, or
- Promoted to a permanent doc at the `_docs/` root, if it has stabilised into
  evergreen reference material.

Stale audit files should not accumulate here indefinitely. Periodic sweeps
delete or promote anything older than the expiry window.

## Filename convention

New files should be named with a leading ISO date so chronological ordering is
obvious in directory listings:

```
YYYY-MM-DD-<topic>.md
```

For example:

- `2026-05-20-test-suite-audit.md`
- `2026-06-01-lint-advisory.md`
- `2026-07-15-remediation-plan.md`

Files that are not tied to a single date (ongoing plans, advisories) may omit
the date prefix, but should still be short-lived in spirit and subject to the
same expiry.
