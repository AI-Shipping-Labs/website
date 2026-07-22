# Landing-page audits — index and reliability caveat

Audit date: 2026-07-21. Eleven top-nav landing pages, audited by parallel agents against a local dev server on `127.0.0.1:8766` with content synced from a local clone of `AI-Shipping-Labs/content`.

Read this page before acting on any finding in the reports below.

## Reports

| Report | Pages | Type |
|---|---|---|
| `blog-resources-downloads-layout.md` | `/blog`, `/resources`, `/downloads` | Layout |
| `courses-community-layout.md` | `/courses`, `/sprints`, `/activities` | Layout |
| `projects-interview-layout.md` | `/projects`, `/interview` | Layout |
| `workshops-events-layout.md` | `/workshops`, `/workshops/catalog`, `/events` | Layout |
| `landing-copy.md` | `/blog`, `/resources`, `/downloads`, `/courses`, `/sprints`, `/activities` | Copy |
| `landing-copy-2.md` | `/projects`, `/interview`, `/workshops`, `/workshops/catalog`, `/events` | Copy |

These reports are proposals, not a record of shipped work.

Status, re-verified against the tree on 2026-07-21 after the 19:20-19:56 commit burst: a narrow slice landed. The `/resources` category-visibility bug, the `/resources` and `/projects` tag filters, the workshop catalog facet accordions and chip scale, and the three internal IA notes are done. Roughly 20 of about 110 findings. Everything else in these files is still open and is tracked in issues #1325 (CTAs and empty states), #1326 (copy), #1327 (design-system conformance) and #1330 (product decisions). Do not read a finding here as unfixed without checking the code first — `/resources` in particular is done.

## The screenshots were taken against a stale server

Discovered after the audits completed, 2026-07-21.

Port 8766 was held by a `runserver --noreload` process started at 18:17:26 and left over from an earlier session. A second `runserver` aimed at 8766 fails to bind and exits, but a health check against the port still returns 200 — from the old process. `scripts/capture_screenshots.py` is designed to detect and reuse a server already on 8766, so every agent reused the stale one.

Because the process ran with `--noreload`, its templates were frozen at 18:17:26. The screenshots therefore do not include:

- the container width and gutter fixes (committed 18:55 onward), so `/sprints` renders at its old `max-w-5xl` in every capture
- the concurrent session's commits from the same window: `bc413d15` unifying series and solo event cards, `1532547b` normalising arrow icons, `39405dba` dropping the duplicate About menu entry

Consequences:

- Any finding about event card alignment on `/events` is suspect. The workshops/events audit reported single and series cards misaligning, and independently noticed that the on-disk templates disagreed with the rendered page. That discrepancy was the stale server, and `bc413d15` may already have fixed the underlying issue. Re-verify before acting.
- Any finding about gutters or the `/sprints` container width describes the pre-fix state.
- The courses/sprints/activities audit is the exception: it detected the staleness, applied the new class to the live DOM through Playwright, and captured both versions. Its `/sprints` conclusions are sound.

The server was restarted at 19:20 and now serves current templates. Before any future audit run, verify freshness rather than trusting a 200: check the listening process start time (`ps -eo pid,lstart,cmd | grep runserver`) against the last template edit, or curl a string you know you just changed.

## The local dev database is not representative of production

Screenshots were taken against a local environment whose content differs from production in three ways that directly affect visual findings. Verified 2026-07-21.

| Local artifact | Cause | Production reality |
|---|---|---|
| Every project and most article covers resolve to `cdn.example.com` and render the fallback rocket | `sync_content --from-disk` without S3 credentials does not upload images; the content repo stores relative paths such as `images/cover.jpg`, which the real pipeline rewrites to `cdn.aishippinglabs.com` | Covers resolve and render normally |
| `QA Banner Project 815`, `Test Project`, `QA Banner Download 815` are published and sort first | Local seed and test-run residue; these titles appear nowhere in the content repo | Not present |
| `/downloads` has essentially one item | Same residue; the single visible download is QA test data | Populated |

