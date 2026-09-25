# AI Buildcamp request tracker — 2026-09-25

This is the working checklist for requests raised during the September 24–25
course iteration. A checked item needs code and rendered or test evidence;
"implemented locally" does not mean deployed. Source content must not be pushed
until the site and shared parser that consume it are deployed, because the
course repository push triggers production sync.

## Release and data

| Request | State | Evidence / next check |
| --- | --- | --- |
| Run AISL on localhost 8030 from the correct repository and main checkout | Needs recheck | Verify live process and route after current edits. |
| Keep focused commits; push AISL; deploy dev; promote to prod | In progress | `94eefe8` reached dev, Deploy Dev `36164802288` green; local grouping commit `aa8f81e` awaits community-base v0.5.8. Prod promotion remains open. |
| Register and sync Buildcamp content into dev, verify rendered course pages | Open | Dev Buildcamp routes currently 404 because source is absent. |
| Preserve/recover lost work, reconcile worktrees/branches, bring needed changes to main | Needs audit | Prior branches and worktrees were reviewed in this session; verify no remaining valuable unmerged work before closure. |
| Do not move Cohort 4 end from November 22 | Decision | User explicitly confirmed. |

## Shared course structure and learner UI

| Request | State | Evidence / next check |
| --- | --- | --- |
| Use one course content shell for Buildcamp, Python, self-paced and workshops; keep common code DRY | Partially implemented | Compare rendered page widths, right margins, top offsets, and content template usage. |
| Make every course cohort-based; create self-paced cohort for Python and similar courses, eventually across repos | Open | AISL first, then community-base/DTC. |
| Show enrolled cohort, current cohort, and past cohorts 1–3 where available | Needs rendered check | Source past cohorts from CMP; do not label current cohort "next." |
| Course home should show current module, current homework and useful next actions without repeating Project in multiple places | Needs rendered check | See home JTBD and design reviews in this directory. Course-specific information belongs on Home, not landing. |
| Home/course layout 3xl width, consistent margins, sensible spacing; avoid extra cards/chrome | Needs rendered check | Recheck attached screenshots and current 8030 rendering. |
| YAML syllabus section separation; homework/capstone as first-level rows | Implemented locally | AISL commits `94eefe8`, `aa8f81e`; latter requires shared `syllabus_section` release and fresh rendering. |
| Canonical first-level overview/session/homework URLs; capstone under `/homework-capstone`; `/c/<uuid>` share redirect | Needs route check | Old nested aliases/redirects were specifically rejected except `/c/<uuid>`. |
| Subtle expansion animation; sidebar spacing and overflow fixes | Needs rendered check | Review screenshot-specific requests. |
| Session page shows event details, past recap and video, with subtle inline event link and Maven auto-registration note | Needs rendered check | Event series was requested from production for local reproduction. |

## Homework behavior and content

| Request | State | Evidence / next check |
| --- | --- | --- |
| Every Buildcamp homework and capstone uses a multi-step form with steps in course navigation | Partially implemented | Verify all 10 authored files, direct navigation, mobile layout, save/resume and submission. |
| Same submission semantics as old CMP and DTC website, spread across steps | Under Astra review | Compare field requirements, validation, save, deadline, closed/scored and statistics behavior. |
| Code URL is required; remove copy saying it is optional | Open | Canonical source correction assigned; enforce at submission boundary, not copy alone. |
| October 5, 2026 at 23:59 UTC homework deadline copy must agree with actual deadline and required code URL | Open | Verify authored date and database after sync; user identified this exact sentence as wrong about the URL. |
| Remove generic "AI Assistants / You can use AI..." homework prose | Open | Canonical source correction assigned. |
| Week 1/2 homework has two weeks; weeks 3–6 one week each; project attempt 1 starts week 7 and lasts two weeks; peer review one week; attempt 2 two weeks plus one-week review | Needs data check | Use prior CMP cohort schedule. Preserve Cohort 4 end November 22. |
| Week 2 capstone asks only for URL; may include up to three Learning in Public links | Needs form check | Other homework can have different configurable link counts. |
| Learning in Public is its own step, configurable count, hidden at zero | Partially implemented | Verify all homework. |
| Learning in Public content uses brief intro, useful links and collapsible examples across AISL and DTC | In progress | Course source edit assigned; reusable rendering belongs in community-base. |
| Validate X example length using X link weighting; literal `<LINK>` counts as a link | In progress | Apply to authored X examples; distinguish from learner-submitted URLs. |
| Use meaningful, nonduplicated step icons/labels, remove "Question 1" duplication and "Step N of M" beside due date | Open | Screenshot 20260924-152151 and 20260925-191013. |
| Step URL `/homework/intro` instead of `?homework_step=intro` | Open | AISL #1827; shared stepper currently generates query URLs. Preserve save, direct entry and old bookmarks. |
| Show learner submission state: no submission, draft, submitted, closed, scored | Open | Keep learner state distinct from homework availability. Inspect each rendered state. |
| Show homework statistics where available, especially scored homework | Open | Shared community-base statistics exist; define concise placement and gating. |
| Keep shared homework behavior in community-base and adopt it in AISL and DTC | Open | Includes step URLs, LIP examples/pattern, validation, status and stats; run both consumer suites after package changes. |
| Preserve save/resume/final submission semantics with meaningful tests | Existing coverage, parity review open | `content/tests/test_homework_step_reader.py`, `playwright_tests/test_homework_steps_1778.py`; expand only for uncovered parity gaps. |

## Follow-up across repositories

| Request | State | Evidence / next check |
| --- | --- | --- |
| Create community-base issue for reusable `/c/<uuid>` links in DTC; defer DTC implementation | Needs issue check | User asked to track, not implement now. |
| Bring cohort-only course model and shared homework design to DTC through community-base | Open | Do after AISL working deployment, with cross-consumer tests. |

## Review notes

- Home learner needs and visual reviews: `2026-09-24-buildcamp-home-jtbd.md` and
  `2026-09-24-buildcamp-home-design-review.md`.
- Homework navigation review: `2026-09-24-buildcamp-homework-controls-review.md`.
- This tracker must be updated when a change is committed, deployed, or rejected
  by rendered evidence. "Open" means no verified completion yet.
