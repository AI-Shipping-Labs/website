# Buildcamp: Maven export versus the AISL course source

Captured 2026-09-21. The attached Maven export is a cohort 4 snapshot: lessons
were captured 2026-09-17 and office hours on 2026-09-21. The correct AISL
comparison target is `AI-Shipping-Labs/ai-buildcamp-course` `main` at
`0370dcdf5c2fbfcba942bba19dfffeae9b9732e7`. It contains the earlier
`restructure-1675-maven-tree` work plus the `00-logistics` change from #1774.
Production had last synced the older flat commit `880fcc5` when checked after
the push; this audit distinguishes the published source from the live site.

## What is already built in the source repo

| Capability | Source evidence |
|---|---|
| Nested syllabus | `00-logistics` first, then nine week modules and 52 week submodules: 44 content and eight session submodules. |
| Logistics split | Five course-wide lessons are direct units in `00-logistics`; Week 1 Overview stays in its own Week 1 submodule after Session 1. |
| Bonus separation | 25 bonus submodules; 112 units carry `is_bonus: true` (including optional units inside core submodules). |
| Sessions | Eight `kind: event` units with `session_position: 1` through `8`. |
| Content preservation | `MAPPING.csv` accounts for all 215 former files: 197 preserved IDs, 13 dropped units and five excluded answer keys. Eight session units have new IDs. |
| URL changes | `URL_CHANGES.csv` lists 227 old/new URL rows, including 197 preserved unit URLs that change. |
| Answer-key exclusion | `course.yaml` now uses `**/solution.md` for the nested tree. |

The source curriculum has 205 units: 197 preserved content units and
eight session units. It is not flat. The repo also has
`RESTRUCTURE_REPORT.md` with the reconciliation and its intentional decisions.
The source has been pushed to GitHub `main`, but the public AISL course still
reflects the older source until a production sync. The production content
source reported `missing_secret` and no sync request after this push, so no
automatic sync occurred.

## Differences from the attached Maven export

| Area | Maven export | AISL source | Action |
|---|---|---|---|
| Overall inventory | 207 linked pages: 97 core and 110 bonus; events excluded from that count | 197 content units plus eight session units | Review individual page mapping; the totals include different kinds of items. |
| Pre-course | Three `Start here: Welcome to Maven` pages | Deliberately omitted as Maven-specific onboarding; `00-logistics` instead contains five existing course-wide lessons | Rewrite those lessons for AISL and add any further orientation learners need. |
| Week 2 | `Week overview` lesson | No matching unit in the overview submodule | Write or intentionally omit it. |
| Week 7 | `Hackathon` and `Guardrails workshop` pages | No matching units | Decide whether either belongs in the next cohort. |
| Week 8 | Code-analysis `Introduction`, `Use Cases Library` placeholder, and three student-example pages | No matching units/whole sections | Decide which are substantive and supply their content. |
| Week 9 | `Student Project Presentations` and a `Project deadline` item | An empty old `Overview` stub is mapped to presentations; no project-deadline object | Replace the low-confidence stub and model the deadline in the actual project workflow. |
| Older content | Maven collapses Elasticsearch, Qdrant and deployment to one placeholder each | Branch preserves four lessons in each of those three areas | Keep the richer material only if it is wanted in the next course; mark optional where appropriate. |
| Removed content | No live Maven equivalents for eight old multi-agent/graph lessons | Branch drops them (part of 13 dropped units) | Review intentional retirement and redirects, especially for learners with progress. |
| Office hours | Eight dated 2026 events, mostly Monday 17:00 Berlin; Session 7 Tuesday 3 November 18:00 | Eight position-based event units | Confirm positions against the real AISL event series and create new-cohort dates/Zoom links. |
| Videos | 137 distinct video IDs on 136 pages with video | 107 distinct video IDs | Reconcile 31 export-only IDs, mostly bonus; four core pages have export-only IDs. |

The eight absent in-week pages are named in the table (1 + 2 + 5). The three
Maven pre-course pages are a separate, deliberate omission. The export also
contains ten pages with no written editor content; do not import capture notes
as learner-facing lessons. Two exported bonus pages have two video links, while
the course frontmatter supports one `video_url`; decide how both will appear.

The source repo's `RESTRUCTURE_REPORT.md` says "11 Maven lessons with no old-content
match" but its table names eight in-week items. Adding the three separately
omitted pre-course pages explains 11. The export's 207 linked pages include a
Week 9 project-deadline item; the earlier live JSON count of 206 counts only
`item_type: lesson`. These counts should be labeled by item type in issue
#1675 instead of treated as conflicting lesson totals.

## Work still needed for a course run on AISL

| Workflow | Current source state | Needed for the next cohort |
|---|---|---|
| Checkout and enrollment | `course.yaml` still links to Maven, uses `program_label: Maven` and `maven_course_key`, and declares only cohort key `4`. | Add the next cohort and decide whether checkout/enrollment also move to AISL. If so, replace the Maven checkout/webhook dependency. |
| Homework | Homework bodies still link to `courses.datatalks.club/ai-buildcamp-3/homework/...`; the Course Management Platform lesson says questions are on Maven and submissions elsewhere. | Provide AISL submission/scoring/progress and rewrite every instruction and URL. |
| Capstone and peer review | Project pages still contain cohort 3 submission links and March/April dates. | Provide AISL project submission/review with next-cohort deadlines and update the pages. |
| Office hours | Event slots are present, but `session_position` was assumed from Maven numbering rather than checked against production `EventSeries`. | Verify the exact series positions, new dates, join links, recordings and recap behavior. |
| Progress and redirects | The source preserves IDs and includes `MAPPING.csv`/`URL_CHANGES.csv`. | Run the website-side sync dry run, resolve all 13 dropped-unit redirects and verify existing learner progress before a live sync. |
| Publication | The nested tree and `00-logistics` are now on GitHub `main`; production last synced `880fcc5`. | Complete platform preflight and redirects, then sync and verify the public course. |

The source still contains an old Course Management Platform orientation lesson
and other cohort-specific references. Importing the Maven export verbatim would
also carry stale platform instructions, so content editing is required in
addition to the structural migration.

## Tracking

The content repo `main` implements much of [website issue #1675](https://github.com/AI-Shipping-Labs/website/issues/1675)
and the structure in [#1774](https://github.com/AI-Shipping-Labs/website/issues/1774).
The remaining website-side sync, dry-run and redirect work is tracked there;
courses, events and coursework are sequenced in [umbrella #1662](https://github.com/AI-Shipping-Labs/website/issues/1662).
The website's deployed support still needs verification. Before syncing the
new source to production, review the mapping and dropped lessons, run the real
preflight against production or a current production snapshot, and verify the
complete learner flow on AISL.