Consequences for the reports:

- Findings about cover-image treatment, fallback walls, media bands, and "ragged" card media are UNRELIABLE. They may describe a local artifact. Re-verify against production or a properly synced environment before acting.
- Findings about sparse and empty states, and about what ranks first in a list, are UNRELIABLE for the same reason.
- Findings about structure, hierarchy, missing CTAs, heading scale, filter UI, gating, terminology, and copy are RELIABLE. They do not depend on which rows exist.

Where a report argues from the design system rather than from a screenshot, the argument stands on its own. For example the objection to the blog list media band rests on the Card Media Slots decision in `_docs/design-system.md`, not on the local fallback rocket, and is unaffected by the caveat above.

## Verified findings

These were checked independently against the running application rather than accepted from the audit reports.

| Finding | Evidence | Status |
|---|---|---|
| `/resources` hides 22 of 41 published curated links | `content/views/pages.py` uses `category_order` for both display order and the `category__in` visibility filter. Commit `ddd61abd` swapped `tools`/`models` out of that list while adding `workshops`/`articles`, hiding 21 `tools` and 1 `models` links. DB counts confirm 41 published, 19 visible | Confirmed; needs a product decision |
| Internal IA notes shipped as public copy on three pages | See the table below | Confirmed |
| Tag filtering is dead code on two pages | `/projects` and `/resources` both compute `all_tags` and support `?tag=`, but neither template renders any control: `grep -c 'all_tags|\?tag='` returns 0 for `projects_list.html` and `collection_list.html` | Confirmed |
| `View Plans` CTA collides with member sprint plans | `templates/content/collection_list.html:109`; the tier glossary reserves "plan" for sprint plans | Confirmed |

### Internal IA notes shipped as public copy

Three separate pages explain the site's own content taxonomy to visitors, in the vocabulary the team uses internally. Two independent audits found these without prompting. All verified 2026-07-21.

| Location | Shipped text | Problem |
|---|---|---|
| `templates/events/events_list.html:118` | "Recordings from past events stay here for legacy discovery. When a recording has a linked workshop, the workshop is the canonical learning artifact." | This is the page the nav labels "Past Recordings". "Legacy discovery" and "canonical learning artifact" are internal terms; the sentence explains a data model, not a benefit |
| `templates/content/collection_list.html:24` | "...without treating this page as the home for every community activity or recording." | An instruction to whoever maintains the page, rendered as the page's lead paragraph |
| `templates/content/workshops_list.html:21` | "Workshop pages are durable hands-on learning artifacts..." | Describes the content type as a system object rather than saying what a visitor gets |

The shared cause is worth noting: each of these reads like a decision rationale that was written to justify an IA change and then pasted into the template as body copy. It is a cheap, low-risk category to fix, and it is the copy a first-time visitor reads first.

## Cross-cutting themes

Recurring across independent audits, which raises confidence that they are real rather than one auditor's taste:

1. No landing page has a body CTA above the fold. Every page opens with a heading and immediately begins its list. Conversion depends on the footer.
2. Filter affordances are computed server-side and never rendered — the same defect on at least two pages, suggesting a partial refactor that removed the controls.
3. Terminology drifts against `_docs/product.md`: `cohort` used for sprints, `Structured Learning Paths` on `/courses` colliding with the separate Learning Paths nav item and content type, `resources` overloaded for both downloads and curated links.

## Open product decisions

Collected from all reports; none are engineering calls.

- Restore `tools` and `models` to `/resources`, and in what order.
- Blog list covers: comply with the Card Media Slots decision and drop the band, or amend the decision to conditional-explicit.
- Whether Learning Paths remains a separate nav item, which decides whether the `/courses` headline rename is mandatory.
- Tag density: render all tags (45 on `/projects`, 51 on `/blog`) or a curated top-N.
- Whether the 5 coming-soon interview categories should be shown, hidden, or demoted; hiding leaves a one-card page.
- Pagination pattern — none exists to copy, and `/blog` is already ~10000 px tall on mobile.
