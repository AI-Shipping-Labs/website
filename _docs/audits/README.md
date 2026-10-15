# Short-lived audits, analyses, and plans

This folder holds point-in-time documents: audits, remediation plans, one-off
analyses, designer reports, and dated status or migration records. They are
evidence about a moment, not current product truth. Evergreen references live
at the `_docs/` root; [`../README.md`](../README.md) maps both classes.

## Adding a file

- Name it `YYYY-MM-DD-<topic>.md` so directory order is chronological. Files
  not tied to one date (ongoing trackers) may omit the prefix but follow the
  same retention rules.
- State what was examined, when, and against which environment, so a later
  reader can judge whether a finding still applies.
- If a document must be kept current over time, it is an evergreen reference
  and belongs at the `_docs/` root instead.

## Retention

Review files here periodically, but age is never the reason to delete one. A
file may be deleted only when all of these hold:

- Its unresolved findings are closed, moved into open issues, or recorded in a
  canonical current document.
- It is not the only source of a decision, rejected alternative, measurement,
  incident fact, migration or recovery record, or rationale that would prevent
  repeated work.
- No open GitHub issue relies on its path, or that issue is updated with a
  durable replacement first.
- Repository references to it are updated.

A closed issue, an expiry guideline, zero inbound links, or an implementation
date is supporting evidence only. Record each deletion with its replacement
destination in the issue that removes it. When a file has stabilised into
evergreen material, promote it to the `_docs/` root instead.
