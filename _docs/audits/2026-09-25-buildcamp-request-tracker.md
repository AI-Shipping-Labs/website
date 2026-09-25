# AI Buildcamp request tracker — 2026-09-25

This is the working checklist for requests raised during the September 24–25
course iteration. A checked item needs code and rendered or test evidence;
"implemented locally" does not mean deployed. Source content must not be pushed
until the site and shared parser that consume it are deployed, because the
course repository may sync into production after a push. The current source
has no webhook secret, so the September 25 update required an explicit sync.

## Release and data

| Request | State | Evidence / next check |
| --- | --- | --- |
| Run AISL on localhost 8030 from the correct repository and main checkout | Running | Restarted after v0.5.9 pin; `/courses/ai-buildcamp` returns 200 and protected canonical homework step paths return 403 anonymously, confirming route resolution. |
| Keep focused commits; push AISL; deploy dev; promote to prod | v0.5.8 site release complete; next shared-step release pending | Site `7ae7ab1` passed Deploy Dev `36172759037`; exact image `20260925-183544-7ae7ab1` reached production and promotion run `36176062139` succeeded. New shared-step adoption commit remains local until community-base v0.5.9 releases. |
| Register and sync Buildcamp content into dev, verify rendered course pages | Dev sync complete; authenticated UI check open | Source `1dec32f0-fd53-497f-8fd6-6f0c6ebae2ba` was initially disabled for missing webhook secret. A forced first sync created the course/cohort; a follow-up succeeded with zero errors. Dev and production both synced source commit `513d795` with zero errors. Dev course landing returned 200; unit pages correctly returned 403 anonymously. Configure the source secret for normal future syncs. |
| Preserve/recover lost work, reconcile worktrees/branches, bring needed changes to main | Needs audit | Prior branches and worktrees were reviewed in this session; verify no remaining valuable unmerged work before closure. |
| Do not move Cohort 4 end from November 22 | Decision | User explicitly confirmed. |

## Shared course structure and learner UI

| Request | State | Evidence / next check |
| --- | --- | --- |
| Use one course content shell for Buildcamp, Python, self-paced and workshops; keep common code DRY | Partially implemented | Compare rendered page widths, right margins, top offsets, and content template usage. |
| Make every course cohort-based; create self-paced cohort for Python and similar courses, eventually across repos | Open | AISL first, then community-base/DTC. |
| Show enrolled cohort, current cohort, and past cohorts 1–3 where available | Needs rendered check | Source past cohorts from CMP; do not label current cohort "next." |
| Explain/fix Course Home showing “No cohort is linked” to an account that can view Buildcamp | Code path explained; account-specific diagnosis open | Screenshot 20260925-194317. Buildcamp is entitlement-gated: a `CourseAccess` grant or staff status permits reading without `CohortEnrollment`. Home derives its cohort only from enrollment rows. Homework resolution can display a past cohort's content as a fallback; submitting it currently auto-creates that cohort enrollment. Confirm the affected account's access provenance and Maven enrollment status without putting identity in reports. Review whether fallback submission should be allowed. |
| Course home should show current module, current homework and useful next actions without repeating Project in multiple places | Needs rendered check | See home JTBD and design reviews in this directory. Course-specific information belongs on Home, not landing. |
| Home/course layout 3xl width, consistent margins, sensible spacing; avoid extra cards/chrome | Needs rendered check | Recheck attached screenshots and current 8030 rendering. |
| YAML syllabus section separation; homework/capstone as first-level rows | Implemented locally | AISL commits `94eefe8`, `aa8f81e`; latter requires shared `syllabus_section` release and fresh rendering. |
| Make Optional-content week separators portable source metadata | Deployed and verified | Screenshot 20260925-221729. Site commit `586a92d43` renders `module.syllabus_section` generically in syllabus, overview, and reader; three focused tests pass on a non-Buildcamp course. Source commit `f55fb73` sets `syllabus_section: Week N` on the first topic of each week. Site image `20260925-211304-aac6388` is live on dev and prod; both synced `f55fb73`, and rendered pages show Week 1–8. Localhost:8030 matches. |
| Canonical first-level overview/session/homework URLs; capstone under `/homework-capstone`; `/c/<uuid>` share redirect | Needs route check | Old nested aliases/redirects were specifically rejected except `/c/<uuid>`. |
| Subtle expansion animation; sidebar spacing and overflow fixes | Needs rendered check | Review screenshot-specific requests. |
| Session page shows event details, past recap and video, with subtle inline event link and Maven auto-registration note | Needs rendered check | Event series was requested from production for local reproduction. |

## Homework behavior and content

