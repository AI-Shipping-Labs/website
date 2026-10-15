# Landing-page audits (2026-07-21) — archived index

Archived point-in-time evidence. Nothing here describes current product truth;
the current templates, `_docs/design-system.md`, and `_docs/product.md` are
authoritative.

## What happened

On 2026-07-21 parallel agents audited eleven top-nav landing pages (layout and
copy for `/blog`, `/resources`, `/downloads`, `/courses`, `/sprints`,
`/activities`, `/projects`, `/interview`, `/workshops`, `/workshops/catalog`,
`/events`) against a local dev server on `127.0.0.1:8766` with content synced
from a local clone of `AI-Shipping-Labs/content`. The six reports were
proposals, about 110 findings in total.

The evidence had two known defects:

- Stale server. Port 8766 was held by a leftover `runserver --noreload`
  process from 18:17:26, so every capture showed templates frozen before that
  evening's container-width fixes and event-card unification (`bc413d15`). A
  health check still returned 200 from the old process. Before any audit,
  verify freshness instead of trusting a 200: compare the listening process
  start time (`ps -eo pid,lstart,cmd | grep runserver`) with the last template
  edit, or fetch a string you just changed.
- Unrepresentative data. Local covers resolved to `cdn.example.com` because
  `sync_content --from-disk` without S3 credentials uploads no images, and QA
  residue (`QA Banner Project 815`, `Test Project`, `QA Banner Download 815`)
  sorted first. Findings about cover media, sparse or empty states, and list
  ranking were unreliable; findings about structure, hierarchy, CTAs, heading
  scale, filters, gating, terminology, and copy did not depend on rows.

## Disposition

The four tracking issues below were closed as resolved or superseded, each with
an item-by-item disposition of the findings it tracked. Two verified findings
are not named in those issues but are fixed in current code: the `View Plans`
CTA no longer exists in `templates/`, and the `/projects` and `/resources` tag
filters now render (`projects_list.html`, `collection_list.html`). The six
reports were removed by #1840.

| Report (removed) | Disposition |
|---|---|
| `blog-resources-downloads-layout.md`, `courses-community-layout.md`, `projects-interview-layout.md`, `workshops-events-layout.md` | CTAs and empty states: #1325. Design-system conformance: #1327. |
| `landing-copy.md`, `landing-copy-2.md` | Headlines, eyebrows, and metadata: #1326. |
| "Open product decisions" from all reports (resources categories, blog covers, Learning Paths nav, tag density, interview coming-soon rows, pagination, and others) | All 15 questions answered by current contracts: #1330. |

`/activities` no longer exists as a page; #1402 redirects it to
`/membership#activities`.

## Finding kept as a regression contract

`content/tests/test_internal_copy_lint.py` cites this finding and keeps the
category out of shipped markup.

### Internal IA notes shipped as public copy

Three pages explained the site's own content taxonomy to visitors in internal
vocabulary (verified 2026-07-21):

| Location at the time | Shipped text | Problem |
|---|---|---|
| `templates/events/events_list.html:118` | "Recordings from past events stay here for legacy discovery. When a recording has a linked workshop, the workshop is the canonical learning artifact." | "Legacy discovery" and "canonical learning artifact" are internal terms; the sentence explains a data model, not a benefit |
| `templates/content/collection_list.html:24` | "...without treating this page as the home for every community activity or recording." | An instruction to whoever maintains the page, rendered as its lead paragraph |
| `templates/content/workshops_list.html:21` | "Workshop pages are durable hands-on learning artifacts..." | Describes the content type as a system object rather than what a visitor gets |

Each read like a decision rationale written to justify an IA change and then
pasted into the template as body copy.