| Request | State | Evidence / next check |
| --- | --- | --- |
| Every Buildcamp homework and capstone uses a multi-step form with steps in course navigation | Partially implemented | Verify all 10 authored files, direct navigation, mobile layout, save/resume and submission. |
| Same submission semantics as old CMP and DTC website, spread across steps | Audit complete; implementation open | Astra's concrete gaps and implementation queue are below. |
| Code URL is required; remove copy saying it is optional | Implemented locally; deploy open | Source commit `99d3d3c` and AISL commit `1c648353b`; 27 Django and 5 focused Playwright tests passed. |
| October 5, 2026 at 23:59 UTC homework deadline copy must agree with actual deadline and required code URL | Source synced; authenticated render check open | Authored fixed date and optional URL language removed from source; production sync at `513d795` succeeded with zero errors. Verify displayed cohort deadline in a learner session. |
| Remove generic "AI Assistants / You can use AI..." homework prose | Source synced; authenticated render check open | Source `99d3d3c` included in production sync `513d795` with zero errors. |
| Week 1/2 homework has two weeks; weeks 3–6 one week each; project attempt 1 starts week 7 and lasts two weeks; peer review one week; attempt 2 two weeks plus one-week review | Needs data check | Use prior CMP cohort schedule. Preserve Cohort 4 end November 22. |
| Week 2 capstone asks only for URL; may include up to three Learning in Public links | Needs form check | Other homework can have different configurable link counts. |
| Learning in Public is its own step, configurable count, hidden at zero | Partially implemented | Verify all homework. |
| Learning in Public content uses brief intro, useful links and collapsible examples across AISL and DTC | AISL source and parser deployed; DTC adoption open | `99d3d3c` updates ten files; AISL sanitizer commit `52cff6396` preserves safe `<details>/<summary>`. Production source sync succeeded at `513d795`. |
| Validate X example length using X link weighting; literal `<LINK>` counts as a link | Source validator complete; shared rule open | Source validator in `99d3d3c`, longest example 247 weighted characters; distinguish authored examples from learner URLs. |
| Use meaningful, nonduplicated step icons/labels, remove "Question 1" duplication and "Step N of M" beside due date | Partially implemented locally | AISL form commit removed step count; shared PR #299 adds semantic step labels and AISL adoption is committed locally. Icon review and deployed check remain. |
| Give the answer form more space below the question text | Implemented locally; rendered check open | Screenshot 20260925-193200; added `mt-6` before the "Your answer" label. |
| Fix capstone starter instructions: literal `bash` and option list numbering | Source synced; authenticated render check open | Screenshots 20260925-193248 and 193318; source commit `bb3743b` uses headings and valid indented code blocks. Production sync at `513d795` succeeded. |
| Make capstone starter answer a normal one-line text field; collect repo URL once on final review | Source and app deployed; authenticated render check open | Screenshot 20260925-193331; source commit `513d795` changes q2 to `free_form` and removes duplicate URL prompt. AISL short-text input implementation shipped in image `20260925-183544-7ae7ab1`. |
| Simplify Learning in Public link controls and remove repetitive optional copy | Implemented locally; rendered check open | `1c648353b`, screenshot 20260925-193506; configurable max and optional links preserved. |
| Keep long code blocks within the homework reader width | Deployed; authenticated render check open | Screenshot 20260925-221346; AISL commit `09ab52cfd`. Chromium layout probe at 1280/390px in dark/light confirms the 7.7kpx code line scrolls inside `<pre>` without widening the document. Site image `20260925-211304-aac6388` is live on dev and prod. |
| Fix Learning in Public step layout and review the self-link | Deployed; authenticated render check open | Screenshot 20260925-221420; AISL overflow commit `09ab52cfd`, source commit `f55fb73` removes the redundant course self-link. Site image and course source are live on dev and prod. |
| Shorten homework and capstone titles in navigation | Deployed and verified | Screenshot 20260925-221630. Source commit `f55fb73` removes redundant module/week prefixes from all 10 canonical homework/capstone titles. Localhost:8030 and production show “Homework: Document Processing with AI” and “Capstone: Your AI Project.” |
| Show readable answers on Review & submit | Open | Screenshot 20260925-221445 shows opaque `option-…` hashes for Questions 1–3, likely after saved choices no longer match current option labels. Show a clear changed-option prompt with a route back to that question; never expose internal option keys. Recheck active choices and stored submissions. |
| Explain saved draft with a subtle help affordance | AISL deployed; shared package PR pending | Screenshot 20260925-221522. AISL commit `09ab52cfd` adds a keyboard/hover accessible disclosure, verified in Chromium and shipped in image `20260925-211304-aac6388`. Community-base PR #300 has a reusable fragment; both linked consumer suites passed on its code commit, and PR release checks are running. Adopt after v0.5.10 release. |
| Step URL `/homework/intro` instead of `?homework_step=intro` | Deployed to dev; prod pending visual fixes | AISL adoption commit uses canonical step paths, direct entry and old query bookmarks. Shared package PR #299 released v0.5.9; first dev run hit a test-only status-200 ratchet, fixed by `09aba04a`. Deploy Dev `36182696480` passed all shards/coverage and `/ping` switched to `20260925-201055-09aba04`. |
| Show learner submission state: no submission, draft, submitted, closed, scored | Open | Keep learner state distinct from homework availability. Inspect each rendered state. |
| Show homework statistics where available, especially scored homework | Open | Shared community-base statistics exist; define concise placement and gating. |
| Keep shared homework behavior in community-base and adopt it in AISL and DTC | Open | Includes step URLs, LIP examples/pattern, validation, status and stats; run both consumer suites after package changes. |
| Preserve save/resume/final submission semantics with meaningful tests | Existing coverage, parity review open | `content/tests/test_homework_step_reader.py`, `playwright_tests/test_homework_steps_1778.py`; expand only for uncovered parity gaps. |

## Follow-up across repositories

| Request | State | Evidence / next check |
| --- | --- | --- |
| Create community-base issue for reusable `/c/<uuid>` links in DTC; defer DTC implementation | Needs issue check | User asked to track, not implement now. |
| Bring cohort-only course model and shared homework design to DTC through community-base | Open | Do after AISL working deployment, with cross-consumer tests. |
| Track DTC Studio `community_base.studio.E001` under local settings | Issue filed; fix later | DataTalksClub/website#435 records the local-settings-only check failure. DTC test settings and package consumer CI passed without silencing it. |

## Review notes

- Home learner needs and visual reviews: `2026-09-24-buildcamp-home-jtbd.md` and
  `2026-09-24-buildcamp-home-design-review.md`.
- Homework navigation review: `2026-09-24-buildcamp-homework-controls-review.md`.
- This tracker must be updated when a change is committed, deployed, or rejected
  by rendered evidence. "Open" means no verified completion yet.

## Astra submission-flow audit (2026-09-25)

Read-only review of AISL, old CMP and DTC found these concrete gaps. This is
the implementation queue for the shared homework work:

1. **Required code URL:** All ten active Buildcamp homework files enable
   `homework_url_field`. AISL currently constructs an optional `FinalField` in
   `content/services/homework_step_reader.py` and the template omits its
   `required` flag. Require it on final submission at the server boundary,
   while allowing an incomplete draft to save.
2. **Accepted submission versus draft:** The shared stepper reports submitted
   when a submission exists but renders newer draft values in the review.
   Show pending edits separately from the accepted submission. Closed/scored
   views must present the accepted snapshot.
3. **Deadline semantics:** Old CMP/DTC accept submissions while the state is
   OPEN, even after the displayed due date. AISL currently auto-closes at the
   due date, a deliberate earlier divergence. Reconcile this with the latest
   parity request only after confirming an operator path to close/score; show
   an accurate late-but-open message. Do not change the separate project
   attempt/review timeline.
4. **Learning in Public:** AISL stores URLs but does not award one point per
   eligible link or block duplicate reuse across homework/project submissions
   in a cohort. Put reusable validation/scoring in community-base and adapt
   both sites.
5. **Results and statistics:** A scored AISL homework currently looks like a
   closed draft. Show the accepted score, correct answers when permitted, and
   statistics only when scored/available. Shared statistics helpers currently
   expect community-base coursework models; AISL needs an adapter.
6. **Configurable fields:** Old CMP/DTC include optional comments and FAQ
   contribution fields. AISL does not yet model or persist these; add only
   where enabled by source configuration.
7. **Review and confirmation:** Give Learning in Public its own label rather
   than “Question 7”; use semantic question titles and targeted errors. Show
   accepted submission time and restore the confirmation notification hook
   subject to preferences.

The audit found existing save/reload/stale-revision/browser tests in
`content/tests/test_homework_step_reader.py` and
`playwright_tests/test_homework_steps_1778.py`. Add focused tests for the new
gaps, especially required URL at submit, accepted versus pending draft,
late-open versus closed, duplicate LIP links/scoring, and scored-only results.

## Homework state presentation decision to implement

Use a small state label beside the homework title in the course navigation and
the same state near the due line on the homework page. Derive it from both the
assignment state and this learner's accepted submission: **Not submitted**,
**Draft**, **Submitted**, **Unsubmitted changes**, **Closed — not submitted**,
or **Scored**. A learner without a submission must not see a cohort-wide
"no submissions" claim. On closed/scored pages, show the accepted snapshot
and submission time; if there is a saved unsent draft, label it separately.
For a scored assignment, show the score first and a compact public-statistics
link or summary only when actual statistics exist. Keep the distribution
detail in the existing statistics surface. Share the state calculation and
presentation contract through community-base, with AISL and DTC supplying
their own model rows and URLs.
