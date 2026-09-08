# Website code audit: detailed finding catalog

Date: 2026-09-07. Baseline commit: `1343497b0003093d4a1430e9784118210e36acfa`.
Review or retire by 2027-03-07.

Read the [implementation plan](2026-09-07-website-complexity-reduction-plan.md) for
the measured baseline, shared ownership model, work packets, dependencies, existing
issue links and acceptance/rollout gates. The [baseline JSON](2026-09-07-website-code-audit-metrics.json)
preserves the repository measurements and earlier issue-state snapshot.

This catalog includes bugs, concurrency/recovery risks and maintenance findings.
It is not a count of proven production incidents. Each finding labels its confidence,
prerequisites and the checks still needed. No application changes or production operations
were performed for this audit. See each review's coverage and limits below.

Priorities are recommended work order: `P0` is an immediate functional blocker or serious trust-boundary failure,
`P1` is a high-impact boundary/correctness issue, `P2` is planned correctness or
simplification, and `P3` is a measurement or lifecycle-dependent candidate.
An issue's existing priority remains authoritative until its owner changes it.

Findings that share a cause remain separate when their failure/recovery contracts differ;
the implementation plan groups them under one work packet. In particular, generic email,
reminders, announcements and plan deliveries share the delivery work, and event concurrency
shares the event-command refactor. Do not add overlapping effort or LOC estimates together.

The plan's recovery and compatibility gates take precedence over local sequencing
suggestions: no blind resend of ambiguous historical delivery, no premature R1/R2 switch,
and no weakening immutable test-policy ceilings. Target line reductions are hypotheses
pending a deletion inventory, not proven removable code.

## Finding index

Total: 57 findings across four review areas.

| ID | Finding |
|---|---|
| [WEB-01](#web-01) | Community project markdown is stored as executable HTML and shown to staff before approval |
| [WEB-02](#web-02) | The global notification bell turns comment text into executable DOM |
| [WEB-03](#web-03) | Public markdown uses two security contracts across closely related models |
| [WEB-04](#web-04) | Course-unit and workshop-page comment APIs bypass their parent access policy |
| [WEB-05](#web-05) | Book Club note-thread access ignores authentication and the author's privacy choice |
| [WEB-06](#web-06) | Cancelled books still accept read and note mutations |
| [WEB-07](#web-07) | Vote toggles are not serialized and the UI allows overlapping mutations |
| [WEB-08](#web-08) | Analytics queue failure can turn an optional visit write into a page 500 |
| [WEB-09](#web-09) | Tag filtering and tag pages repeatedly scan whole catalogs in Python |
| [WEB-10](#web-10) | Related-content rails load every published object across up to six models |
| [WEB-11](#web-11) | Poll listing issues two count queries per displayed poll |
| [WEB-12](#web-12) | Comment listing fetches replies with one query per top-level comment |
| [WEB-13](#web-13) | Frontend mutation helpers retry deterministic and ambiguous failures indiscriminately |
| [WEB-14](#web-14) | Studio ships a second, normally unreachable checkpoint editor beside the shared board |
| [WEB-15](#web-15) | The Django admin timestamp editor silently turns malformed times into wrong chapter positions |
| [WEB-16](#web-16) | Embedded event claims send anonymous visitors to the home page after sign-in |
| [IAM-01](#iam-01) | Generic email deduplication claims after the external send |
| [IAM-02](#iam-02) | Queued community actions can apply obsolete payment state |
| [IAM-03](#iam-03) | Stripe billing-email sync bypasses verified identity change |
| [IAM-04](#iam-04) | GET requests mutate unsubscribe preferences |
| [IAM-05](#iam-05) | Signup and newsletter user acquisition still race on unique email |
| [IAM-06](#iam-06) | LLM progress apply can overwrite and later undo a human completion |
| [IAM-07](#iam-07) | Stripe event idempotency is a terminal marker, not an execution claim |
| [IAM-08](#iam-08) | Stripe attempt numbers use unlocked `MAX + 1` |
| [IAM-09](#iam-09) | Account merge mutates users and relations without locking either account |
| [IAM-10](#iam-10) | Reminder markers suppress failed channels permanently |
| [IAM-11](#iam-11) | Nullable user defeats the per-event Slack uniqueness guarantee |
| [IAM-12](#iam-12) | General announcement fan-out has no durable business-event identity |
| [IAM-13](#iam-13) | The recording upload worker does not validate ownership of the current lease |
| [IAM-14](#iam-14) | Seven Slack POST implementations bypass the existing retrying client |
| [IAM-15](#iam-15) | Dormant Calendly capacity state is still actively maintained |
| [OPS-01](#ops-01) | Maven step claims fail on PostgreSQL |
| [OPS-02](#ops-02) | GitHub follow-up sync can be lost during lock release |
| [OPS-03](#ops-03) | Maven active-occurrence uniqueness is absent during R1 |
| [OPS-04](#ops-04) | API and Studio event edits use stale transition snapshots |
| [OPS-05](#ops-05) | Concurrent Zoom provisioning can orphan provider meetings |
| [OPS-06](#ops-06) | Plan-ready and partner-intro `sending` claims never expire |
| [OPS-07](#ops-07) | Sprint delivery claims are initially written as terminal states |
| [OPS-08](#ops-08) | Plan and week uniqueness races escape as 500 responses |
| [OPS-09](#ops-09) | Anonymous event registration can race while creating the user |
| [OPS-10](#ops-10) | Two plan write stacks implement the same aggregate |
| [OPS-11](#ops-11) | API and Studio separately validate and orchestrate event transitions |
| [OPS-12](#ops-12) | Zoom accepts unmatched recording webhooks without terminal state |
| [OPS-13](#ops-13) | GitHub sync retains duplicate helper authorities |
| [OPS-14](#ops-14) | Worker task formatting has two active authorities |
| [OPS-15](#ops-15) | Integration-setting writers have inconsistent atomicity and validation |
| [SYS-01](#sys-01) | Staff CLI sends its credential to an absolute foreign URL |
| [SYS-02](#sys-02) | CLI path segments are interpolated without URL encoding |
| [SYS-03](#sys-03) | Docker copies scratch data and development machinery into its build context |
| [SYS-04](#sys-04) | Runtime imports rely on undeclared transitive dependencies |
| [SYS-05](#sys-05) | Starting local development can silently author migrations |
| [SYS-06](#sys-06) | Test debt has accumulated its own substantial maintenance system |
| [SYS-07](#sys-07) | The global test runner keeps an expired payment compatibility window open |
| [SYS-08](#sys-08) | Per-test CI sharding may repeat expensive class fixtures on every shard |
| [SYS-09](#sys-09) | Several delivery and safety systems need explicit retirement boundaries |
| [SYS-10](#sys-10) | Measure whether advisory complexity reporting needs stronger enforcement |
| [SYS-11](#sys-11) | CLI table output collapses collection envelopes into one truncated cell |

## Content, browser interfaces, and smaller product domains

### Scope and method

This report covers `content/`, public and Studio `templates/`, first-party code in
`static/`, `assets/`, `bookclub/`, `analytics/`, `questionnaires/`, `comments/`,
`voting/`, and the corresponding browser behavior in `playwright_tests/`.

The audit followed `AGENTS.md`, `_docs/PROCESS.md`, and
`_docs/testing-guidelines.md`. It was read-only apart from this report. No live
production data, remote content repositories, external services, or GitHub writes
were used. No full test suite was run.

Priority means:

| Priority | Meaning |
|---|---|
| P0 | Exploitable trust-boundary failure; contain before routine work. |
| P1 | Material authorization, integrity, availability, or scaling defect. |
| P2 | Significant performance or maintenance cost with a practical reduction path. |

### Recommended sequence

| Order | Findings | Purpose |
|---|---|---|
| 1 | `WEB-01`, `WEB-02` | Close the two member-to-privileged-browser stored-XSS paths. |
| 2 | `WEB-04`, `WEB-05`, `WEB-06`, `WEB-07`, `WEB-08` | Repair access, lifecycle, concurrency, and request-availability invariants. |
| 3 | `WEB-09`, `WEB-10`, `WEB-11`, `WEB-12` | Bound work by result size rather than total catalog or thread size. |
| 4 | `WEB-03`, `WEB-13`, `WEB-14` | Consolidate rendering and browser mutation infrastructure after behavior is protected. |

### Findings

<a id="web-01"></a>

### WEB-01 — Community project markdown is stored as executable HTML and shown to staff before approval

| Field | Assessment |
|---|---|
| Priority | P0 |
| Confidence | High |
| Classification | Confirmed defect; executable HTML path established by code and an offline renderer check, without browser execution. |
| Effort | Small to medium, estimated 1–2 engineer days including backfill and browser coverage. |
| Estimated reduction | Estimate: one member-to-staff/public script-execution path removed; little net LOC reduction. |

Evidence:

- `content/views/api.py:88-164` (`submit_project`) accepts `content_markdown` from
  any authenticated member, creates a pending project, and calls `save()` without
  a sanitizer or `full_clean()`.
- `content/models/project.py:121-132` (`Project.save`) calls `render_markdown`,
  video replacement, and `linkify_urls`, but never `sanitize_html`.
- `studio/views/projects.py:35-55` gates the review view with
  `@staff_required`, then `templates/studio/projects/review.html:72-76` renders
  `project.content_html|safe`. Review is therefore the first display step, before
  an approval decision can protect staff.
- `content/models/project.py:156-161` (`Project.approve`) publishes the same
  stored HTML; `templates/content/project_detail.html:99-108` then renders it
  with `|safe` for an authorized public-page viewer.
- A focused offline call to the current renderer preserved
  `<img onerror=...><script>...</script>` unchanged. Passing the result through
  `content.utils.markdown.sanitize_html` removed both the event handler and
  script, proving that the existing safe primitive covers this payload.

Trigger and impact:

An authenticated attacker submits a project whose markdown contains raw active
HTML. A staff member merely opens its normal review page. The browser receives
the payload in a `|safe` block under the staff session; approval is not required.
If approved, authorized project readers receive the same payload. The likely
impact is action execution with the victim's same-origin privileges, including
staff actions when the victim is a reviewer. CSRF does not mitigate script that
runs on the application origin.

Remediation:

1. Introduce one canonical `render_safe_markdown` pipeline that applies markdown,
   supported transforms, linkification, and sanitation in a documented order.
   The current `sanitize_html` is not a blind drop-in after every transform: its
   allowlist has no `iframe`, while `replace_video_urls_in_html` deliberately
   emits constrained YouTube/Loom iframes at
   `content/templatetags/video_utils.py:315-363`. Preserve generated video embeds,
   event-widget hydration attributes, and Mermaid wrappers through trusted
   constructors or narrowly validated allowlist rules; continue stripping
   author-supplied iframe/script/event-handler markup.
2. Use it in `Project.save()` for every write, including `update_fields` paths.
3. Validate submitted URL fields and payload limits at the endpoint or form
   boundary; model field validators are not invoked by plain `save()`.
4. Re-render all existing project `content_html` from `content_markdown` in a
   resumable data migration or management command before trusting stored HTML.
   During that transition, render existing project source through the safe pipeline
   on read or show escaped text; sanitizer-on-write alone does not protect old rows.
5. Keep the template `|safe` only for output produced by the canonical sanitizer.

Regression checks:

| Layer | Check |
|---|---|
| Django | Submit raw `<script>`, event-handler, SVG, `javascript:` link, and malformed-tag payloads; assert stored `content_html` has no active markup and normal headings, code, links, images, and Mermaid survive. |
| Django | Exercise create, resave, `update_fields`, approve, and reject paths; assert pending content remains unpublished and derived HTML stays fresh. |
| Playwright | As a member submit a sentinel payload, then as staff open the review page and approve it. Assert no sentinel global, dialog, or attacker-controlled request occurs and the text/allowed formatting is visible on review and public pages. JavaScript behavior belongs in Playwright, not HTML string tests. |

Rollout, backfill, rollback:

Deploy sanitizer-on-write and safe handling of existing rows first, then run a dry-run backfill that reports changed rows,
then apply it in bounded batches and sample legitimate embeds/Mermaid. Keep the
original markdown as the rollback source. A rollback must keep member submissions
fail-closed: serve escaped/plaintext markdown or suppress the affected rich body
until a corrected renderer is available. Do not restore the unsafe renderer or
unsafe derived HTML from a backup.

<a id="web-02"></a>

### WEB-02 — The global notification bell turns comment text into executable DOM

| Field | Assessment |
|---|---|
| Priority | P0 |
| Confidence | High |
| Classification | Confirmed defect; attacker-controlled data reaches an `innerHTML` sink, without an audit-time browser execution. |
| Effort | Small, estimated 1 engineer day with Playwright coverage. |
| Estimated reduction | Estimate: three unsafe string interpolation sites and one inline-handler interpolation removed. |

Evidence:

- `comments/views/api.py:147-176` stores an authenticated member's comment body
  verbatim.
- `comments/services.py:11-27` immediately invokes content-comment notification
  creation after the comment write.
- `notifications/services/notification_service.py:607-625` puts the verbatim
  comment excerpt in `Notification.body`.
- `notifications/views/api.py:18-20` correctly requires login, but
  `notifications/views/api.py:59-70` returns raw `title`, truncated `body`, and
  `url` values as JSON.
- `templates/includes/header.html:537-559` concatenates all three values into an
  HTML string. It interpolates `url` twice, including inside an inline `onclick`,
  and assigns the result to `listEl.innerHTML`.
- The full notification page autoescapes the same fields at
  `templates/notifications/notification_list.html:49-62`; the defect is specific
  to the global bell renderer.

Trigger and impact:

A member who can comment on a victim-owned note, plan, course unit, or workshop
page posts a short active-HTML payload. The notification service sends the body
to the owner/instructor. When the recipient opens the bell on any page, the
dropdown inserts it as HTML. A linked instructor account may be staff, so the
victim can be privileged. The 80-character API truncation does not prevent a
compact event-handler payload.

Remediation:

1. Replace string assembly with `document.createElement`, `textContent`, safe
   attributes, and `addEventListener`.
2. Treat notification URLs as data: accept only an application-relative URL or
   an explicit same-origin allowlist before assigning `href`.
3. Remove the interpolated inline `onclick`; close over the numeric notification
   ID and validated URL in the event listener.
4. Extract the bell code from the 635-line header partial into a first-party JS
   module so it can be tested and reused by the full notification page if useful.

Regression checks:

| Layer | Check |
|---|---|
| Django | Confirm the API preserves arbitrary text as JSON data and rejects or normalizes unsafe notification URLs; do not assert browser safety from an HTML string. |
| Playwright | Post comment bodies containing tag, quote, entity, SVG, and event-handler payloads; open the recipient's bell and assert literal text, safe navigation, no sentinel/global mutation, no dialog, and no attacker request. |
| Playwright | Cover notification titles and URLs with quotes and markup, plus keyboard activation and mark-read behavior after the DOM construction change. |

Rollout, backfill, rollback:

The DOM fix needs no data backfill because stored bodies are valid plain text.
Deploy it independently and retain the server response shape. Rollback is the JS
asset revert; if rollback is required, temporarily suppress notification bodies
from the bell rather than restoring the unsafe sink.

<a id="web-03"></a>

### WEB-03 — Public markdown uses two security contracts across closely related models

| Field | Assessment |
|---|---|
| Priority | P1 |
| Confidence | High |
| Classification | Suspected runtime risk and maintenance observation; direct member exploitation is confirmed separately in `WEB-01`. |
| Effort | Medium to large, estimated 3–6 engineer days because legitimate HTML compatibility needs inventory and backfill. |
| Estimated reduction | Estimate: replace 7–10 bespoke render sequences with one pipeline and remove roughly 40–100 duplicated transform lines over time. |

Evidence:

- `content/utils/markdown.py:105-114` defines an `nh3` allowlist that removes
  scripts and event handlers. `content/utils/markdown.py:254-265` exposes
  `render_description_html`, which renders, linkifies, and sanitizes.
- Member-authored Book Club notes correctly use that service in
  `bookclub/models.py:440-452`, and the templates safely trust only the derived
  field at `templates/bookclub/_note_card.html:52` and
  `templates/bookclub/book_chapter.html:90`.
- Articles use an unsanitized sequence at `content/models/article.py:146-161`.
  Courses, modules, and units repeat it at `content/models/course.py:149-165`,
  `content/models/course.py:299-318`, and `content/models/course.py:379-400`.
  Workshops and workshop pages repeat it at
  `content/models/workshop.py:375-387` and `content/models/workshop.py:595-625`.
  Marketing pages repeat it at `content/models/marketing_page.py:220-233`.
- These derived fields are deliberately rendered with `|safe`, for example at
  `templates/content/blog_detail.html:82`,
  `templates/content/reader/_course_unit_content.html:25-39`,
  `templates/content/reader/_workshop_page_content.html:71-74`, and
  `templates/content/marketing_page.html:42`.
- `content/views/interview.py:89-98,117-128` creates another unsanitized renderer
  at request time.

Trigger and impact:

Most affected content comes from private sync repositories or Studio, so an
attacker needs content-author privileges, a compromised content source, or a
sync-boundary failure. Raw active HTML then reaches public pages. Even where the
trust decision is intentional, identical-looking markdown fields have different
contracts, so a future member-authored field can silently choose the unsafe path,
as happened for projects.

Remediation:

1. Name and document separate primitives for `trusted_raw_html` and sanitized
   markdown. Make sanitized markdown the default.
2. Route each model through the shared safe renderer, using existing extension flags
   and narrowly named variants for title stripping or video/shortcode transforms.
   Do not add a renderer registry unless the existing functions cannot express the contract.
   Generated media should be represented as validated structured output or
   trusted placeholders, rather than permitting arbitrary author-supplied
   `iframe` attributes.
3. Inventory the current source corpus for tags/attributes that the allowlist
   would remove. Add narrowly scoped transforms or allowlist entries only for
   real content needs.
4. Backfill all derived HTML from source fields; never sanitize already transformed
   HTML when the source markdown exists.
5. Add a repository check that rejects new `render_markdown` + `|safe` pipelines
   unless they use an approved safe/trusted primitive.

Regression checks:

| Layer | Check |
|---|---|
| Django | Table-driven tests for every rendered field with a shared malicious corpus and a shared legitimate corpus, including Mermaid, code highlighting, tables, event widgets, images, and shortcodes. |
| Content sync integration | Sync a fixture for each content type and assert derived HTML and public status. Ensure `update_fields` keeps all derived columns fresh. |
| Playwright | Render representative public content and assert supported widgets and diagrams still work while active payloads remain inert. |

Rollout, backfill, rollback:

Add a comparison mode that logs or exports sanitized-output diffs without serving
them. Fix corpus incompatibilities, enable by content type, then backfill in
batches. Keep source markdown and a count/hash of changed rows. Roll back per
content type while preserving sanitizer-on-write for member-authored projects.

<a id="web-04"></a>

### WEB-04 — Course-unit and workshop-page comment APIs bypass their parent access policy

| Field | Assessment |
|---|---|
| Priority | P1 |
| Confidence | High |
| Classification | Confirmed authorization and data-integrity defect. |
| Effort | Medium, estimated 2–4 engineer days including the access matrix and legacy-thread handling. |
| Estimated reduction | Estimate: one owner-aware policy registry replaces the separate two-domain resolver; net LOC may be neutral while four endpoint decisions converge. |

Evidence:

- `comments/views/api.py:35-73` resolves only plans and Book Club notes. Its
  docstring explicitly says unit and workshop UUIDs fall through to public read
  and authenticated write.
- All four operations use this resolver:
  `comments/views/api.py:85-99` (list), `147-161` (create), `189-210` (reply), and
  `236-258` (vote). A UUID that resolves to no owner also falls through, so any
  authenticated user can create orphan comment rows.
- The owning pages have stricter policies. Course unit pages call
  `decide_course_unit_access` at `content/views/courses.py:462-488`, including
  preview, registered, tier, individual access, and drip decisions. Workshop
  pages call per-page access at `content/views/workshops.py:948-981`.
- The templates hide Q&A behind those decisions at
  `templates/content/reader/_course_unit_content.html:45-46` and
  `templates/content/reader/_workshop_page_content.html:46-81`, but a direct API
  request does not pass through the page view.
- The lifecycle registry already registers `Unit` and `WorkshopPage` alongside
  other owners (`comments/threads.py:24-105`; asserted at
  `comments/tests/test_threads.py:52-71`). Access resolution is the duplicate
  mechanism that drifted.

Trigger and impact:

A guest or below-tier member who obtains a stable content UUID can read its Q&A
directly. Any authenticated member can post, reply, or vote even when the parent
body and on-page Q&A are gated. An arbitrary UUID creates an ownerless thread and
may activate the posting account. UUID entropy lowers casual discovery but is not
an authorization boundary; identifiers can persist in browser history, old
markup, notifications, exports, or source material.

Remediation:

1. Extend `ThreadOwner` registration with a resolver plus read/write policy, or
   add a companion policy registry keyed by the same model registration.
2. Resolve unit and workshop owners with their published parents and apply their
   effective access rules. Decide explicitly how course drip lock affects Q&A.
3. Return 404 for unknown owner UUIDs on reads and writes; preserve 401 for an
   unauthenticated write before disclosing existence.
4. Audit existing orphan threads with the existing cleanup command before
   deletion. Define how sync delete/recreate should retain legitimate unit/page
   threads, which are currently intentionally non-cascading.

Regression checks:

| Layer | Check |
|---|---|
| Django | One access matrix for unknown, draft/unpublished, preview/open, registered, each tier, individual course access, drip-locked, and per-page workshop overrides across list/create/reply/vote. Assert both status and absence of writes/activation/notifications. |
| Django | Registry completeness test requires every registered owner to declare an access policy and verifies query-bounded resolution. |
| Playwright | Navigate as below-tier users and also call the APIs directly with known UUIDs; assert no comments leak and no write succeeds. Confirm eligible readers retain the full Q&A flow. |

Rollout, backfill, rollback:

First report orphan counts and identify legitimate legacy IDs. Deploy owner
resolution in observe-only logging if ambiguity is high, then enforce. No content
backfill is required; orphan deletion is a separate, dry-run-first operation.
Rollback must preserve closed access for every known gated parent and retain
unknown-UUID write rejection. If a new owner-policy implementation must be
reverted, fall back to denying its comment API operations until the prior policy
can be restored; do not restore public-read/authenticated-write fallthrough.

<a id="web-05"></a>

### WEB-05 — Book Club note-thread access ignores authentication and the author's privacy choice

| Field | Assessment |
|---|---|
| Priority | P1 |
| Confidence | High |
| Classification | Confirmed authorization defect, including a focused predicate reproduction. |
| Effort | Small to medium, estimated 1–2 engineer days. |
| Estimated reduction | Estimate: one shared note-visibility predicate replaces divergent page/thread rules; roughly 10–25 duplicated policy lines avoided. |

Evidence:

- `bookclub/comments_permissions.py:11-18` states anonymous viewers can never read.
  The implementation at `bookclub/comments_permissions.py:59-76` only compares
  `get_user_level(viewer)` with the book level.
- Anonymous users have level zero, so a level-zero book passes. A focused call
  against the current predicate printed `anonymous_can_read_open_book_note=True`.
- The thread predicate never consults `ReaderProfile`. The canonical page helper
  at `bookclub/profiles.py:15-26` treats anonymous targets as private and hides an
  explicit private profile from other members.
- The chapter page applies that privacy helper at
  `bookclub/views.py:663-684`; its direct thread endpoint only applies the weaker
  predicate at `comments/views/api.py:97-99` and `159-161`.

Trigger and impact:

Anyone with the UUID can read a note thread on an open book anonymously. A
tier-eligible member can read or write the thread under another member's private
note even though the chapter feed and reader profile hide the note. The UUID is
high entropy, but it appears in Q&A markup while public and may survive in browser
state, logs, notification metadata, or shared links after the author switches to
private.

Remediation:

1. Require an authenticated viewer for every note-thread read and write.
2. Define one `viewer_can_read_note` decision used by chapter feed, profile, API,
   and notification recipient logic: owner and staff bypass; otherwise require
   public notes plus book access.
3. Define write policy explicitly. The natural rule is owner/staff, or another
   eligible member only while the note is public.
4. When visibility changes to private, stop future direct-reply notifications to
   disallowed users; existing notification links should fail closed through the
   corrected read predicate.

Regression checks:

| Layer | Check |
|---|---|
| Django | Cross-product of anonymous/member/staff/owner, open/paid book, public/private/no profile, and read/create/reply/vote. Assert 404 for hidden reads, 401 for anonymous writes, 403 for known-but-forbidden writes, and no side effects. |
| Playwright | Capture a formerly public thread UUID, switch the author private, then verify another member cannot see the note or fetch its comments while owner and staff still can. |

Rollout, backfill, rollback:

No data backfill is required. Deploy the shared predicate and monitor 403/404
rates by owner type without logging UUIDs or comment text. Rollback must not expose
formerly private note threads. Any relaxation requires an explicit product
decision that changes the privacy contract, plus member communication and a
migration plan; retain the explicit authenticated-read requirement promised by
the existing module contract.

<a id="web-06"></a>

### WEB-06 — Cancelled books still accept read and note mutations

| Field | Assessment |
|---|---|
| Priority | P1 |
| Confidence | High |
| Classification | Confirmed lifecycle/access defect. |
| Effort | Small, estimated less than 1 engineer day. |
| Estimated reduction | Estimate: reuse of `_resolve_book_or_404` removes roughly 12–20 duplicated resolver lines. |

Evidence:

- Normal reads reject cancelled books at `bookclub/views.py:300-308`
  (`book_detail`), `426-434` (`book_progress`), and `599-607`
  (`chapter_detail`).
- `bookclub/views.py:530-570` (`chapter_read`) rejects only unknown and hidden
  draft books before writing/deleting `ChapterRead`; it has no cancelled check.
- `bookclub/views.py:700-744` (`chapter_note`) has the same omission before
  creating/updating/deleting `Note` and activating the account.
- A correct reusable resolver already exists at
  `bookclub/views.py:749-763` and rejects both cancelled and non-staff draft
  books. Other mutation views use it at `bookclub/views.py:857-875`.

Trigger and impact:

An authenticated, tier-eligible member who knows the book slug and chapter number
can POST directly after a book is cancelled. The GET pages return 404, but read
marks, note bodies, cascade effects, and account activation can still change.
This makes cancellation an inconsistent presentation state rather than an
enforced lifecycle state.

Remediation:

Use `_resolve_book_or_404` in `chapter_read` and `chapter_note`, then route all
future book endpoints through the same resolver. Keep chapter lookup and tier
checks after lifecycle resolution so a cancelled resource reveals no extra detail.

Regression checks:

| Layer | Check |
|---|---|
| Django | Status matrix for current/upcoming/finished/cancelled/draft and anonymous/member/staff on both POST endpoints. For every denial assert no read/note row, deletion, activation, or notification side effect. |
| Playwright | Cancel a seeded book after a member has loaded it; submit the stale form and assert 404 plus unchanged data. This protects the realistic stale-tab flow. |

Rollout, backfill, rollback:

No backfill is necessary. Optionally report activity rows created after each
book's cancellation timestamp if historical cleanup matters. The code change is
safe to roll back, though doing so restores the inconsistent state.

<a id="web-07"></a>

### WEB-07 — Vote toggles are not serialized and the UI allows overlapping mutations

| Field | Assessment |
|---|---|
| Priority | P1 |
| Confidence | High in the race; medium in observed frequency. |
| Classification | Suspected runtime risk with a concrete time-of-check/time-of-use defect; no concurrent audit-time reproduction. |
| Effort | Medium, estimated 2–3 engineer days across transaction and browser behavior. |
| Estimated reduction | Estimate: eliminate two race outcomes and one class of 500; little LOC reduction. |

Evidence:

- `voting/views/api.py:54-82` performs existing-vote lookup, total-vote count,
  and insert/delete as separate operations without `transaction.atomic` or a
  lock.
- The uniqueness constraint at `voting/models/poll.py:131-153` protects only the
  same `(poll,user,option)` tuple. It cannot protect the total
  `max_votes_per_user` invariant.
- Two requests for different options can both observe a count below the maximum
  and both insert. Two requests for the same option can both miss the existing
  row; one then raises `IntegrityError`, which the view does not catch.
- `templates/voting/poll_detail.html:188-232` leaves every vote button enabled
  while `fetch` is pending and updates the local `votesRemaining` counter from
  whichever response arrives. Existing Playwright coverage at
  `playwright_tests/test_voting_polls.py:313-383` tests only a sequential fourth
  vote after three committed votes.

Trigger and impact:

A double-click, slow connection, multiple tabs, or concurrent API calls overlap
the check and write. The stored vote count can exceed the configured maximum, a
request can 500, or the browser can display a stale/negative remaining count.

Remediation:

1. Wrap the decision in `transaction.atomic` and serialize on a stable row shared
   by the user's votes for that poll. Locking the `Poll` row is simple and correct;
   a dedicated per-user-per-poll state row offers finer concurrency if needed.
2. Recompute the authoritative user count and affected option count inside the
   transaction and return both.
3. Make duplicate-insert handling deterministic; do not expose `IntegrityError`.
4. Disable relevant vote buttons while one mutation is pending, or queue one
   intent, then render only the server's authoritative state.

Regression checks:

| Layer | Check |
|---|---|
| Django transaction test | Use separate database connections and a barrier to race different options at `max-1`, and the same option from zero. Assert the max invariant, no 500, and a documented final toggle state. This needs PostgreSQL-aware coverage; SQLite cannot prove lock behavior. |
| Playwright | Delay the first response and double-click/toggle another option. Assert one in-flight mutation policy, enabled-state recovery, and counters matching a reload. |

Rollout, backfill, rollback:

Before deploy, report poll/user groups above their maximum. Decide whether to
retain earliest votes and remove excess in a deterministic one-off repair. The
transaction path can be rolled back independently from the UI lock, but keep the
UI lock if it improves feedback.

<a id="web-08"></a>

### WEB-08 — Analytics queue failure can turn an optional visit write into a page 500

| Field | Assessment |
|---|---|
| Priority | P1 |
| Confidence | High |
| Classification | Confirmed availability defect with a focused injected-failure reproduction. |
| Effort | Small, estimated less than 1 engineer day. |
| Estimated reduction | Estimate: remove the synchronous production fallback and one failure coupling; 2–8 LOC reduction. |

Evidence:

- `analytics/middleware.py:177-193` catches failures from the inline test-mode
  `record_visit` path.
- In production mode, `analytics/middleware.py:195-209` catches queue failure,
  imports `record_visit`, and calls it outside a second `try`. A database or task
  failure therefore escapes.
- The middleware invokes `_enqueue_visit` before calling the application view at
  `analytics/middleware.py:289-320`.
- A focused check patched both `jobs.tasks.async_task` and
  `analytics.tasks.record_visit` to raise. `_enqueue_visit` propagated
  `RuntimeError: db down`, confirming the path.
- Current middleware tests force `Q_CLUSTER['sync']=True` at
  `analytics/tests/test_middleware.py:23-40`, so they exercise the protected
  branch and cannot catch the production fallback defect.

Trigger and impact:

A consented GET/HEAD containing UTM parameters hits the production branch while
the task queue is unavailable. If the synchronous fallback also fails, the user
gets a 500 before the requested view runs. Queue and database trouble are exactly
when synchronous fallback is least safe, and the optional analytics write adds
request latency even when it succeeds.

Remediation:

Treat campaign visit tracking as best effort: log the enqueue failure with safe
metadata and drop the visit. If lossless analytics is a product requirement, use
a durable application outbox written with primary business transactions rather
than an ad hoc inline fallback in request middleware. Keep synchronous behavior
only in explicitly configured test mode.

Regression checks:

| Layer | Check |
|---|---|
| Django middleware | With `Q_CLUSTER.sync=False`, patch enqueue failure and assert the requested page still returns its normal response and the view runs exactly once. |
| Django middleware | Also patch fallback/database errors to prove no analytics exception escapes; assert a bounded, payload-free log record. |
| Django | Retain the current sync-mode attribution assertions separately. |

Rollout, backfill, rollback:

No backfill is possible for dropped visits. Add a counter for enqueue failures and
alert on sustained loss. Rollback is trivial, but restoring the inline fallback
would restore request coupling.

<a id="web-09"></a>

### WEB-09 — Tag filtering and tag pages repeatedly scan whole catalogs in Python

| Field | Assessment |
|---|---|
| Priority | P2 |
| Confidence | High |
| Classification | Confirmed performance and maintenance observation. |
| Effort | Medium to large, estimated 4–8 engineer days if normalized tags are introduced. |
| Estimated reduction | Estimate: replace `O(all published rows)` work per filtered request with indexed matching rows and remove roughly 40–70 lines of repeated scan/filter plumbing. |

Evidence:

- `content/views/pages.py:78-91` (`_filter_by_tags`) iterates an entire queryset
  into a Python ID list, then returns a second queryset filtered by those IDs.
- `content/views/pages.py:335-349` first materializes every blog article to build
  topics and creates another full queryset for legacy-tag filtering.
- Project and collection lists materialize their complete published querysets to
  build filter metadata at `content/views/pages.py:415-444` and
  `content/views/pages.py:485-513`, then evaluate the filtered queryset again.
- `content/views/tags.py:75-83` improved the index to tags-only projection, but
  still transfers every tag list. `content/views/tags.py:97-129` projects selected
  fields yet scans every row in five content types for every tag detail request.
- The storage contract is a JSON list (`content/utils/tags.py:1-27`), so counts,
  containment, and faceting have no shared indexed relation. A parallel filter
  implementation also exists in the events domain at
  `events/views/pages.py:97`.

Trigger and impact:

Any catalog or tag request pays work proportional to all published content, even
when the result has one item. Wide list views can perform a second query after
the scan. Latency, memory, and database-to-app transfer grow with the corpus; the
duplicated Python loops make publication and AND/OR semantics likely to drift.

Remediation:

1. Choose one indexed tag representation. A normalized tag plus content-assignment
   table supports counts and cross-type pages cleanly; a PostgreSQL JSON/array GIN
   strategy is smaller but needs an explicit SQLite/test fallback and still leaves
   cross-model aggregation.
2. Centralize catalog publication predicates and AND-tag semantics.
3. Query matching rows, counts, difficulties, and topics from projected/indexed
   data. Paginate result pages before materializing presentation objects.
4. Keep tag normalization on write and enforce uniqueness in the indexed form.

Regression checks:

| Layer | Check |
|---|---|
| Django | Cross-content fixtures assert normalization, multi-tag AND behavior, publication rules, unknown tags, counts, and deterministic date ordering. |
| Django performance | Compare small and 10x larger unrelated catalogs. Assert stable query count and inspect SQL to ensure the application does not fetch unrelated titles/descriptions/HTML. Query count alone is insufficient because the current scan already uses a constant number. |
| Playwright | Filter from tag chips on blog, projects, collections, and the tag detail page; verify URL state, combined filters, empty state, and back navigation. |

Rollout, backfill, rollback:

Backfill the tag index from normalized JSON in batches, dual-write during
verification, compare counts by type/tag, then switch reads. Retain JSON as the
rollback source until the new index has passed at least one content-sync cycle.

<a id="web-10"></a>

### WEB-10 — Related-content rails load every published object across up to six models

| Field | Assessment |
|---|---|
| Priority | P1 |
| Confidence | High |
| Classification | Confirmed scalability risk and maintenance observation. |
| Effort | Medium, estimated 3–5 engineer days; lower after `WEB-09` supplies an indexed tag relation. |
| Estimated reduction | Estimate: bound candidate materialization to tens of narrow rows instead of all published rows; memory/CPU reduction grows linearly with catalog size. |

Evidence:

- `content/services/related_content.py:128-175` defines published querysets for
  articles, tutorials, projects, workshops, courses, and events.
- `content/services/related_content.py:180-217` iterates every row, collects them,
  deduplicates event/workshop pairs, builds cards, normalizes tags, and sorts in
  Python before returning at most three cards.
- The querysets have no `.only()` projection, so models' large markdown/HTML,
  transcript, material, and other fields may be loaded solely to derive a short
  card.
- This work runs on article, project, tutorial, and workshop detail requests at
  `content/views/pages.py:378`, `465`, `618` and
  `content/views/workshops.py:636`.
- Existing tests in `content/tests/test_related_content.py` thoroughly cover
  ranking and pair deduplication, but no row-scaling/query-shape guard was found.

Trigger and impact:

Opening one eligible detail page loads the entire published catalog into the web
process. Queries remain roughly constant, so query-count monitoring misses the
growing row transfer, object allocation, tag normalization, and sort work.

Remediation:

1. Preserve the current ranking and event/workshop deduplication contract.
2. Fetch bounded candidates that overlap indexed tags, selecting only fields used
   by `_build_card`; fetch a bounded newest-per-type fallback only when needed.
3. Apply publication and durable-past-event predicates in SQL where practical.
4. Cache the final rail by `(content type, id, content version)` only after reads
   are bounded; caching the current full scan hides rather than fixes the cost.

Regression checks:

| Layer | Check |
|---|---|
| Django | Reuse the current ranking suite against the new candidate source, including current-item exclusion, ties, no tags, durable past events, and event/workshop pairs. |
| Django performance | Add many unrelated items and assert the rows/columns fetched remain bounded. Inspect captured SQL or instrument candidate counts; do not rely only on total query count. |
| Playwright | Smoke the rail links, access badges, and fallback heading on each detail-page type. |

Rollout, backfill, rollback:

Run old and new selectors side by side in a management check to compare top-three
results and timing on local seeded data. Tag-index backfill follows `WEB-09` if
used. Switch behind a short-lived flag; rollback restores selection without data
loss.

<a id="web-11"></a>

### WEB-11 — Poll listing issues two count queries per displayed poll

| Field | Assessment |
|---|---|
| Priority | P2 |
| Confidence | High |
| Classification | Confirmed query-performance defect. |
| Effort | Small, estimated less than 1 engineer day. |
| Estimated reduction | Estimate: remove exactly `2N` per-poll count queries, replacing them with annotations on the list query. |

Evidence:

- `voting/views/pages.py:8-28` loops through every open poll and reads
  `poll.options_count` and `poll.total_votes` for each accessible row.
- Those properties each issue a count query at `voting/models/poll.py:84-92`.
- Auto-close and tier filtering also occur in Python at
  `voting/views/pages.py:17-23`, so inaccessible/expired rows are materialized
  before being discarded.

Trigger and impact:

For `N` displayed polls the list performs the base query plus `2N` counts. Open
polls that the viewer cannot access still consume application work. This is small
today but has a direct, avoidable scaling slope.

Remediation:

Filter `required_level__lte` and `(closes_at IS NULL OR closes_at > now)` in the
query, then annotate distinct option and vote counts. Expose annotated names on
the presentation rows instead of calling query-backed model properties.

Regression checks:

| Layer | Check |
|---|---|
| Django | Assert status/tier/auto-close filtering and exact annotated counts with polls having multiple options and votes. |
| Django performance | Render one poll and many polls under `CaptureQueriesContext`; assert the count is constant and SQL has no per-poll count statements. |

Rollout, backfill, rollback:

No backfill or special rollout is needed. Compare rendered counts before deploy.
The queryset change is directly reversible.

<a id="web-12"></a>

### WEB-12 — Comment listing fetches replies with one query per top-level comment

| Field | Assessment |
|---|---|
| Priority | P2 |
| Confidence | High |
| Classification | Confirmed query-performance defect. |
| Effort | Small, estimated less than 1 engineer day. |
| Estimated reduction | Estimate: replace `N` reply queries with one prefetch query, saving `N-1` queries for a non-empty thread. |

Evidence:

- `comments/views/api.py:101-107` loads top-level comments with users and vote
  counts.
- Inside the loop, `comments/views/api.py:118-142` evaluates
  `comment.replies.select_related('user')` separately for every top-level row.
- Authenticated requests add one sensible query for voted IDs at
  `comments/views/api.py:109-116`; that is already constant.
- `comments/tests/test_api.py:15-108` verifies values and ordering but has no
  small-versus-large query guard.

Trigger and impact:

Every fetch of a busy Q&A thread grows by one database round trip per top-level
comment. The global Book Club chapter can mount several threads, multiplying the
cost in the browser.

Remediation:

Prefetch ordered replies with their users once, preferably to a named attribute,
and serialize that attribute. Keep top-level vote annotation and voted-ID lookup
as separate constant queries. Consider a response limit/pagination contract for
large threads after eliminating the N+1.

Regression checks:

| Layer | Check |
|---|---|
| Django | Preserve top-level and reply ordering and serialized fields for zero, one, and many replies. |
| Django performance | Compare one and many top-level comments and assert constant query growth, including authenticated `user_voted`. |
| Playwright | A thread with multiple questions/replies renders and all reply/vote handlers still target the correct card. |

Rollout, backfill, rollback:

No backfill is required. Deploy as a query-only change and compare API payloads.
Rollback is direct.

<a id="web-13"></a>

### WEB-13 — Frontend mutation helpers retry deterministic and ambiguous failures indiscriminately

| Field | Assessment |
|---|---|
| Priority | P1 |
| Confidence | High in code behavior; medium in production incidence. |
| Classification | Suspected runtime integrity risk; no lost-response browser reproduction was run. |
| Effort | Medium, estimated 2–4 engineer days. |
| Estimated reduction | Estimate: consolidate three retry implementations into one policy and remove roughly 80–160 lines, depending on module boundaries. |

Evidence:

- `static/js/studio/plan_editor_api.js:40-105` converts every HTTP/network failure
  to `ok:false`, then `writeWithRetry` retries all methods and all statuses after
  one second.
- `static/js/plans/checkpoint_task_board.js:141-211` independently implements the
  same unconditional policy. It retries DELETE at
  `static/js/plans/checkpoint_task_board.js:645-667` and POST move operations at
  `static/js/plans/checkpoint_task_board.js:804-821`.
- `static/js/plans/member_plan.js:38-69` has a third client; `apiPatch` throws for
  every non-2xx response and `apiPatchWithRetry` retries it without classifying
  the failure.
- Studio loads both API clients on the same page at
  `templates/studio/plans/_editor_body.html:344-347`, so they maintain separate
  in-flight/save state and messages.

Trigger and impact:

A 400/403/404 is repeated after a one-second delay, adding latency without a
chance of success. More seriously, a DELETE or POST can commit on the server and
lose its response; the retry then returns 404 or a second state transition, and
the client rollback restores stale DOM even though the first mutation succeeded.
Overlapping PATCH responses can likewise let an older result overwrite newer
feedback.

Remediation:

1. Create one small same-origin JSON client with a documented error taxonomy and
   shared in-flight accounting.
2. Never retry deterministic 4xx responses. Retry only operations that are
   idempotent by contract and only on selected network/5xx failures.
3. Add idempotency keys or version preconditions for retriable POST/DELETE
   operations. On an ambiguous outcome, refetch authoritative plan state instead
   of replaying a local rollback blindly.
4. Abort or ignore superseded field saves and derive the save indicator from the
   shared in-flight set.

Regression checks:

| Layer | Check |
|---|---|
| Playwright | Monkeypatch `fetch` so the original request commits but its first promise rejects. Verify delete/move reconciles from server state and a reload matches the DOM. |
| Playwright | Return 400/403 and assert exactly one request, prompt error feedback, and correct rollback. Return one 503 for an idempotent PATCH and assert the documented retry. |
| Playwright | Delay two edits and resolve responses out of order; assert the newest edit and save indicator win. JavaScript retry behavior should not be string-tested in Django HTML tests. |

Rollout, backfill, rollback:

First make the current operations observable by method/status/retry outcome
without logging bodies. Introduce the shared client one surface at a time, then
remove old helpers. No data backfill is needed. Keep server endpoints compatible
during migration so individual surfaces can roll back independently.

<a id="web-14"></a>

### WEB-14 — Studio ships a second, normally unreachable checkpoint editor beside the shared board

| Field | Assessment |
|---|---|
| Priority | P2 |
| Confidence | High for the current template; medium for exact deletable LOC until coverage pins all fallbacks. |
| Classification | Maintenance observation and practical deletion opportunity. |
| Effort | Medium, estimated 2–3 engineer days with browser regression coverage. |
| Estimated reduction | Estimate: approximately 350–500 lines from `plan_editor.js`, plus about 70 duplicate markdown-renderer lines from `member_plan.js` if the shared renderer is reused. |

Evidence:

- `templates/studio/plans/_editor_body.html:344-347` always loads
  `checkpoint_task_board.js` before `plan_editor.js`, both as deferred scripts.
- `static/js/studio/plan_editor.js:116-127` creates the shared board whenever the
  global exists. The shared module's `create` function returns an object and
  publishes the global at `static/js/plans/checkpoint_task_board.js:881-898`.
- Despite that invariant, Studio retains its older checkpoint drag path behind
  `if (!checkpointBoard)` at `static/js/studio/plan_editor.js:329-366`, binds its
  older checkpoint controls at `674-677`, and retains an older bulk-move path at
  `840-985`. Supporting snapshot, keyboard, edit, delete, and rendering helpers
  span much of `static/js/studio/plan_editor.js:195-327,413-672`.
- The fallback contains a dormant unsafe assignment at
  `static/js/studio/plan_editor.js:600-610`: if the API omits
  `description_html`, raw edited `value` is assigned to `innerHTML`.
- Markdown parsing is also duplicated almost line-for-line in
  `static/js/plans/checkpoint_task_board.js:21-102` and
  `static/js/plans/member_plan.js:173-251`.
- The three main plan scripts total 2,437 physical lines
  (`plan_editor.js` 1,052, `checkpoint_task_board.js` 898,
  `member_plan.js` 487), over half of the 4,305 first-party lines across 14 files
  in `static/js`. Including the separate 51-line `tailwind.config.js` gives 4,356
  first-party standalone JS lines. The much larger `static/vendor/mermaid` tree
  is a generated dependency, not first-party product logic.

Trigger and impact:

Today the fallback is parsed and shipped but normally not bound. It doubles the
mental model for checkpoint move/edit/delete behavior, keeps a dormant unsafe
sink, and makes fixes easy to apply to one implementation only. If asset loading
or template order changes, the older behavior silently becomes active rather
than failing clearly.

Remediation:

1. Make the shared board a required dependency for the Studio editor and fail
   visibly during development if it is missing.
2. Delete the Studio checkpoint fallback and its helpers after mapping references;
   leave resource, deliverable, next-step, summary, and note behavior in the
   Studio-specific module.
3. Export one safe markdown renderer from the shared board or a small presentation
   module and use it for member goals/deliverables/next steps.
4. Split `plan_editor.js` by remaining responsibility only if modules gain a clear
   owner and load contract; file splitting alone does not reduce complexity.

Regression checks:

| Layer | Check |
|---|---|
| Playwright | Run Studio and member checkpoint flows for add/cancel draft, edit/cancel, complete, delete, drag within/across weeks, keyboard move, bulk carry-forward, failure rollback, focus, and progress. |
| Playwright | Remove or block the shared board asset in a test and assert an explicit initialization failure rather than activation of a second implementation. |
| Playwright | Verify markdown links/code/emphasis render identically on member and Studio surfaces and malicious attribute payloads stay inert. |

Rollout, backfill, rollback:

Land behavior coverage first, delete one fallback slice at a time, and compare
bundled asset size. No data backfill is needed. Reverting an individual deletion
is safe while the shared board API remains stable.

### Coverage matrix

| Area | Reviewed | Result and limits |
|---|---|---|
| `content/` | Models, public views, access decisions, markdown utilities/extensions, catalog/tag views, related-content service, sync-facing derived fields, and focused tests. | Findings `WEB-01`, `WEB-03`, `WEB-04`, `WEB-09`, `WEB-10`. External content repositories and a real sync run were not inspected. |
| Public templates | Base/header/footer, content detail/list/reader templates, comments partials, voting pages, Book Club pages, `|safe`, inline handler, and dynamic-HTML call sites. | Findings `WEB-01`–`WEB-05`, `WEB-07`, `WEB-12`. No full caller proof was attempted for generic raw-HTML partial parameters. |
| Studio templates | Base/global search, project review, plan editor, sync/worker/import fragments, largest templates, inline JS and dynamic-HTML call sites. | Findings `WEB-01`, `WEB-02`, `WEB-13`, `WEB-14`. Studio Python business logic was outside this specialist scope except where needed to prove a template boundary. |
| First-party `static/js` | All first-party file inventory; deep review of plan board/editor/member scripts and Mermaid loader; global `innerHTML`/dynamic-code search. | Findings `WEB-13`, `WEB-14`, plus `WEB-15` and `WEB-16` from the completion pass below. All 14 first-party modules now have review coverage, including account/event/admin/Studio helpers. Browser execution remains limited as stated in each finding. |
| `static/vendor` | Inventory, size, load entry point, and provenance distinction. | The 46-file, approximately 4.9 MB full Mermaid asset tree contains 44 JavaScript files with 115,949 physical JS lines; all 46 files total 115,973 lines. It is a vendored generated dependency and is lazy-loaded by `static/js/mermaid-render.js:1-28,55-58`; those lines were not treated as first-party complexity or line-reviewed. |
| `assets/` and CSS | Tailwind source entry and generated/static layout inventory. | No substantive defect found. No visual regression, browser matrix, or unused-class purge analysis was run. |
| `bookclub/` | Lifecycle views, note rendering, profile visibility, thread permissions, reading aggregations, and focused Django/Playwright tests. | Findings `WEB-05`, `WEB-06`. No production book history was inspected. |
| `analytics/` | Campaign middleware/task boundary, consent-oriented tests, aggregations/activity inventory. | Finding `WEB-08`. No queue, database, GA, or production traffic was contacted. |
| `questionnaires/` | Models, public/Studio views, onboarding AI service/task structure, and query-scaling tests. | No standalone high-confidence finding. Provider streaming/fallback behavior was not exercised because external calls were excluded. |
| `comments/` | API CRUD/vote paths, lifecycle registry/cleanup, notification handoff, Q&A templates/script, and tests. | Findings `WEB-02`, `WEB-04`, `WEB-05`, `WEB-12`. No high-concurrency comment-vote test was run. |
| `voting/` | Model invariants, list/detail/API views, inline browser behavior, Django tests, and Playwright journey. | Findings `WEB-07`, `WEB-11`. Concurrency was established by code inspection, not reproduced against PostgreSQL. |
| `playwright_tests/` | Located authoritative browser owners for notifications, comments, Book Club privacy, voting, Studio plans, member plans, Mermaid, and Studio search; checked gaps against implementation. | Browser tests were read but not run. Recommended JS checks stay in Playwright per `_docs/testing-guidelines.md`. |

### Strengths and complexity to keep

- `content/utils/markdown.py:17-44,105-114` has a concrete `nh3` allowlist, and
  `bookclub/models.py:440-452` applies it correctly to member-authored notes while
  preserving derived-field freshness. The remediation should expand this design.
- `comments/threads.py:24-105,108-155` centralizes owner registration, cascade
  cleanup, and orphan discovery. Extend this registry with access policy rather
  than discarding it.
- `templates/comments/_qa_script.html:33-83` escapes comment and reply bodies
  before its `innerHTML` render and scopes multi-thread behavior per wrapper. The
  notification bell should follow this proven handling or, preferably, build DOM
  nodes with `textContent`.
- `content/services/course_units.py:61-108` makes preview, legacy open-course,
  registered, tier, and verification decisions explicit. That complexity encodes
  real product states; reuse it from comment authorization instead of copying a
  simplified tier comparison.
- `bookclub/views.py:663-684` and `bookclub/reading.py` deliberately batch privacy
  and progress work, with small-versus-large query checks in Book Club tests.
- `content/services/related_content.py:220-265` has careful event/workshop pair
  deduplication. Optimize candidate selection without weakening this semantic.
- The Mermaid runtime is self-hosted and loaded only on pages containing Mermaid
  nodes. Its large generated source should remain vendor-classified; replacing it
  is a dependency/distribution decision, not a first-party code cleanup.
- Questionnaire onboarding and its Studio response list have explicit attempt and
  query-scaling coverage. The service is large because it coordinates durable AI
  attempts and streaming/fallback states; split it only along those state-machine
  boundaries, not to chase file size.

### Checks actually performed

| Check | Outcome |
|---|---|
| Read `AGENTS.md`, `_docs/PROCESS.md`, `_docs/testing-guidelines.md` | Audit and test-layer constraints applied. |
| Repository inventories with `rg`, `find`, `wc`, and `du` | Distinguished first-party JS from the vendored Mermaid tree; identified largest templates and plan modules. |
| Cross-boundary source tracing with numbered lines | Followed project input through save/review/public render; comments through notification storage/API/bell; content UUIDs through page and direct API policies. |
| Focused markdown renderer check under `DJANGO_SETTINGS_MODULE=website.settings` | Current `render_markdown` preserved script/event-handler HTML; `sanitize_html` stripped it. No row was written. |
| Focused Book Club permission call | Current predicate returned `True` for an anonymous viewer of a level-zero note thread. No row was written. |
| Focused analytics injected-failure call | With enqueue and direct write patched to raise, `_enqueue_visit` propagated the direct-write exception. No row was written. |
| Existing tests reviewed | Located sequential behavior coverage and the missing security, failure-mode, row-scaling, and concurrency cases described above. |
| Git status before report | Only pre-existing `asl_cli/asl_cli/commands/events.py` and `asl_cli/tests/test_cli.py` edits were present; they were not touched. |

### Unexamined limits

- No browser or Playwright execution was performed, so the two XSS findings state
  that executable markup reaches browser sinks, not that JavaScript was executed
  during this audit.
- No PostgreSQL concurrency test, load test, `EXPLAIN`, memory profile, Lighthouse,
  accessibility audit, responsive screenshot pass, or cross-browser run was made.
- No production database, queue, logs, users, secrets, content repository, AWS
  resource, email/Slack/Zoom provider, AI provider, or GitHub API was accessed.
- Vendored Mermaid internals, generated migrations, caches, screenshots, and
  third-party library security advisories were not line-audited.
- API/Studio Python, events, plans, integrations, identity, payments, and messaging
  were examined only where necessary to prove an owned frontend/content boundary;
  their wider architecture belongs to the parallel audit owners.

### Account, event, admin and Studio JavaScript completion

### Scope and method

This bounded pass closes the account/event/Studio JavaScript coverage gap in
the main content/frontend audit. It reviews these eight tracked modules and
their current template and API boundaries:

- `static/js/accounts/auth-helpers.js`
- `static/js/accounts/inline-register.js`
- `static/js/events/event_detail.js`
- `static/js/events/event_series.js`
- `static/js/event-widget.js`
- `static/js/admin/timestamp_editor.js`
- `static/js/studio/dirty_form_guard.js`
- `static/js/studio/typeahead_lifecycle.js`

The pass was read-only except for this report. It did not repeat the plan
editor, member plan, checkpoint board, or Mermaid reviews. It followed values
through rendered templates and the API or form code that consumes them. It
also checked existing Django and Playwright coverage. No live services or full
test suites were used.

Outcome: two additional defects warrant catalog entries, `WEB-15` and
`WEB-16`. No executable DOM injection was found in these eight files: values
from APIs are rendered with `textContent`, input `value`, or constructed DOM;
the only reviewed `innerHTML` assignments clear a container or insert constant
button markup.

### Per-file coverage

| Module | Consumers and server boundary checked | Result |
|---|---|---|
| `static/js/accounts/auth-helpers.js` | Loaded by `templates/accounts/login.html:10`, `templates/accounts/register.html:10`, and `templates/accounts/password_reset_request.html:10`; their inline handlers use its CSRF, JSON, pending, and message helpers. | No additional finding. Messages use `textContent` at `static/js/accounts/auth-helpers.js:37-46`. The helpers assume their documented form elements exist, and all three current consumers provide them. Non-JSON responses fall into each caller's generic error path rather than becoming markup. |
| `static/js/accounts/inline-register.js` | Loaded by `templates/accounts/register.html:11-12` for `templates/accounts/includes/_register_form.html:1-29`; POSTs to `accounts.views.auth.register_api`. | No additional finding. `next` is emitted with `json_script`, parsed at `static/js/accounts/inline-register.js:18-25`, and sent to the API. The response redirect used at line 84 is produced only after `sanitize_verification_return_path` at `accounts/views/auth.py:380-383`; that sanitizer rejects external, protocol-relative, control-character, auth, admin, and member-only targets at `accounts/return_context.py:53-93`. Error strings are passed to the safe helper. The module's single `registerPending` flag and disabled button cover the current single-form contract. |
| `static/js/events/event_detail.js` | Loaded by `templates/events/event_detail.html:82` and `templates/plans/sprint_detail.html:315`; registration buttons live at `templates/events/_event_registration_card.html:276,287`, under the slug root at `templates/events/event_detail.html:17`. It calls `register_for_event` and `unregister_from_event`. | No additional finding. The sprint consumer has no event-detail slug or mutation buttons, so only the shared time-display/feedback initializers can bind. The API independently checks authentication, event state, scope, and tier at `events/views/api.py:465-530`; concurrency is guarded by the insert helper and `IntegrityError` path at lines 532-557. Unregister scopes deletion to `request.user` at lines 598-618. Display strings and errors are not assigned to HTML sinks. |
| `static/js/events/event_series.js` | Loaded by `templates/events/event_series.html:186`; reads the same template's staff-independent panel URLs at lines 72-116 and calls `series_registration`. | No additional defect. The endpoint authenticates at `events/views/api.py:633-666`, scopes rows to `request.user`, keeps POST idempotent at lines 702-738, and deletes only that user's future registrations at lines 668-700. Summary text uses `textContent` at `static/js/events/event_series.js:47-53`. The raw `window.gtag` call at lines 87-93 should eventually use `aslabAnalytics.trackWithNavigationFallback` for consistent default dimensions, but the local fallback still reloads and this is below a separate finding threshold. |
| `static/js/event-widget.js` | Loaded through `templates/_partials/event_widget_script.html:13` by blog, marketing, course-unit, and workshop-page templates. It hydrates markdown placeholders emitted by `content/markdown_extensions/event_widget.py:81` and calls `triggers.views.widget.widget_state` / `widget_claim`. | `WEB-16`. The claim API rechecks authentication and membership eligibility at `triggers/views/widget.py:140-177`, rate limits at lines 179-189, and emits only after those checks at lines 191-204. API labels and body use `textContent` at `static/js/event-widget.js:32-65,84-118`; no XSS sink was found. |
| `static/js/admin/timestamp_editor.js` | Declared by `content.admin.widgets.TimestampEditorWidget.Media` at `content/admin/widgets.py:41-45`, used by `events.admin.event.EventAdminForm` and `content.admin.course.UnitAdminForm`, and serializes the hidden JSON field rendered by `templates/admin/widgets/timestamp_editor.html:1-19`. | `WEB-15`. Constant action-button HTML at lines 54-60 is safe; label/time data are assigned through input values. The defect is silent time coercion and missing server shape validation. |
| `static/js/studio/dirty_form_guard.js` | Loaded globally from `templates/studio/base.html:1287`; activated only by the sticky action bar's `data-studio-dirty-guard-form` and currently used by article, marketing-page, course, event, questionnaire, redirect, call-host, persona, sprint, book, event-series, and plan forms. | No additional finding. It excludes disabled/read-only/hidden controls at lines 5-20, snapshots visible controls at lines 23-54, detects input/change at lines 140-145, and guards same-origin navigation and `beforeunload` at lines 157-187. `playwright_tests/test_studio_dirty_form_guard_1192.py:159-293` covers dirty/clean transitions, save, cancelled link navigation, synced operational fields, and unload cancellation. Hidden serialized editors remain covered when their visible inputs dispatch input/change; that convention should remain part of new editor reviews. |
| `static/js/studio/typeahead_lifecycle.js` | Used by `templates/studio/users/merge.html:244-289`, `templates/studio/courses/enrollments_list.html:169-222`, `templates/studio/courses/access_list.html:146-199`, and `templates/studio/includes/_people_picker.html:55,190-203`. The search endpoints are staff-gated at `studio/views/tier_overrides.py:125-142` and `studio/views/courses.py:429-457`. | No additional finding. Generation, current query, focus, and active-state checks at `static/js/studio/typeahead_lifecycle.js:25-71` reject stale responses. Consumers prevent blur on suggestion `mousedown`, use `encodeURIComponent` for queries, and construct result rows with `textContent`. Playwright coverage exercises selection and dismissal in `playwright_tests/test_studio_user_merge.py`, `test_studio_course_access.py`, and `test_studio_course_enrollments.py`. |

<a id="web-15"></a>

### WEB-15 — The Django admin timestamp editor silently turns malformed times into wrong chapter positions

| Field | Assessment |
|---|---|
| Priority | P2 |
| Confidence | High; confirmed by code and a focused form check. |
| Classification | Confirmed correctness defect. |
| Attacker prerequisite | None. Trigger requires a staff operator using the Django admin timestamp widget. |
| Effort | Small to medium, estimated 1–2 engineer days including browser coverage and existing-data inspection. |
| Estimated reduction | Estimate: centralizing parsing/validation can remove about 20–40 lines of duplicate timestamp rules across the admin widget and Studio recording editor; the main benefit is one storage contract rather than LOC. |

Evidence:

- `static/js/admin/timestamp_editor.js:13-27` accepts any two- or three-part
  colon string and replaces every unparseable component with zero. Thus
  `1:99` becomes 159 seconds, `abc:30` becomes 30 seconds, `abc` becomes zero,
  and negative components remain possible. The text input at lines 38-43 has
  only a placeholder, with no pattern, validity state, or error element.
- Every input event serializes the coerced number into the hidden field at
  `static/js/admin/timestamp_editor.js:72-85`, erasing what the operator typed
  before the server can explain the mistake.
- `events/admin/event.py:74-86` accepts any JSON list without validating item
  keys, types, ranges, or labels; malformed JSON is silently changed to an empty
  list. `content/admin/course.py:21-29` uses the same widget and relies on the
  generic JSON model field, which likewise has no timestamp-item validator.
- Both storage fields are unconstrained `JSONField`s at
  `events/models/event.py:290-293` and `content/models/course.py:347-350`.
  The public normalizer skips negative values at
  `content/templatetags/video_utils.py:239-256`, so some accepted admin rows
  disappear only when rendered.
- The client coercion is a separate defect from server validation: 159 and zero
  are valid canonical seconds once serialized, so accepting those numbers alone
  would not prove that item validation is absent.
- A focused local `EventAdminForm` check therefore used structurally invalid
  canonical data instead. It accepted
  `[{'time_seconds': -5, 'label': 'negative'}, 'not-an-object',
  {'label': 'missing time'}]` with no errors. This independently confirms that
  the server boundary does not enforce item shape or non-negative seconds.

Trigger and impact:

A staff member mistypes a chapter time, for example `1:99` or `abc`. The editor
immediately replaces the raw meaning with 159 or zero seconds and the form saves
successfully. The published chapter then seeks to the wrong position; a
negative value can be stored and silently omitted from the public player. A
malformed JSON submission can also clear all chapters without a validation
error. This is an operator-input integrity issue, not an untrusted-user attack.

Remediation:

1. Define one server-side timestamp item validator for the canonical
   `{time_seconds, label}` shape: require a list of objects, a non-negative
   integer number of seconds, and a string label with the intended length.
   Reuse it in both admin forms, and raise `ValidationError` rather than
   returning `[]` on malformed data.
2. Make the browser parser strict. Accept `MM:SS` or `H:MM:SS`, require numeric
   components, and require seconds and the non-leading minute component to be
   `0..59`. Show an inline error with `aria-invalid`/`aria-describedby`, retain
   the typed value, and block submission while any row is invalid.
3. Keep workshop-source `{time, title}` compatibility at its existing sync and
   normalization boundary. Do not make the Django admin widget silently accept
   two storage shapes.
4. Consider sharing validation semantics with
   `validate_event_timestamps_for_api`, which already gives the Studio recording
   form an actionable error at `studio/views/recordings.py:57-74`, while keeping
   explicit adapters for its current `{time, label}` input shape.

Regression checks:

| Layer | Check |
|---|---|
| Python unit | Table-test the shared validator with empty lists, canonical rows, zero, hour-long positions, booleans, floats, numeric strings, negative values, missing keys, non-object rows, malformed JSON, and overlong/non-string labels. Assert invalid values produce form errors and never become `[]`. |
| Django integration | POST both `EventAdminForm` and `UnitAdminForm` with valid and invalid timestamp payloads. Assert valid canonical data round-trips and invalid data leaves the model unchanged. |
| Playwright | On an actual Django admin event/unit page, enter `1:99`, `abc:30`, `abc`, and a negative value. Assert the offending input keeps its text, exposes an accessible error, save does not navigate, and the database remains unchanged. Then verify `59:59` and `1:00:00`, row reorder, removal, and reload. JavaScript parsing behavior belongs here rather than in an HTML string assertion. |
| Public player | Save valid boundary values and assert the chapter labels seek to exactly the persisted seconds; verify no valid row disappears during normalization. |

Rollout, backfill, rollback:

Before enforcement, run a read-only report over Event and Unit timestamp JSON to
count invalid shapes, negative values, and suspicious component-derived values;
`159` alone cannot prove an earlier `1:99`, so do not rewrite ambiguous positive
seconds automatically. Repair clearly invalid rows manually or from source.
Deploy server validation before or with the stricter UI. No schema migration is
required. Monitor admin validation errors without logging labels. Rollback may
remove the client constraint while keeping server validation and the operator's
typed value; it must not restore the silent malformed-JSON-to-empty-list path.

<a id="web-16"></a>

### WEB-16 — Embedded event claims send anonymous visitors to the home page after sign-in

| Field | Assessment |
|---|---|
| Priority | P2 |
| Confidence | High; the current request/response chain is deterministic. |
| Classification | Confirmed navigation defect. |
| Attacker prerequisite | None. Trigger requires an anonymous visitor using a claim widget embedded in content. |
| Effort | Small, estimated less than 1 engineer day including browser coverage. |
| Estimated reduction | No meaningful LOC reduction expected. A shared safe-return helper prevents another ad hoc URL contract. |

Evidence:

- `static/js/event-widget.js:125-129` fetches
  `/widgets/<slug>/state` without the current page as a `next` parameter.
- For an anonymous user, `triggers/views/widget.py:103-107` builds
  `login_url` from `request.GET.get('next', '/')`. The actual hydration request
  therefore always produces `/accounts/login/?next=/`.
- `static/js/event-widget.js:94-100` assigns that URL to the visible “Sign in to
  claim” link. Clicking from a blog article, marketing page, course unit, or
  workshop page takes the visitor through login and then to `/`, losing the
  content and claim context.
- Existing Playwright coverage at
  `playwright_tests/test_event_widget_1070.py:240-269` checks that the anonymous
  CTA is visible and styled but does not assert its target or complete the
  login-return-claim flow.
- A focused `_build_state` check supplied
  `next=/blog/kept?ref=x&sort=new`. The backend returned
  `/accounts/login/?next=/blog/kept?ref=x&sort=new`; parsing that login query
  produced `next=['/blog/kept?ref=x']` and a separate `sort=['new']`. This
  confirms that raw concatenation loses part of the intended return target.
  This is not an executable URL in the current sink, but the fix must sanitize
  and encode rather than merely append `window.location.href`.

Trigger and impact:

An anonymous visitor reaches an embedded claim CTA in eligible content and
selects “Sign in to claim.” After authentication, the normal return-path logic
honors the supplied `/`, so the visitor lands on the home page. They must find
the original content again before claiming, which creates avoidable abandonment
in the exact conversion path the widget exists to support. No privilege bypass
or open redirect was established.

Remediation:

1. Send the current local path, query, and fragment as the widget's intended
   return target. Because fragments are never sent in HTTP requests, the client
   must include it explicitly if fragment preservation is desired.
2. At the server boundary, run the value through the existing local-return URL
   policy in `accounts.return_context` and build the login URL with
   `append_next` or `urlencode`. Do not interpolate a raw nested query string.
3. Return the resulting same-origin login URL in widget state and keep the
   client as a thin renderer. Define whether auth/admin/member-only destinations
   use the stricter verification exclusions; the current public content
   consumers need only public local destinations.
4. Add the return-path assertion to the existing event-widget flow rather than
   creating a second test of the same hydration behavior.

Regression checks:

| Layer | Check |
|---|---|
| Python unit | Request widget state with absent, local path, query, fragment-encoded, protocol-relative, external, backslash, and control-character `next` values. Assert the returned login URL has one correctly encoded local `next` value and unsafe targets fall back to `/`. |
| Playwright | Visit a public article with a widget as an anonymous user, including an ordinary query parameter. Click the sign-in CTA, authenticate, and assert return to the same article/query, then claim successfully. Repeat one workshop or course-unit consumer only if it exercises a distinct access gate. |
| Playwright security | Supply an unsafe return target through the hydration request and assert the CTA stays same-origin and post-login navigation falls back safely. Inspect the anchor target and final browser URL; an HTML body string assertion is insufficient. |

Rollout, backfill, rollback:

No backfill is required. Deploy the server sanitizer/encoder first so old clients
remain safe, then send the page target from the widget client. Track anonymous
sign-in CTA clicks and completed claims by page without logging full query
strings. Rollback can stop sending `next`; retain server sanitization and
encoding. The safe fallback is the current home-page return.

### Cross-file simplification observation

`auth-helpers.js` centralizes account CSRF/JSON/pending behavior, while
`static/js/events/event_detail.js:2-28`, `static/js/events/event_series.js:2-28`, and
`static/js/event-widget.js:19-24` each implement another cookie reader and the event
modules repeat button and JSON-error handling. Their behavior currently differs
in cookie decoding, non-JSON responses, error presentation, and analytics
dispatch. This did not establish another user-visible defect in the bounded
pass, so it is not assigned a finding ID. When `WEB-13` introduces a small
same-origin JSON client, these event mutations are practical follow-on
consumers. Preserve endpoint-specific state rendering and do not turn a small
helper into a broad frontend framework. Estimate: roughly 40–80 duplicated
lines may be removable after browser coverage pins the current interactions.

### Checks performed and limits

| Check | Result |
|---|---|
| `node --check` on all eight reviewed modules | Passed. |
| Focused anonymous `_build_state` call with `next=/blog/kept?ref=x&sort=new` | Returned `/accounts/login/?next=/blog/kept?ref=x&sort=new`; `parse_qs` treated `sort` as a separate login parameter and truncated `next` to `/blog/kept?ref=x`. No database row was written. |
| Focused `EventAdminForm` validation with negative, non-object, and missing-key items | Form was valid with no errors and retained all three invalid items, independently confirming absent server item validation. No model was saved. |
| Current tests inspected | Account auth tests cover safe redirect sanitization; event API suites cover registration/access/idempotency; event-widget Playwright covers visual states and claiming; timestamp tests cover ordinary valid JSON and Studio editor behavior; dirty-form and typeahead Playwright suites cover their main interactions. |

No browser tests were executed, so the report does not claim observed browser
navigation or JavaScript execution. Django decorators and server authorization
were inspected only for the concrete API consumers named above. Vendor assets,
plan scripts, Mermaid, unrelated inline scripts, and Python domain behavior
beyond these trust boundaries remain outside this completion pass.

## Identity, payments, messaging, and background work

Date: 2026-09-07

Scope: `accounts/`, `payments/`, `email_app/`, `community/`, `crm/`, `triggers/`, `notifications/`, and `jobs/`.

This is a static, read-only audit of the current checkout. It makes no claim about production incidence. “Confirmed” below means the control flow or database invariant is provable from the checked-out code and schema. “Reproduced” means a local, isolated check demonstrated the behavior. “Suspected runtime risk” means the code admits the failure, but its occurrence depends on timing, provider behavior, configuration, or traffic not observed here.

### Executive assessment

The largest simplification opportunity is to establish one business authority at each asynchronous boundary:

| Boundary | Current authority is split across | Target authority |
|---|---|---|
| Login email | Stripe webhook, `User`, `EmailAlias`, allauth rows, email-change requests | One verified identity-change service; Stripe email is billing/contact evidence only |
| Community access | Payment handlers enqueue imperative invite/remove/reactivate actions | One reconcile-to-current-entitlement command, carrying an expected membership version |
| Member messaging | `EmailLog`, `EventReminderLog`, notification rows, campaign deliveries, host-specific log lookups, Slack functions | One durable delivery intent per business event, recipient, and channel, with explicit attempt and outcome state |
| Stripe event execution | Attempt row, terminal `WebhookEvent`, handler-specific locks and ledgers | One claimed event execution record with a lease, then handler dispatch |

The code already contains good examples of these ideas. Campaign delivery now has a durable per-recipient ledger; trigger delivery snapshots payload and secret state into a leased job; scheduled community removal rechecks effective access; email change locks both the request and user. The remediation should extend those proven patterns instead of adding more one-off flags.

Prior-audit status was rechecked rather than copied. The campaign dispatch, password-reset-token, API-token inactive-user, merge-credential, SES-message-index, and effective-tier issues described in the 2026-08-30 audit are materially remediated in current code. The generic `EmailService.send` path in IAM-01 remains distinct from the closed campaign work. IAM-05 and IAM-08 reverify still-open issues #1519 and #1525.

### Findings

<a id="iam-01"></a>

### IAM-01 — Generic email deduplication claims after the external send

Priority: P1  
Classification: confirmed code defect; crash-window incidence is a suspected runtime risk  
Confidence: high  
Effort: medium, 3–6 engineering days plus migration and rollout observation

Evidence:

- `email_app/services/email_service.py:193-209` describes `dedupe_key` as durable lifecycle idempotency, then performs only an unlocked `EmailLog` existence check.
- `email_app/services/email_service.py:249-258` calls SES before any row owns the key.
- `email_app/services/email_service.py:270-278` inserts the uniquely constrained log only after SES accepts the request.
- `email_app/models/email_log.py:78-87` makes `dedupe_key` unique. That constraint protects the database row, not the already-completed SES side effect.
- Current callers rely on this primitive in `payments/services/webhook_handlers.py:988-1001`, `payments/services/monthly_payment_grace.py:816-821`, and `accounts/services/privacy.py:302-310`.

Trigger and impact: two workers can both observe no row and send. One then loses the unique insert, after both messages are already accepted. A worker death between SES acceptance and line 271 leaves no dedupe evidence, so a retry sends again. The result can be duplicate payment-failure, grace, or privacy messages, plus a misleading task failure after successful delivery. This does not revive the closed campaign bug: campaign dispatch has its own stronger ledger and does not use this check as its only claim.

Remediation:

1. Add a generic `DeliveryIntent` or narrowly scoped transactional-email delivery row keyed by the existing business `dedupe_key`.
2. Claim the row atomically before transport; record `pending`, `dispatching`, `sent`, `failed_retryable`, and `ambiguous` outcomes plus `transport_started_at`, provider ID, attempts, and a lease token.
3. Treat a timeout after request transmission as ambiguous. Do not promise exactly once across an unknown provider response; require provider lookup, a provider-supported idempotency key, or an operator retry decision.
4. Make `EmailLog` the immutable successful-attempt evidence linked to the intent, rather than the claim itself.
5. Move the three callers to the new authority and remove their duplicated preflight assumptions.

Regression scenarios:

- PostgreSQL `TransactionTestCase`: two threads start on the same key behind a barrier; mocked SES is reached once and both callers resolve to the same delivery result.
- Fault-injection service test: provider returns an ID, then code raises before finalization; retry reports `ambiguous` and does not automatically resend.
- Service tests: retryable pre-transport failure releases or reschedules the lease; opted-out promotional sends never create a transport attempt.

Rollout/backfill/rollback: backfill successful intents from non-null `EmailLog.dedupe_key` rows. Deploy the model and dual-write observation first, then claim through it caller by caller. Keep old log reads during rollback. Never auto-resend an `ambiguous` historical row.

Estimated reduction: 40–100 production lines once caller-specific existence checks and delivery markers converge. This estimate overlaps IAM-10, IAM-12, and IAM-13 and must not be added to them mechanically.

<a id="iam-02"></a>

### IAM-02 — Queued community actions can apply obsolete payment state

Priority: P1  
Classification: confirmed code defect; harmful ordering requires a queue race  
Confidence: high  
Effort: medium, 3–5 engineering days

Evidence:

- `payments/services/community_hooks.py:16-78` enqueues imperative `invite`, `reactivate`, and `remove` tasks with only `user_id`.
- `community/tasks/hooks.py:119-172` reloads the user but never checks current effective tier, subscription generation, or expected transition before calling Slack.
- `community/tasks/removal.py:26-61` demonstrates the correct boundary: scheduled removal reloads the user and skips removal when `get_user_level(user) >= 20`.

Trigger and impact: an invite queued after checkout can run after a later cancellation; a remove queued after subscription deletion can run after a later resubscription or a new tier override. The old action then changes Slack membership or sends an obsolete invite email. The ORM broker makes queue insertion part of the surrounding database transaction where applicable, but it does not preserve ordering across separately committed payment transitions.

Remediation:

1. Replace the three action tasks with `reconcile_community_membership(user_id, expected_membership_version=None)`.
2. Compute desired access from the current effective entitlement at task execution.
3. Increment a membership version in the same transaction that changes entitlement; include it in the queued payload and discard older versions.
4. Make Slack changes convergent (`ensure present` / `ensure absent`) and attach audit rows to the observed version and desired state.
5. Use a repair schedule for enqueue outages rather than relying on support replay of imperative actions.

Regression scenarios:

- Service test: capture an invite task, cancel the subscription, then execute it; assert no invite email, Slack call, or success audit.
- Service test: capture a remove task, resubscribe or add an active Main override, execute it; assert the member remains.
- Transaction test: execute versions N and N+1 in reverse order and assert final Slack intent reflects N+1 exactly once.

Rollout/backfill/rollback: derive the initial version for all users, deploy the reconciler so old queued wrappers call it, then change producers. Run a read-only desired-versus-observed report followed by bounded reconciliation. Keep wrappers for rollback while making them revalidate state.

Estimated reduction: 70–140 production lines by deleting action-specific enqueue/task branches and support replay paths.

<a id="iam-03"></a>

### IAM-03 — Stripe billing-email sync bypasses verified identity change

Priority: P1  
Classification: confirmed trust-policy inconsistency; account-security impact is a suspected risk, not a demonstrated takeover  
Confidence: high for behavior, medium for exploitability  
Effort: medium, 3–6 engineering days plus data review

Evidence:

- `_docs/product.md:165` explicitly lists customer email sync as shipped, so the behavior is intentional today.
- `payments/services/webhook_handlers.py:1798-1829` declares Stripe `customer.updated` authoritative for local email.
- `payments/services/webhook_handlers.py:1838-1921` resolves by customer ID, checks collisions, and directly writes `User.email`.
- The handler does not lock the user, update allauth email rows, clear Slack identity cache, change `email_verified`, or create/confirm `EmailChangeRequest`.
- By contrast, `accounts/services/email_change.py:283-369` locks the request and user, checks availability at confirmation, marks the address verified, synchronizes allauth, and preserves the former address as an alias.

Trigger and impact: a member edits the billing email in Stripe’s portal. The next webhook changes the login and password-reset address without proof that the new mailbox is controlled, while secondary identity tables can retain the old address. A portal session normally starts from an authenticated account, so this audit does not assert a standalone account-takeover path. The confirmed problem is that billing contact data silently bypasses the application’s stated verification and identity synchronization rules.

Remediation:

1. Decide and document the trust boundary: store Stripe email as billing contact/evidence, not primary authentication identity.
2. On `customer.updated`, update a billing-email field or mismatch record and notify the member at the verified login address.
3. If product requires identity sync, create a pending `EmailChangeRequest` and require proof at the new mailbox through the existing service.
4. Route all successful identity changes through one service that updates `User`, allauth, aliases, verification state, session/security policy, and relevant caches together.

Regression scenarios:

- Payment-handler test: a different Stripe email leaves `User.email`, allauth rows, aliases, and verification state unchanged and records billing evidence.
- Account-service test: only confirmation of a valid email-change request changes every identity representation atomically.
- Race test: concurrent Stripe update and account confirmation produces one coherent identity and no unique-constraint 500.

Rollout/backfill/rollback: inventory `CommunityAuditLog(action="email_synced_from_stripe")`. Do not automatically revert addresses because some may be legitimate and verified elsewhere; flag them for user or operator confirmation. Add the billing field before stopping primary sync. Rollback can restore the handler while retaining billing evidence.

<a id="iam-04"></a>

### IAM-04 — GET requests mutate unsubscribe preferences

Priority: P1  
Classification: confirmed code defect; scanner incidence is a suspected runtime risk  
Confidence: high  
Effort: small, 1–2 engineering days

Evidence:

- `email_app/views/newsletter.py:199-284` accepts GET and POST, but writes `user.unsubscribed=True` at lines 270–272 before branching by method.
- `email_app/views/newsletter.py:287-314` similarly writes `email_preferences["maven_emails"]=False` for GET or POST.
- `email_app/services/email_service.py:497-505` puts the unsubscribe token in a URL, and `email_app/services/email_service.py:597-614` advertises that URL through `List-Unsubscribe` plus the RFC one-click POST header.
- The general unsubscribe JWT intentionally has no expiry (`accounts/utils/tokens.py:63-88`).
- `email_app/tests/test_newsletter.py:443-447` currently codifies the unsafe GET mutation.

Trigger and impact: mail security scanners, browser prefetchers, or link-preview bots issue GET requests to links before a human clicks. That can globally unsubscribe a member or disable Maven mail without intent. RFC one-click behavior uses the POST callback; it does not require the human landing-page GET to mutate.

Remediation:

1. Make GET and HEAD validate the token and render a confirmation page without writing.
2. Preserve a `csrf_exempt`, idempotent POST for mailbox-provider one-click callbacks.
3. For browser confirmation, post a form with CSRF; if the same endpoint must serve both, distinguish the signed provider POST contract from the interactive form clearly.
4. Apply the same method contract to Maven preference links.

Regression scenarios:

- Django view tests: scanner-like GET and HEAD leave preferences unchanged.
- View test: valid one-click POST changes the preference once and remains idempotent.
- Browser/Playwright flow: GET renders confirmation, human POST changes the preference, invalid token does not.

Rollout/backfill/rollback: no reliable data distinguishes scanner opt-outs from human opt-outs, so avoid automatic resubscription. Measure GET/POST traffic before and after, and offer members an ordinary preferences UI. The old response page can remain as rollback-compatible presentation.

<a id="iam-05"></a>

### IAM-05 — Signup and newsletter user acquisition still race on unique email

Priority: P2  
Classification: confirmed code defect; reverified open issue #1519  
Confidence: high  
Effort: small, 1–3 engineering days

Evidence:

- `accounts/models/user.py:96-99` makes `User.email` unique.
- `accounts/views/auth.py:396-414` performs `exists()` and then `create_user()` without handling `IntegrityError`.
- `email_app/views/newsletter.py:153-181` performs `get()` and then `create_user()` without handling `IntegrityError`.

Trigger and impact: simultaneous registration/subscription requests for the same normalized email can both pass the read. One insert loses and returns a 500. A registration and newsletter request racing each other can also create semantics based on whichever endpoint happens to win, while the loser does not send the response promised by its flow.

Remediation:

1. Create one normalized-email acquisition service.
2. Attempt creation inside an inner atomic block; catch `IntegrityError` outside that savepoint and fetch the winner.
3. Keep endpoint semantics explicit: registration must not silently convert an existing newsletter-only account into a password account without the proper authenticated/verification flow; newsletter remains enumeration-safe and may resend verification.

Regression scenarios:

- PostgreSQL `TransactionTestCase` with barriers for register/register, subscribe/subscribe, and register/subscribe.
- Assert one user, no 500, no duplicate verification sends, and endpoint-appropriate response semantics.

Rollout/backfill/rollback: no schema backfill. Deploy the shared service behind both endpoints. Rollback is limited to callers because the helper does not change stored state shape.

Estimated reduction: 20–40 production lines by removing two acquisition implementations.

<a id="iam-06"></a>

### IAM-06 — LLM progress apply can overwrite and later undo a human completion

Priority: P1  
Classification: confirmed concurrency defect  
Confidence: high  
Effort: medium, 2–4 engineering days

Evidence:

- `crm/tasks/apply_plan_sprint_progress.py:123-138` loads messages and plan items, then performs an unbounded-time external LLM call outside the transaction.
- At `crm/tasks/apply_plan_sprint_progress.py:143-186`, the transaction uses the stale in-memory item, checks `done_at`, overwrites it, and records `previous_done_at=None`; it neither refetches nor locks the plan item.
- `crm/tasks/apply_plan_sprint_progress.py:199-223` restores `previous_done_at` on undo, which can therefore clear a human completion that happened during the LLM call.

Trigger and impact: staff or the member completes a plan item while parsing is in flight. The worker’s stale object still has `done_at=None`, overwrites the human timestamp, and claims provenance. A later undo sets it back to null. Member progress and audit provenance are corrupted.

Remediation:

1. Keep the LLM call outside the transaction.
2. Inside the transaction, lock/refetch each parsed target and the event/watermark row.
3. Apply completion with a conditional `filter(pk=..., done_at__isnull=True).update(done_at=now)`; create `AppliedProgressChange` only when the update count is one.
4. On undo, conditionally restore only if the current value still matches this change’s applied timestamp/version. Record conflicts instead of overwriting newer human work.

Regression scenarios:

- PostgreSQL `TransactionTestCase`: pause the parser, manually complete the item, resume it; assert no applied-change row and that undo preserves the human timestamp.
- Service test: a human edit after automatic apply makes undo report a conflict and keep the newer value.
- Service test: two ingests for different watermarks cannot both own the same completion.

Rollout/backfill/rollback: add an `applied_done_at` or row-version field before enforcing conditional undo. Existing change rows cannot prove whether an identical timestamp came from a human; require conservative operator review for old undo operations. Rollback leaves the extra provenance harmless.

<a id="iam-07"></a>

### IAM-07 — Stripe event idempotency is a terminal marker, not an execution claim

Priority: P1  
Classification: confirmed protocol defect; downstream impact varies by handler  
Confidence: high  
Effort: medium/large, 5–8 engineering days

Evidence:

- `payments/services/webhook_dispatch.py:372-385` creates an attempt and then checks `WebhookEvent.exists()`.
- `payments/services/webhook_dispatch.py:387-497` executes the handler before the terminal event row exists.
- `payments/services/webhook_dispatch.py:498-506` records the terminal row only after a clean handler return.
- `payments/services/signatures.py:40-42` is an unlocked existence check; `payments/services/signatures.py:63-80` uses `get_or_create` only for the post-handler marker.
- For one concrete path, `payments/services/webhook_handlers.py:1668-1708` locks the user but will reapply a scheduled-cancellation event and enqueue scheduling on each concurrent execution. Handler-specific ledgers make other paths safer, but they do not establish a single event execution.

Trigger and impact: duplicate Stripe deliveries or an operator replay overlap before either terminal marker is committed. Both run the handler. Consequences depend on the handler and its local guards: redundant queue jobs/audits are straightforward; external sends or newly added side effects can duplicate; lock ordering increases contention. The system’s event-level idempotency contract is therefore weaker than its documentation.

Remediation:

1. Replace terminal-only `WebhookEvent` semantics with a claimed execution state: a pre-handler claim, lease token/expiry, explicit `side_effect_started` or equivalent evidence, terminal outcomes, and attempt linkage.
2. Atomically insert the event claim before dispatch. A live owner returns/retries without invoking the handler. Reclaim an expired claim automatically only when durable state proves that handler execution or side effects never started.
3. Preserve Stripe retries for failures proven to occur before handler/side-effect start by releasing or safely reclaiming that claim. An expired lease after the handler may have started is `ambiguous`; a later Stripe delivery must reconcile handler-specific evidence or require operator action rather than automatically re-run the handler. Retain permanent outcomes terminally.
4. Define the crash-after-side-effect case explicitly. Handler business ledgers still remain necessary for provider calls that cannot be rolled back, and each handler needs a deterministic reconciliation rule before an ambiguous event can advance.

Regression scenarios:

- PostgreSQL `TransactionTestCase`: concurrent `process_event` calls for the same ID reach a counting handler once and produce two delivery attempts linked to one execution.
- Fault injection: crash before handler start leaves a reclaimable claim; crash after `side_effect_started` leaves an ambiguous claim that a retry does not execute automatically; crash after terminal commit returns `already_processed`.
- Per-handler tests: cancellation scheduling, checkout fulfillment, and payment-failure email preserve current retry semantics under the new claim.

Rollout/backfill/rollback: extend the existing table rather than create a second event identity if practical. Backfill all existing terminal rows as terminal; do not infer `processing` from old received attempts. Deploy read compatibility before activating claims. A feature flag can revert dispatch while leaving state columns intact.

<a id="iam-08"></a>

### IAM-08 — Stripe attempt numbers use unlocked `MAX + 1`

Priority: P2  
Classification: confirmed concurrency defect; reverified open issue #1525  
Confidence: high  
Effort: small/medium, 1–3 engineering days when combined with IAM-07

Evidence:

- `payments/services/webhook_dispatch.py:185-210` computes `Max("attempt_number") + 1` and inserts later.
- `payments/models/stripe_webhook_delivery.py:116-121` has indexes but no uniqueness constraint for `(stripe_event_id, attempt_number)`.

Trigger and impact: two deliveries for one event read the same max and store the same attempt number. Ordering and “delivery number N” diagnostics become ambiguous exactly during the duplicate/retry incidents where they matter most.

Remediation:

1. Allocate attempt numbers while locking the event execution row from IAM-07.
2. Add a database unique constraint on `(stripe_event_id, attempt_number)` as defense in depth.
3. If IAM-07 is delayed, introduce a small per-event counter row and retry allocation on conflict; do not retain standalone `MAX + 1`.

Regression scenarios:

- PostgreSQL `TransactionTestCase`: N simultaneous attempts receive exactly `1..N` once each.
- Migration test: duplicate historical numbers are deterministically renumbered by `received_at`, then PK, before the constraint is added.

Rollout/backfill/rollback: first audit and renumber duplicate pairs, then add the constraint. Consumers should sort by `received_at, id` during transition. Rollback can drop the constraint without losing attempt rows.

<a id="iam-09"></a>

### IAM-09 — Account merge mutates users and relations without locking either account

Priority: P1  
Classification: suspected runtime correctness risk from a confirmed missing synchronization boundary  
Confidence: high for missing locks, medium for incidence  
Effort: medium, 3–5 engineering days

Evidence:

- `accounts/services/account_merge.py:796-815` receives already-loaded `canonical` and `secondary` objects and opens a transaction.
- The service has no `select_for_update` call anywhere.
- `accounts/services/account_merge.py:653-737` reconciles scalar fields from those potentially stale objects and performs an unrestricted `canonical.save()`.
- `accounts/services/account_merge.py:815-892` repoints relations, registers the alias, scrubs/deactivates the secondary, and writes the audit in that transaction.

Trigger and impact: two operators merge the same secondary into different canonical users, or a payment/profile update commits while a merge is building its plan. Relations can split between canonical accounts, stale scalar values can overwrite a concurrent payment/profile change, and aliases/audits may describe a state that was not the actual source state. The transaction prevents partial rollback within one invocation but does not serialize competing invocations.

Remediation:

1. At the beginning of the transaction, lock/refetch both users in deterministic PK order. This serializes competing merges and other writers that acquire the same locks; it does not stop a writer that loaded stale state before the lock and later performs an unconditional save.
2. Re-run every guard and build the plan from locked rows, including staff and subscription-conflict checks.
3. Lock conflict-sensitive alias and active entitlement rows before reconciliation.
4. Replace unrestricted `canonical.save()` with explicit changed fields or conditional/versioned updates. Audit payment, profile, identity, and entitlement writers for the same fields: make them participate in the lock protocol or use a row version/compare-and-swap that raises an explicit conflict instead of stale-saving after the merge commits.
5. Consider a merge-operation row with source uniqueness so two different targets cannot claim one secondary.

Regression scenarios:

- PostgreSQL `TransactionTestCase`: two merges sharing a secondary cannot both succeed; all relations and alias resolve to one target.
- Concurrent payment update loaded before the merge lock cannot stale-save afterward: it preserves the newest subscription identifiers or produces an explicit version conflict.
- Concurrent profile/identity update, including a writer that does not initially acquire the merge lock, either participates in the final lock/version check or produces an explicit conflict, never a lost update.

Rollout/backfill/rollback: inspect existing merge audit rows and aliases for a secondary referenced by multiple canonical users or relations left on scrubbed accounts. Avoid automatic repair where ownership is ambiguous. Row locking needs no data migration; an operation model can be introduced compatibly.

<a id="iam-10"></a>

### IAM-10 — Reminder markers suppress failed channels permanently

Priority: P1  
Classification: confirmed delivery-state defect  
Confidence: high  
Effort: medium, 3–5 engineering days, preferably under IAM-01’s delivery authority

Evidence:

- `notifications/services/notification_service.py:636-647` explicitly uses one `EventReminderLog` row as the dedupe gate for bell and email.
- `notifications/services/notification_service.py:668-682` persists the marker and bell before attempting email.
- `notifications/services/notification_service.py:684-709` swallows any email failure; every later cron tick sees the marker and skips the email.
- `notifications/services/event_reminders.py:95-114` similarly persists the Slack guard before posting and swallows the failure.

Trigger and impact: a transient SES/Slack timeout, invalid short-lived configuration, or provider outage during the first attempt means the time-sensitive channel is never retried. The marker means “attempt was claimed,” while its model/docstring says reminders “have been sent.” Moving the marker after the call would create the inverse duplicate-send crash window, so ordering alone cannot repair this.

Remediation:

1. Create one reminder business event and independent channel deliveries for bell, email, and Slack.
2. Mark the database-only bell successful transactionally.
3. Give external channels attempt/outcome state and bounded retry; preserve an explicit terminal failure visible to operators.
4. Reuse IAM-01’s transport semantics instead of adding reminder-specific booleans.

Regression scenarios:

- Service test: first SES call fails before transport, second scheduled pass retries email without creating a second bell.
- Service test: Slack 429 is retryable and does not turn the guard into success.
- Fault injection after provider acceptance results in `ambiguous`, not blind resend.

Rollout/backfill/rollback: treat historical markers with no corresponding `EmailLog` as unknown, not safe to resend after the event time. Backfill successful channel rows where logs exist. Activate new delivery state only for future reminders. Preserve old markers for rollback reads.

<a id="iam-11"></a>

### IAM-11 — Nullable user defeats the per-event Slack uniqueness guarantee

Priority: P1  
Classification: confirmed schema defect; locally reproduced for the configured development database semantics  
Confidence: high  
Effort: small, 1–2 engineering days

Evidence:

- `notifications/models/notification.py:87-135` permits `user=NULL` and uses `unique_together(event, user, interval)` to claim one `24h_slack` row.
- `notifications/services/event_reminders.py:95-108` relies on `get_or_create(event=event, user=None, interval="24h_slack")` as the concurrency guard.
- Ordinary SQL uniqueness treats null values as distinct. An isolated in-memory SQLite reproduction inserted two identical `(event_id=1, user_id=NULL, interval="24h_slack")` rows under the declared three-column unique constraint and returned a count of 2. PostgreSQL’s default unique-null behavior has the same boundary unless `nulls_distinct=False` or a conditional constraint is used.
- Existing tests at `notifications/tests/test_event_reminders.py:410-472` are sequential and therefore do not exercise competing creates.

Trigger and impact: overlapping cron workers both fail to find a guard and both successfully insert a nullable-key row, then both post the channel announcement. Members see duplicate Slack announcements and the database permanently contains multiple supposed singleton guards.

Remediation:

1. Add separate conditional uniqueness: `(event, interval)` where `user IS NULL`, and `(event, user, interval)` where `user IS NOT NULL`; or use PostgreSQL `nulls_distinct=False` after checking all intervals.
2. Deduplicate existing nullable guard rows before migrating.
3. Retain `get_or_create`; the database must be the arbiter under concurrency.

Regression scenarios:

- PostgreSQL `TransactionTestCase`: concurrent guard creation yields one row and one Slack call.
- Migration test: multiple historical null guards collapse deterministically without touching per-user rows.

Rollout/backfill/rollback: delete duplicate nullable guards while retaining the earliest. Add the constraint in a migration suitable for table size/locking. Rolling back the constraint does not require restoring duplicates.

<a id="iam-12"></a>

### IAM-12 — General announcement fan-out has no durable business-event identity

Priority: P2  
Classification: maintainability observation with confirmed duplicate-admitting paths  
Confidence: high  
Effort: medium/large, 5–10 engineering days under the shared delivery work

Evidence:

- `notifications/models/notification.py:39-81` has no source/business-event key on `Notification`.
- `notifications/services/notification_service.py:418-499` bulk-creates all bell rows, posts Slack, and optionally loops through email with no batch or per-recipient claim.
- `notifications/services/notification_service.py:504-555` repeats the unclaimed bulk fan-out for series.
- Admin actions call it directly after broad status updates: `content/admin/article.py:9-17`, `content/admin/course.py:87-91`, `content/admin/download.py:9-14`, `events/admin/event.py:46-50`, and `voting/admin/poll.py:23-27`.
- Studio’s `studio/views/notifications.py:140-151` uses a recent-notification check before fan-out, which is another check-then-act race and expires after 24 hours.

Trigger and impact: repeated admin actions, two Studio requests, or retry after partial failure create duplicate bells and can repeat Slack/email. A partial pass cannot resume only missing recipients because no announcement batch records the intended audience and channel outcomes. Whether reopening/reannouncing is desirable is a product decision; the code currently cannot distinguish an intentional new announcement from a duplicate execution.

Remediation:

1. Create an explicit announcement event/batch with a caller-supplied idempotency key and optional “intentional resend” generation.
2. Snapshot or deterministically define the audience, then create per-recipient/channel intents through the shared delivery authority.
3. Make all Django admin and Studio actions call the same announcement command after commit.
4. Delete title/time-window dedupe once durable event identity is authoritative.

Regression scenarios:

- Concurrent Studio POSTs for one generation produce one batch, one bell per eligible user, and one Slack intent.
- Partial email failure resumes only failed recipients.
- Explicit resend creates generation N+1 and is visibly distinct in operator history.
- Authorization remains covered at the Studio view layer; delivery services accept no request-derived audience overrides.

Rollout/backfill/rollback: apply only to future announcements; historical notification titles are insufficient reliable keys. Start with Studio, then admin actions. Preserve the legacy service as a compatibility adapter during rollback.

Estimated reduction: 100–220 production lines across recent-title checks, specialized fan-out loops, and channel-specific recovery. This overlaps IAM-01 and IAM-10.

<a id="iam-13"></a>

### IAM-13 — The recording upload worker does not validate ownership of the current lease

Priority: P2  
Classification: suspected runtime risk from confirmed non-idempotent worker control flow  
Confidence: high for duplicate work, medium for user-visible duplication  
Effort: medium, 3–5 engineering days

Evidence:

- `events/services/recording_upload.py:63-90` has a row-locked enqueue lease, a good producer-side guard.
- `jobs/tasks/recording_upload.py:46-75` reloads the event, then unconditionally refreshes `recording_upload_enqueued_at`; it does not check whether `recording_s3_url` is already present and carries no lease token.
- `jobs/tasks/recording_upload.py:96-129` always downloads, uploads the deterministic S3 key, and saves the URL.
- `jobs/tasks/recording_upload.py:136-169` invokes host notification after upload.
- The host notifier itself checks for an existing log and sends before inserting it at `events/services/recording_ready_notification.py:143-180`; locking an empty query result does not serialize two first sends.

Trigger and impact: duplicate queue rows, a stale reclaimed worker, or task retry can run the whole download/upload after another worker succeeded. This wastes Zoom bandwidth, disk, worker time, and S3 transfer, and can overwrite the same object. Concurrent first completion can also duplicate a host email through the same pre-send empty-check window as IAM-01.

Remediation:

1. Issue a lease token/generation when enqueueing and pass it to the worker.
2. At worker start, atomically verify the token and unfinished state. An obsolete or already-uploaded task returns without external work.
3. Heartbeat/extend only the owned token during long transfers; reclaim creates a new token.
4. Record upload state and object provenance before notification: lease generation, expected S3 version/ETag or checksum, size, and task identity as supported by the provider. A deterministic key alone does not prove that the object was produced by the current generation. Dispatch host mail through the shared delivery intent.

Regression scenarios:

- Service test: invoking the task after `recording_s3_url` exists does not call Zoom, S3, or email.
- Transaction test: old and reclaimed token execute in reverse order; only the current token reaches download.
- Fault injection after S3 success but before DB finalize performs a provider metadata lookup and reconciles only when generation/checksum/version provenance proves that object belongs to the interrupted attempt. A key-only match stays ambiguous and does not publish, notify, or overwrite automatically.

Rollout/backfill/rollback: add nullable generation/state/provenance fields; initialize completed events as complete but mark provenance unknown unless existing object metadata proves it. Initialize unfinished events with no active token. Producer and worker should accept both payload shapes for one deploy window. Keep deterministic S3 keys for compatibility, while treating them as location rather than ownership proof.

<a id="iam-14"></a>

### IAM-14 — Seven Slack POST implementations bypass the existing retrying client

Priority: P2  
Classification: maintainability and operability observation  
Confidence: high  
Effort: medium, 3–6 engineering days

Evidence:

- `community/services/slack.py:120-220` already implements common authentication, HTTP/JSON validation, and one bounded `Retry-After` retry.
- `notifications/services/slack_announcements.py:260-339`, `342-434`, and `437-506` contain three near-identical raw `requests.post` implementations without that 429 handling.
- `community/services/staff_notifications.py:352-368`, `424-464`, `609-663`, and `884-950` add four more raw `chat.postMessage` implementations.
- The three files total 2,466 production lines; formatting is legitimately domain-specific, but transport policy is repeated.

Trigger and impact: under Slack rate limiting, community membership calls retry once while announcements and staff notifications fail immediately. Error shapes, HTTP-status checks, logging, and future token/timeout changes can drift across seven copies. Some callers then persist a guard or finish their workflow, turning the transient error into a missed notification.

Remediation:

1. Extract a small public `SlackApiClient.post_message(channel, text, blocks, token=None)` from the existing `_api_call` behavior.
2. Keep message builders in their domains; move only transport, response parsing, redacted error reporting, timeouts, and retry policy.
3. Return a typed result/error so callers make explicit retry-versus-terminal decisions.
4. Route Slack delivery intents through the shared channel state where durable delivery is required.

Regression scenarios:

- Client unit tests for HTTP 429, `ok=false/ratelimited`, invalid JSON, network error, and non-200 response.
- Contract tests feed each existing message builder through the client and assert the same payload.
- Ensure logs never include bot tokens or full sensitive payloads.

Rollout/backfill/rollback: convert one caller family at a time; compare response/error metrics. Keep the old functions as adapters until all calls migrate.

Estimated reduction: 130–230 production lines with a larger reduction in duplicated behavior than file splitting would provide.

<a id="iam-15"></a>

### IAM-15 — Dormant Calendly capacity state is still actively maintained

Priority: P3  
Classification: maintainability observation and deletion opportunity  
Confidence: high  
Effort: small/medium, 2–4 engineering days including migrations and test cleanup

Evidence:

- `community/models/call_host.py:1-6` states that bookability is a simple operator-authored link and legacy capacity fields are dormant.
- `community/models/call_host.py:58-65` nevertheless stores `capacity` and `current_load`; admin hides them in `community/admin/call_host.py:12`.
- `community/services/calendly.py:208-395` maintains `BookedCall` plus atomic load increments/decrements across create, cancellation, staging, and promotion paths.
- Many `community/tests/test_calendly_webhook.py` assertions exist solely to prove a counter that cannot change member-visible availability.

Trigger and impact: every Calendly webhook pays locking, state-transition, repair, and testing complexity for a value operators cannot edit and the product does not read. A counter drift looks operationally meaningful despite having no product effect. `BookedCall` and unmatched-call records still have legitimate CRM/audit value and should remain.

Remediation:

1. Confirm no external reporting contract consumes the two fields.
2. Remove `capacity`, `current_load`, `_increment_load`, `_decrement_load`, and capacity-only locking/branches.
3. Retain idempotent `BookedCall`/`UnmatchedBookedCall` state and association logic.
4. Rewrite service documentation around call tracking rather than availability capacity.

Regression scenarios:

- Calendly service tests retain create/cancel ordering, duplicate webhook, unmatched promotion, member/host association, and signature coverage.
- Delete counter-only tests; do not replace them with tests of removed state.

Rollout/backfill/rollback: take a final read-only export if the counters are used informally, then drop fields in a normal migration. A rollback migration can re-add zeroed fields, but historical counters are not authoritative.

Estimated reduction: 20–50 production lines and roughly 80–160 test lines, plus two model fields and an entire invalid invariant.

### Remediation sequence and acceptance gates

### Phase 0 — Write four invariants before changing code

1. A primary login email changes only through the verified identity authority.
2. Community membership converges from current effective entitlement; queued action order cannot decide access.
3. Every external delivery has a durable business key and per-channel outcome; an ambiguous provider response is not silently called success or retried.
4. One Stripe event execution owns handler dispatch at a time; delivery attempts remain separate evidence.

Gate: PM, engineer, tester, and on-call agree on state diagrams, retryable versus terminal errors, operator-visible ambiguous states, and retention requirements. This prevents another collection of narrowly named flags.

### Phase 1 — Fix local correctness failures

Implement IAM-04, IAM-05, IAM-06, IAM-09, and IAM-11. These are bounded changes with direct regression tests and limited cross-module migration risk.

Gate:

- scanner GETs cause zero writes;
- concurrent acquisition returns no 500;
- progress apply/undo cannot overwrite newer human state;
- account merges serialize on both accounts;
- database constraints, not sequential tests, enforce singleton Slack guards.

### Phase 2 — Establish shared asynchronous authorities

Implement IAM-02, IAM-07, and IAM-08, followed by the generic delivery intent in IAM-01. Migrate reminders (IAM-10), announcements (IAM-12), and recording-ready email (IAM-13) onto the delivery primitive.

Gate:

- every task payload carries either a durable business key or expected state version;
- queue replay tests execute jobs in reverse and duplicate order;
- provider fault injection covers before-send, accepted response, timeout-after-send, and finalize failure;
- Studio/on-call surfaces expose pending, retryable failure, terminal failure, and ambiguous outcomes without secrets;
- no feature claims exactly-once delivery where the provider cannot prove it.

### Phase 3 — Consolidate transport and delete dead state

Implement IAM-14 and IAM-15. Remove compatibility adapters only after call-site search shows zero direct raw Slack transports and all deployments have crossed the migration boundary.

Gate:

- one Slack client owns transport policy;
- message builders stay in their product domains;
- Calendly call tracking passes without capacity fields or capacity-only tests;
- run `scripts/affected_tests.py` and exactly its emitted Django/Playwright scope, following `_docs/testing-guidelines.md` rather than widening to the full local suite.

Estimated net simplification across all phases: 380–750 production lines and 100–250 test lines. These are labeled estimates based on current duplicate branches and overlap heavily; they are a planning range, not an additive promise. The more important reduction is four authoritative state machines replacing many implicit check-then-act conventions.

### Coverage

| App | Surfaces reviewed | Result / boundary |
|---|---|---|
| `accounts/` | auth/register/reset, account preferences and keys, email resolution/change, merge, privacy/export/deletion, sessions, tier overrides | Findings IAM-03, IAM-05, IAM-09; previous token/merge-credential/privacy fixes reverified at code level |
| `payments/` | checkout/session fulfillment, webhook verify/dispatch/handlers, subscription transition/reconciliation, monthly grace, community hooks, models/constraints | Findings IAM-01, IAM-02, IAM-03, IAM-07, IAM-08 |
| `email_app/` | email service and logs, campaign path, newsletter verification/unsubscribe, SES identity/event correlation, tasks | Findings IAM-01 and IAM-04; campaign ledger recognized as the stronger positive pattern |
| `community/` | Slack community service, hook/removal tasks, staff notifications, membership refresh, Calendly tracking and call hosts | Findings IAM-02, IAM-14, IAM-15; scheduled removal revalidation recognized as a positive pattern |
| `crm/` | Slack ingestion, LLM parse/apply/undo, models/provenance, CRM notification helpers | Finding IAM-06 |
| `triggers/` | emission claims, subscription filtering/secrets, outbound URL pinning, durable jobs/leases, widget claims, Studio views | No reportable defect found in current active user-widget path; durable snapshot, SSRF controls, lease, and unique attempt design are positive patterns. Nullable system emissions are dormant because the only production caller is the authenticated widget claim. |
| `notifications/` | notification model/API/page isolation, audience selection, generic/series/comment fan-out, reminders, Slack announcements | Findings IAM-10, IAM-11, IAM-12, IAM-14 |
| `jobs/` | queue/schedule helper, cleanup/retention, session cleanup, override expiry, recording upload/reclaim, task naming | Finding IAM-13; bounded cleanup loops and producer-side recording locks are positive patterns |

### Complexity that is justified

- `accounts/services/privacy.py` is large because it enumerates a wide deletion/export data surface and preserves webhook correlation evidence. Its size alone is not a reason to introduce dynamic model walking or split business ownership blindly.
- `payments/services/webhook_handlers.py` carries real Stripe lifecycle distinctions: delayed checkout, stale-subscription rejection, refunds/disputes as review-only evidence, grace/recovery, and cancellation timing. Consolidate event claims and shared transitions, but keep explicit event semantics.
- `triggers/tasks.py:63-202` correctly snapshots destinations/secrets and uses a database lease plus unique `(job, attempt)` evidence. This complexity is justified by secure, retryable outbound webhooks and is a useful template.
- `community/services/slack.py:120-220` centralizes rate-limit and response behavior well. The simplification is to make it the shared transport, not remove its retry policy.
- Campaign delivery’s dedicated recipient ledger is justified for large fan-out. It should inform the generic delivery authority without forcing all transactional sends through campaign concepts.
- Effective-tier predicates shared across access, notifications, and scheduled community removal prevent a prior class of override regressions.

### Checks run

Read before audit:

```text
sed -n '1,260p' AGENTS.md
sed -n '1,720p' _docs/PROCESS.md
sed -n '1,2050p' _docs/testing-guidelines.md
```

Repository and static checks:

```text
git status --short
git diff --check
rg --files accounts payments email_app community crm triggers notifications jobs
find <owned-app> ... | wc -l
rg -n 'transaction.atomic|select_for_update|get_or_create|update_or_create|bulk_create|async_task|requests.post|dedupe_key' <owned-apps>
uv run python manage.py check
```

`manage.py check` completed successfully with zero silenced issues. It emitted the expected local warnings about the development fallback `SECRET_KEY` and database access during app initialization. `git diff --check` passed. The pre-existing dirty files remained `asl_cli/asl_cli/commands/events.py` and `asl_cli/tests/test_cli.py`; this audit did not touch them.

Isolated reproduction:

```text
uv run python - <<'PY'
import sqlite3
c = sqlite3.connect(':memory:')
c.execute('create table t(event_id integer, user_id integer null, interval text, unique(event_id,user_id,interval))')
c.execute("insert into t values (1,NULL,'24h_slack')")
c.execute("insert into t values (1,NULL,'24h_slack')")
print(c.execute('select count(*) from t').fetchone()[0])
PY
```

It printed `2`, reproducing IAM-11’s nullable-unique boundary without touching project data.

No Django or Playwright test suite was run because the audit made no product-code change and the assignment prohibited full suites. No live service, production API, provider, GitHub mutation, commit, or push was used.

### Limits

- This audit did not observe production data, queue timing, SES/Slack/Stripe responses, or deployment topology. Incidence and user counts are unknown.
- Concurrency findings were derived from transaction/schema boundaries; only the nullable-unique behavior received a local isolated reproduction. PostgreSQL transaction tests remain required before fixes ship.
- Necessary cross-app call tracing touched `events/`, `content/`, `studio/`, `voting/`, `comments/`, and `integrations/`, but those apps were not comprehensively audited here.
- Infrastructure, AWS IAM/SES/SNS/RDS/ECS, content repositories, templates, browser UI, accessibility, and performance/load behavior were outside this assignment.
- Existing tests were read selectively for claimed contracts and missing concurrency coverage; this is not a test-suite-quality audit.

## Operations, events, plans, and integrations

Audit date: 2026-09-07  
Audited commit: `1343497b0003093d4a1430e9784118210e36acfa`  
Scope: `api/`, `member_api/`, Python under `studio/`, `events/`, `plans/`, and `integrations/`

### Executive assessment

The main operational risk is inconsistent delivery-state machinery. Maven has a current PostgreSQL blocker, GitHub sync can lose a queued follow-up, and four plan notification workflows use three different claim conventions, two of which can remain terminal after a worker dies. Event writes are also orchestrated separately in the API and Studio, which permits stale transition snapshots and duplicate Zoom provisioning under concurrent requests.

The largest safe simplification is to keep the existing HTTP contracts while moving plan and event mutations behind shared command services. The current plan surfaces contain about 10,100 lines across eight primary view modules, and the event API/Studio pair contains two independent validation and lifecycle coordinators. Extracting shared commands first should remove approximately 1,200–2,100 lines without forcing a client migration. Two already-tracked cleanup issues, `#1536` and `#1537`, can remove smaller duplicate authorities immediately.

| Order | Finding | Priority | Classification | Existing issue |
|---:|---|---|---|---|
| 1 | OPS-01 Maven step claims fail on PostgreSQL | P0 | Confirmed defect | `#1574` |
| 2 | OPS-02 GitHub follow-up sync can be lost | P1 | Confirmed concurrency defect | New/reuse after issue search |
| 3 | OPS-03 Maven active-occurrence uniqueness is absent during R1 | P1 | Suspected runtime risk, explicitly staged | `#1266` rollout |
| 4 | OPS-04 Event edits use stale transition snapshots | P1 | Suspected runtime risk | New |
| 5 | OPS-05 Concurrent Zoom provisioning can orphan meetings | P1 | Suspected runtime risk | New |
| 6 | OPS-06 Plan-ready and partner-intro claims never expire | P1 | Confirmed recovery defect | New |
| 7 | OPS-07 Sprint delivery claims start in terminal states | P1 | Confirmed recovery defect | New |
| 8 | OPS-08 Plan/week uniqueness races return 500 | P1 | Confirmed API defect | New |
| 9 | OPS-09 Anonymous event account creation still races | P1 | Confirmed defect, cross-domain | Consider `#1519` |
| 10 | OPS-10 Two plan write stacks implement the same aggregate | P2 | Maintenance finding | New |
| 11 | OPS-11 API and Studio separately orchestrate event transitions | P2 | Maintenance finding | New |
| 12 | OPS-12 Zoom accepts unmatched webhooks without a terminal state | P2 | Confirmed observability defect | New |
| 13 | OPS-13 GitHub sync retains duplicate helper authorities | P2 | Maintenance finding | `#1536` |
| 14 | OPS-14 Worker task formatting has two active authorities | P2 | Maintenance finding | `#1537` |
| 15 | OPS-15 Settings writers have inconsistent atomicity and validation | P2 | Maintenance/risk finding | New |

### Coverage matrix

| Domain | Reviewed entry points and internals | Depth and limits |
|---|---|---|
| `api/` | event CRUD and series actions; plan creation/import/detail; week/checkpoint/item APIs; integration settings; worker serializers; OpenAPI declarations; queryset permission gates | Deep on event/plan writes, permissions, transition dispatch, and duplicate business rules. Sampled read-only CRM, ingestion, and worker list endpoints. Did not re-audit identity, payments, or messaging internals owned by other reviewers. |
| `member_api/` | event list/detail/register; complete plan read/write surface; API-key scope decorators as consumed; event/plan serializers and query budgets | Deep on ownership gates, pagination/prefetch behavior, registration and nested-plan mutation. Authentication/key implementation itself belongs to the identity review. |
| `studio/` Python | event edit/Zoom actions; sprint and plan-request flows; settings import/save; worker detail; service modules and URL surface | Deep on paired API/Studio operations. UI templates and client behavior were only traced to establish endpoint consumers. Campaigns, identity, and payment panels were left to their owners. |
| `events/` | public/member registration; event/series lifecycle services; cancellation/reschedule dispatch; Zoom meeting and recording workflows; relevant model constraints/tasks | Deep on mutation, registration, provider calls, retries, and idempotency. Email transport internals are cross-referenced to the messaging review rather than duplicated here. |
| `plans/` | aggregate model and uniqueness constraints; ready/partner/cadence/sprint-end delivery services; sprint/member views; progress/query helpers | Deep on write concurrency, delivery states, permissions, and duplicate API implementations. Rendering-only plan pages were sampled. |
| `integrations/` | GitHub webhook/queue/lock/orchestration and compatibility facade; Zoom webhook; Maven webhook ledger; settings registry/config; integration models/migrations | Deep on webhook claims, leases, schema constraints, retries, and configuration mutation. GitHub content-type parser correctness was sampled at dispatcher boundaries; content semantics belong to the content review. |

### Findings

<a id="ops-01"></a>

### OPS-01 — Maven step claims fail on PostgreSQL

| Attribute | Assessment |
|---|---|
| Priority / confidence | P0 / high |
| Classification | Confirmed defect |
| Location | `integrations/services/maven.py:575-585`, `_run_step`; `integrations/models/maven_enrollment_event.py:54-60`, nullable `user` foreign key |
| Trigger and impact | Every enrollment calls `_run_step`. It builds `select_for_update().select_related("user")`; because `user` is nullable, PostgreSQL creates a nullable outer join and rejects `FOR UPDATE` over that join. Maven enrollment therefore returns 500 before entitlement or onboarding steps run. This is already tracked as `#1574`. |
| Fix sequence | Remove `select_related("user")` from the locked query; claim only the `MavenEnrollmentEvent` row, commit, then fetch the user separately. Alternatively use PostgreSQL `select_for_update(of=("self",))`, with a backend-safe fallback. Preserve the current lease, attempt cap, and per-step finish logic. |
| Regression scenarios | PostgreSQL `TransactionTestCase`: an enrolled webhook reaches the override and notification steps; a nullable-user removal occurrence is claimable; two concurrent claims still execute a step once. SQLite coverage cannot prove the PostgreSQL SQL restriction. |
| Rollout / backfill / rollback | Ship independently before other Maven work. Replay failed occurrences after deployment with the existing replay operation. No schema backfill. Rollback is code-only. |
| Effort / reduction | S, 0.5–1 day; neutral LOC, removes a production blocker. |

<a id="ops-02"></a>

### OPS-02 — GitHub follow-up sync can be lost during lock release

| Attribute | Assessment |
|---|---|
| Priority / confidence | P1 / high |
| Classification | Confirmed concurrency defect |
| Location | `integrations/services/github_sync/orchestration.py:941-952`, `release_sync_lock`; caller at `:431-454`, `_release_lock_and_enqueue_follow_up`; webhook writer at `integrations/views/github_webhook.py:128-137` |
| Trigger and impact | The finishing worker refreshes the source and reads `sync_requested`, while a webhook concurrently sets it to true. If the webhook write lands after the refresh and before the worker save, the worker writes `sync_requested=False` and returns the stale false value. The new commit receives no follow-up job and remains unsynced until another manual or webhook trigger. |
| Fix sequence | Make consume-and-release one transaction with `select_for_update`; read and clear `sync_requested` while holding the row lock, then enqueue only from the returned state. Route the webhook through the shared `content_sync_queue` service and store one queued log/task identity per accepted follow-up. Keep the atomic `acquire_sync_lock` update. |
| Regression scenarios | PostgreSQL `TransactionTestCase` with barriers around release and webhook request; assert the flag is either consumed into one queued job or remains true, never silently false with no job. Add sequential cases for no follow-up and stale-lock reclamation. |
| Rollout / backfill / rollback | Before deploy, inspect sources with recent webhook timestamps but an older successful commit and enqueue them once. Code rollback is safe; do not revert any corrective sync already queued. |
| Effort / reduction | M, 2–3 days; approximately 40–70 lines removed by deleting webhook-specific queue bookkeeping. |

<a id="ops-03"></a>

### OPS-03 — Maven active-occurrence uniqueness is absent during R1

| Attribute | Assessment |
|---|---|
| Priority / confidence | P1 / high |
| Classification | Suspected runtime risk with a confirmed schema gap; the gap is explicitly accepted by staged rollout `#1266` |
| Location | `integrations/services/maven.py:275-310`, `_handle_enrolled`; `integrations/models/maven_enrollment_event.py:124-127`, `Meta`; migration `integrations/migrations/0024_mavenenrollmentevent_cohort_key_and_more.py:8-12,30-45`; `website/release_phase.py:1-11` |
| Trigger and impact | Two concurrent deliveries for the same email/course/cohort can both miss the active row and create different random `dedupe_key` rows. The service catches `IntegrityError` as if a partial unique constraint existed, but no constraint exists. Each occurrence owns a separate step ledger, so duplicate entitlement audit, Slack, notification, and welcome actions are possible. The test at `integrations/tests/test_maven_corrections.py:203-230` explicitly permits two active rows while `R1_EXPAND_COMPATIBILITY=True`. |
| Fix sequence | Prepare the intended R2 under `#1266`: inventory and reconcile duplicate active `identity_hash` rows; backfill safe legacy identities; add a partial unique constraint for non-empty `identity_hash` with `lifecycle="active"`; then keep the `IntegrityError` convergence path. Do not enable the constraint or retire R1 compatibility until `#1266` confirms that all overlapping web/worker versions write compatible identities and that its rollback image can run safely with the constraint present. |
| Regression scenarios | PostgreSQL concurrent webhook test with a barrier at occurrence creation; exactly one active occurrence and one execution of each visible step. Migration test covers duplicate reconciliation and repeat-safe application. Keep removal/re-enrollment as two lifecycle occurrences. |
| Rollout / backfill / rollback | This audit does not authorize an immediate R1-to-R2 switch. Follow `#1266` compatibility and rollback gates: emit a duplicate report, rehearse reconciliation, prove every mixed-version writer and rollback image is constraint-compatible, deploy/backfill in the order specified there, and only then enable R2/retire R1. If that proof fails, leave R1 enabled and the constraint undeployed while reducing the overlap window. |
| Effort / reduction | M/L, 3–5 days including data reconciliation; little LOC reduction, major invariant gain. |

<a id="ops-04"></a>

### OPS-04 — API and Studio event edits use stale transition snapshots

| Attribute | Assessment |
|---|---|
| Priority / confidence | P1 / high |
| Classification | Suspected runtime risk |
| Location | `api/views/events.py:1269-1275,1324-1351`, `event_detail`; `studio/views/events.py:1001-1011,1096-1110`, `event_edit` |
| Trigger and impact | Concurrent API and Studio edits load independent event objects and independent `old_event` snapshots without a row lock or version check. Both can save, and both then derive cancellation/reschedule, Zoom sync, series enrollment, and banner work from stale before/after pairs. Effects include lost field updates, duplicate sequence bumps/messages, or a Zoom update based on a state that another request already replaced. |
| Fix sequence | Introduce `mutate_event(event_id, patch, actor, source, expected_version=None)`. Lock the event, validate and apply mutable fields/hosts, compute one transition record, commit, then dispatch lifecycle services from that record. Add an optimistic version/`updated_at` precondition for remote API callers and return 409 on stale edits. Keep provider calls outside the row-lock transaction. |
| Regression scenarios | PostgreSQL transaction tests for simultaneous reschedule/cancel and API/Studio edits; assert one coherent final row, monotonic ICS sequence, and lifecycle enqueue counts derived from the winning transition. View tests cover 409 mapping; no browser test is needed for the command invariant. |
| Rollout / backfill / rollback | Add the command behind both adapters before deleting old code. Log transition IDs and source during rollout. No backfill. Roll back adapters to the old paths if provider dispatch metrics regress. |
| Effort / reduction | L, 5–8 days; approximately 500–900 lines removed across paired event writers and validators. |

<a id="ops-05"></a>

### OPS-05 — Concurrent Zoom provisioning can orphan provider meetings

| Attribute | Assessment |
|---|---|
| Priority / confidence | P1 / high |
| Classification | Suspected runtime risk at a non-idempotent provider boundary |
| Location | `api/views/events.py:731-755`, `_maybe_create_zoom_meeting`; `studio/views/events.py:1476-1487`, `event_create_zoom`; `events/tasks/create_series_zoom_meetings.py:38-52,80-166` |
| Trigger and impact | Two requests/jobs both observe an empty `zoom_meeting_id`, both create meetings at Zoom, and both save. The last save wins and the first Zoom meeting becomes an untracked live meeting. Duplicate series jobs expose the same window for every occurrence and overwrite the last-run summary. |
| Fix sequence | Add a provisioning state with claim token, lease timestamp, attempts, and provider request correlation. Compare-and-set the claim before the network call, create once, then finalize only if the token still owns the claim. Recover expired claims and reconcile known provider responses. Route API, Studio, synced-event, and series creation through this one service. |
| Regression scenarios | Transaction/concurrency test with a blocking mocked Zoom client: two callers yield one provider call and one stored meeting. Test worker death after claim and stale-lease recovery, plus a late response that cannot overwrite a newer owner. Series test starts duplicate jobs. |
| Rollout / backfill / rollback | Add nullable claim fields first. Reconcile events with missing IDs against Zoom where possible; report orphans for operator deletion. Feature-flag the shared provisioner and keep old read fields for rollback. |
| Effort / reduction | L, 5–8 days; approximately 80–140 lines removed after consolidating four callers. |

<a id="ops-06"></a>

### OPS-06 — Plan-ready and partner-intro `sending` claims never expire

| Attribute | Assessment |
|---|---|
| Priority / confidence | P1 / high |
| Classification | Confirmed recovery defect |
| Location | `plans/services/plan_ready_emails.py:269-280,353-385`; `plans/services/partner_intro_emails.py:276-291,343-376`; state models at `plans/models.py:865-927,1025-1073` |
| Trigger and impact | A process can die after committing the `sending` claim and before marking sent/failed. Every future call treats `sending` as permanently in progress, so the member never receives the plan-ready or partner-intro delivery. `updated_at` exists but neither claimant uses it as a lease. Existing tests assert that a fresh `sending` row is skipped but do not distinguish a stale one. |
| Fix sequence | Add a small plans-owned claim function with token, `claimed_at`, lease TTL, attempts, sent terminal state, and stale-state classification; use it only for these two services. A stale claim is `ambiguous`, not automatically retryable, because the provider may have accepted the email before the worker died. Reuse the message delivery idempotency policy owned by the email review rather than creating a generic workflow framework. |
| Regression scenarios | Freeze time: fresh claims do not resend; expired claims are reclaimed by one of two contenders; sent claims never reopen; provider failure moves to retryable failed; simulated exception after claim is recoverable. Use service-level Django tests. |
| Rollout / backfill / rollback | Report every `sending` row older than the chosen TTL as ambiguous. Absence of a linked `EmailLog` does not prove that transport never started. Reconcile provider records, task logs, and operator evidence; retry only rows for which transport is proven not to have started. Leave unresolved rows held for manual disposition or mark them with an explicit unknown outcome. Add fields before switching claim logic. Rollback leaves new fields unused. |
| Effort / reduction | M, 3–4 days; approximately 40–80 lines removed through the small shared claim function. |

<a id="ops-07"></a>

### OPS-07 — Sprint delivery claims are initially written as terminal states

| Attribute | Assessment |
|---|---|
| Priority / confidence | P1 / high |
| Classification | Confirmed recovery defect |
| Location | `plans/tasks/sprint_end.py:193-255,258-274`, `_deliver_plan_recap` / `_claim_delivery`; `plans/services/sprint_cadence.py:118-145`, `_create_log_once` / `_finalize_log`; defaults at `plans/models.py:1083-1147` |
| Trigger and impact | Sprint-end creates the unique claim with `status="sent"` before creating a notification or sending email. A crash leaves `sent_at=NULL` but permanently suppresses retries. Cadence uses `status="skipped"` as its pre-delivery claim and has the same crash window. The audit row therefore claims a terminal outcome that never happened. |
| Fix sequence | Add `pending/sending/ambiguous` states and the same small plans-owned claim function as OPS-06; claim in-progress, create the in-app notification transactionally, send provider mail, then finalize. Define whether a proven pre-transport `email_failed` state retries and cap attempts. Keep the helper scoped to these plan deliveries rather than introducing a generic workflow framework. |
| Regression scenarios | Service tests inject a crash after claim, after notification, and after email mock; rerun converges to the documented state without duplicate notification. Assert terminal `sent` always has `sent_at` and owned evidence. Add concurrent claimant test on PostgreSQL. |
| Rollout / backfill / rollback | Classify sprint-end rows where `status="sent" AND sent_at IS NULL` and cadence rows with placeholder terminal status but incomplete evidence as ambiguous. A provider-accepted send followed by a log crash can leave no linked evidence, so do not automatically resend. Reconcile provider/task records and retry only when transport is proven not to have started; hold unresolved rows for manual disposition. Add states in a forward-compatible migration. |
| Effort / reduction | M/L, 4–6 days; approximately 80–150 lines removed when combined with OPS-06. |

<a id="ops-08"></a>

### OPS-08 — Plan and week uniqueness races escape as 500 responses

| Attribute | Assessment |
|---|---|
| Priority / confidence | P1 / high |
| Classification | Confirmed API defect |
| Location | `api/views/plans.py:434-466,501-518,1193-1243`, plan create; constraint `plans/models.py:783-790`; `member_api/views/plans.py:902-942,1005-1057`, week create/update; constraint `plans/models.py:1180-1187`; correct older pattern at `api/views/weeks.py:191-204` |
| Trigger and impact | Concurrent creates both pass `.exists()`. The loser hits the database uniqueness constraint inside `atomic` and raises an uncaught `IntegrityError`, despite documented 409/422 duplicate responses. Member week PATCH can similarly collide with another renumber operation. Ordinary retries or two clients can produce a 500 for a valid conflict. |
| Fix sequence | Treat the database constraint as authoritative. Keep optional prechecks for friendly latency, catch `IntegrityError` outside the atomic block, inspect/map the known constraint, and return one consistent 409 conflict across both plan APIs. Extract a shared create/renumber command so the old and member adapters cannot drift again. |
| Regression scenarios | API contract tests patch/save the owned command to raise the named uniqueness error and assert 409 plus no partial children. PostgreSQL `TransactionTestCase` proves two real concurrent requests produce one success and one conflict. Do not add a test that merely re-tests Django uniqueness. |
| Rollout / backfill / rollback | No backfill. Add error telemetry by constraint name before consolidation. Response-code standardization may require a short compatibility note for clients currently expecting member 422. |
| Effort / reduction | S/M, 1–3 days; approximately 30–60 lines removed through shared conflict mapping. |

<a id="ops-09"></a>

### OPS-09 — Anonymous event registration can race while creating the user

| Attribute | Assessment |
|---|---|
| Priority / confidence | P1 / high |
| Classification | Confirmed defect; event/identity boundary |
| Location | `events/views/api.py:121-145`, `_create_unverified_subscriber`; `:352-355,438-453`, anonymous registration branch; unique email at `accounts/models/user.py:98` |
| Trigger and impact | Two first-time submissions for the same email both see no user. One creates the unique email row; the other raises `IntegrityError` before the registration convergence logic. The registration-row race fixed under prior finding `M-04/#1518` is still resolved, but account creation remains a distinct window and overlaps the open signup race `#1519`. |
| Fix sequence | Move get-or-create subscriber resolution into an identity-owned service that catches unique-email conflicts and reloads the canonical user. Then call the existing idempotent registration helper. Ensure only the request that creates the registration sends calendar mail and only the user-creation winner sends the account-claim mail, or use a delivery claim. |
| Regression scenarios | PostgreSQL concurrent anonymous POSTs: one user, one registration, deterministic success/already response, bounded emails. Existing-user and verified-user cases must preserve tier/password/preferences. The concurrency invariant belongs at service/API level. |
| Rollout / backfill / rollback | Reuse/extend `#1519` rather than creating competing identity services. No backfill; inspect recent 500s for duplicate email violations. Rollback is code-only. |
| Effort / reduction | M, 2–3 days shared with identity work; approximately 20–40 lines removed from the event view. |

<a id="ops-10"></a>

### OPS-10 — Two plan write stacks implement the same aggregate

| Attribute | Assessment |
|---|---|
| Priority / confidence | P2 / high |
| Classification | Maintenance finding with demonstrated behavioral drift |
| Location | `api/views/plans.py` (2,539 lines), `api/views/weeks.py` (424), `api/views/checkpoints.py` (602), `api/views/plan_items.py` (652), `member_api/views/plans.py` (1,945); permission adapters at `api/views/_permissions.py:41-63` and `member_api/views/plans.py:99-169` |
| Trigger and impact | The browser/Studio editor still consumes `/api/weeks`, `/api/checkpoints`, and item routes, while scoped clients consume `/member-api/v1/plans`. Both stacks validate and mutate the same `Plan` aggregate, already disagreeing on duplicate status codes and constraint handling (OPS-08). Every new field or invariant must be implemented and tested twice. |
| Fix sequence | Keep both URL contracts. Extract commands for plan patch/create, week create/renumber/delete, checkpoint move/toggle, singleton note, and ordered item CRUD. Let adapters own authentication, error-envelope translation, and serialization only. After consumers migrate, evaluate retiring member-capable routes from the broad `/api` namespace; do not remove Studio browser endpoints first. Move large OpenAPI dictionaries beside schema declarations so runtime views expose their control flow. |
| Regression scenarios | Command tests cover each invariant once. Thin contract tests per adapter cover permission, envelope, and status translation. Keep Playwright only for drag/drop and JavaScript rollback flows. Add a parity table test over representative valid/invalid commands rather than duplicating every behavior. |
| Rollout / backfill / rollback | Migrate one child type at a time behind unchanged endpoints. Compare serialized results in shadow tests. No data backfill. Old adapter implementation remains a straightforward rollback until each slice stabilizes. |
| Effort / reduction | XL, 2–3 weeks in slices; estimated 700–1,200 production lines and substantial duplicate tests removed. |

<a id="ops-11"></a>

### OPS-11 — API and Studio separately validate and orchestrate event transitions

| Attribute | Assessment |
|---|---|
| Priority / confidence | P2 / high |
| Classification | Maintenance finding that contributes to OPS-04/OPS-05 |
| Location | `api/views/events.py:473-643`, `_collect_event_values` (McCabe 47), and `:1261-1359`; `studio/views/events.py:1001-1210`, `event_edit`; `api/views/event_series.py:193+`; Studio event-series actions in `studio/views/events.py`; shared lifecycle services under `events/services/` |
| Trigger and impact | Choice/date/series/host validation, old-state capture, persistence, and lifecycle dispatch remain in each view. Shared services exist for calendar, Zoom, and occurrence publication, but callers decide ordering independently. Fixes such as synced-field policy, default duration, or cancellation behavior can land on one surface only. Complexity metrics identify the hotspots; the actual defect evidence is the stale orchestration in OPS-04. |
| Fix sequence | Define typed event input and transition result objects; move validation and persistence to event commands; retain API JSON and Studio form adapters for coercion/error presentation. Fold book/sprint event-series resolver duplication into one domain resolver. Keep provider-specific lifecycle services separate and call them from a single post-commit dispatcher. |
| Regression scenarios | Domain table tests cover create/update combinations and transitions. One API test and one Studio test per adapter prove mapping. Existing browser tests remain authoritative for form JavaScript. Add parity tests for equivalent API/form inputs. |
| Rollout / backfill / rollback | Extract validation first with no behavior change, then mutation/dispatch. Instrument source and transition. No backfill. Each adapter can roll back independently while the shared service remains. |
| Effort / reduction | L/XL, 2–3 weeks; approximately 500–900 lines removed, overlapping OPS-04. |

<a id="ops-12"></a>

### OPS-12 — Zoom accepts unmatched recording webhooks without terminal state

| Attribute | Assessment |
|---|---|
| Priority / confidence | P2 / high |
| Classification | Confirmed observability/recovery defect |
| Location | `integrations/views/zoom_webhook.py:83-102`, webhook response; `:121-123,144-153,190-191`, `_handle_recording_completed` |
| Trigger and impact | A signed `recording.completed` webhook with a missing meeting ID or no matching event returns normally. The endpoint responds 200, but the `WebhookLog` remains `processed=False` without a terminal ignored reason. Zoom is told not to retry, and there is no deferred matcher, so a webhook that arrives before the event stores its meeting ID is stranded and unprocessed logs accumulate. |
| Fix sequence | Model explicit `processed`, `ignored_terminal`, and `retryable_unmatched` outcomes with reason/attempt metadata. If ordering races are expected, schedule bounded replay by meeting ID; otherwise mark terminal ignored in the same transaction. Add a provider delivery key when Zoom supplies one, or hash a stable event identity for dedupe. |
| Regression scenarios | Webhook tests for missing ID, unknown meeting, later event match/replay, duplicate delivery, and known event. Assert both response and durable terminal/retry state. ORM queue writes may remain inside the surrounding atomic block because `Q_CLUSTER['orm']='default'`; they roll back together. |
| Rollout / backfill / rollback | Classify existing unprocessed Zoom logs by meeting ID and event match, replay matchable recent rows, mark old unmatchable rows ignored with an audit reason. Retain raw payload according to existing retention policy. |
| Effort / reduction | M, 2–4 days; neutral LOC, removes ambiguous operational backlog. |

<a id="ops-13"></a>

### OPS-13 — GitHub sync retains duplicate helper authorities

| Attribute | Assessment |
|---|---|
| Priority / confidence | P2 / high |
| Classification | Maintenance finding, revalidated prior finding `M-23/#1536` |
| Location | Duplicate implementations in `integrations/services/github_sync/repo.py:57-86` and `integrations/services/github_sync/parsing.py:18-45`; compatibility exports in `integrations/services/github.py:1-6,97-108` |
| Trigger and impact | `_extract_readme_title`, `_derive_readme_content_id`, and `_derive_workshop_page_content_id` have multiple implementations. Current production dispatchers use the parsing versions, while the facade and legacy tests preserve private imports. A future fix can change one copy and leave another caller with different content identity, creating duplicate synced records. |
| Fix sequence | Complete `#1536`: select the parsing module as authority, import it where needed, migrate tests away from the facade’s private symbols, delete repo copies, then narrow the compatibility facade to true public/task-string exports. Add an import-boundary check rather than source-copy tests. |
| Regression scenarios | Focused sync tests prove stable IDs and README title behavior through the public sync path. Existing records retain identical UUIDv5 outputs. No browser coverage. |
| Rollout / backfill / rollback | No backfill because formulas are byte-equivalent today. Compare outputs over representative paths before deletion. Revert is code-only. |
| Effort / reduction | S, 0.5–1 day; approximately 25–45 production lines plus redundant tests removed. |

<a id="ops-14"></a>

### OPS-14 — Worker task formatting has two active authorities

| Attribute | Assessment |
|---|---|
| Priority / confidence | P2 / high |
| Classification | Maintenance finding, revalidated prior finding `M-24/#1537` |
| Location | `api/serializers/worker.py:33-74`, `_format_task_value` / `_looks_like_traceback`; identical logic at `studio/views/worker.py:389-415`; Studio already imports `extract_error_summary` from the API serializer |
| Trigger and impact | API and Studio can format the same task result differently after any one-sided change. The current module comments acknowledge mirroring while another formatter (`extract_error_summary`) is already shared. This is a small but active duplicate authority in an operator debugging surface. |
| Fix sequence | Complete `#1537`: expose neutral worker-presentation helpers from a non-HTTP module under `jobs` or a shared service; import them from both API serializer and Studio; keep API wire-shape serialization in `api`. |
| Regression scenarios | One unit table for `None`, strings, nested objects, tracebacks, and defensive repr; one API shape test and one Studio context smoke test. Delete mirror tests. |
| Rollout / backfill / rollback | No data or operational rollout. Code-only rollback. |
| Effort / reduction | S, under 1 day; approximately 20–35 production lines and duplicate tests removed. |

<a id="ops-15"></a>

### OPS-15 — Integration-setting writers have inconsistent atomicity and validation

| Attribute | Assessment |
|---|---|
| Priority / confidence | P2 / medium-high |
| Classification | Maintenance finding with a low-frequency partial-write risk |
| Location | atomic API batch at `api/views/integration_settings.py:314-416`; non-atomic Studio group writer at `studio/views/settings.py:414-502`; non-atomic import at `studio/services/settings_io.py:110-194`; registry mutation at `integrations/settings_registry.py:1392-1396` |
| Trigger and impact | The API validates then applies all rows in one transaction. Studio group save validates first but writes each row without a transaction; settings import also updates integration rows and OAuth rows incrementally. A database error midway leaves a partial configuration even though the UI describes group/batch behavior. Type normalization is reimplemented, and import can bypass Studio URL/integer/email validation. Cache invalidation is correctly called by both views, so the earlier audit suspicion of stale import cache is ruled out. |
| Fix sequence | Create a registry-backed `validate_setting_value` and `apply_setting_updates` service. Perform group/import writes inside `transaction.atomic`, including `SocialApp` membership changes, and call `clear_config_cache` with `transaction.on_commit`. Keep the registry as the single metadata authority and stop mutating its dictionaries at import time by constructing typed definitions once. |
| Regression scenarios | Service tests inject a failure on the second update and assert zero persisted changes and no cache stamp; commit success publishes one stamp; import rejects invalid typed values before writes; API and Studio adapters yield the same stored values. |
| Rollout / backfill / rollback | No normal backfill. Export current settings before deployment as an operator rollback artifact. Existing DB values remain readable; code rollback is safe. |
| Effort / reduction | M, 2–4 days; approximately 60–120 lines removed from three writers. |

### Sequenced remediation plan

| Wave | Work | Exit condition |
|---:|---|---|
| 0 | Fix `#1574` / OPS-01 and replay affected Maven occurrences. | PostgreSQL webhook test green; failed occurrences replayed. |
| 1 | Fix OPS-02, prepare OPS-03 reconciliation/constraint work within `#1266`, and add recovery states for OPS-06/OPS-07. | Sync follow-up cannot disappear; stale plan claims become explicit ambiguous states; Maven R2 proceeds only after `#1266` proves mixed-version and rollback readiness. |
| 2 | Add the shared Zoom provisioner (OPS-05), unmatched webhook outcomes (OPS-12), and uniqueness conflict mapping (OPS-08/OPS-09). | Concurrent provider/database boundary tests converge to one owned result. |
| 3 | Extract event commands (OPS-04/OPS-11) while retaining URL/form contracts. | API and Studio call the same transition coordinator and parity tests pass. |
| 4 | Extract plan aggregate commands (OPS-10), then finish small tracked deletions OPS-13/OPS-14 and settings unification OPS-15. | One authoritative mutation implementation per plan child type; `#1536/#1537` closed. |

### Complexity worth retaining

- `api/views/_permissions.py` uses queryset-layer plan gates. Keeping authorization in the query prevents serializers or new endpoints from accidentally exposing other members’ rows.
- `member_api` pagination, progress annotations, and detail prefetches are the correct fix for prior issues `#1507` and `#1520`; do not collapse them into serializer-time relation access.
- `events/services/registration.py` and series registration helpers centralize membership-safe `get_or_create` behavior. The account-resolution race in OPS-09 should feed into these helpers, not replace them.
- Separate lifecycle services for calendar updates, Zoom synchronization, occurrence publication, and recording upload are valuable. The simplification is one transition coordinator, while provider modules remain isolated.
- Recording upload enqueue inside `transaction.atomic` is intentional with the configured Django-Q ORM broker (`website/settings.py:711-720`): the queue row and event lease share the default database transaction. Moving that call to `on_commit` without a durable pending state would create a different crash window.
- Maven’s per-step ledger, attempt cap, lease, and successful-step suppression are useful complexity. OPS-01 and OPS-03 repair its SQL/invariant boundaries; they do not justify replacing the ledger with one coarse webhook flag.
- `integrations/settings_registry.py` is large because it carries operator metadata and documentation for many integrations. Preserve one registry; simplify its construction and make all writers consume it.
- GitHub checkout/path safety and content-specific dispatchers should remain separated. The deletion target is duplicate helpers and queue orchestration, not content-type boundaries.

### Prior-audit revalidation

| Prior item | Current conclusion |
|---|---|
| `H-08/#1507`, member event serialization N+1 | Closed and verified structurally: member event queries now annotate registration state and prefetch hosts/instructors. Do not reopen from stale audit text. |
| `M-04/#1518`, registration check-then-create | The event-registration row now converges through the shared helper and catches `IntegrityError`. OPS-09 is the remaining user-account creation race, a distinct boundary that should coordinate with `#1519`. |
| `M-06/#1520`, unbounded member plans | Closed and verified: list pagination is fixed at 20, detail relations are prefetched, and query-budget tests exist. |
| `M-23/#1536` | Still present; revalidated as OPS-13. |
| `M-24/#1537` | Still present; revalidated as OPS-14. |
| Suspected Zoom queue/transaction gap | Ruled out: `Q_CLUSTER['orm']='default'`, so the `OrmQ` enqueue participates in the event transaction and is invisible until commit. |
| Suspected settings-import cache gap | Ruled out at the HTTP boundary: `studio/views/settings.py:601` calls `clear_config_cache` after import. OPS-15 is about atomicity and validation parity. |

### Checks actually performed

| Check | Result |
|---|---|
| Read repository process and testing guidance | Read `AGENTS.md`, `_docs/PROCESS.md`, and `_docs/testing-guidelines.md`; proposed tests follow affected-scope and correct-layer rules. |
| Repository state | Audited `1343497b0003093d4a1430e9784118210e36acfa`; preserved the pre-existing dirty `asl_cli/asl_cli/commands/events.py` and `asl_cli/tests/test_cli.py`. |
| Static call/constraint tracing | Traced each finding from entry point through transaction, model constraint, external call, and existing test references using `rg`, `nl`, and targeted file reads. |
| Complexity evidence | Used `.tmp/code-audit-2026-09-07/metrics.json` and `ruff-advisory.json` only to locate hotspots; no finding is based on a score alone. |
| Prior issue revalidation | Read tracker `#1546` snapshot and the recent issue inventory; retained open `#1536/#1537/#1574`, and explicitly ruled resolved prior claims out. |
| Broker behavior check | Verified `website/settings.py:719` configures Django-Q’s ORM broker on the default DB; this ruled out the apparent Zoom transaction/enqueue defect. |
| Tests executed | None. No code was changed, and concurrency behavior requiring PostgreSQL cannot be proved by the local SQLite test path. The commands above are proposed regression checks, not claimed results. |

### Proposed validation commands for implementation issues

Run only labels selected by the eventual diff. Likely focused inner loops are:

```bash
uv run python manage.py test integrations.tests.test_maven_corrections --parallel 4
uv run python manage.py test integrations.tests.test_zoom integrations.tests.test_github_sync --parallel 4
uv run python manage.py test plans.tests.test_plan_ready_action plans.tests.test_partner_intro_emails plans.tests.test_sprint_end_recaps plans.tests.test_sprint_cadence --parallel 4
uv run python manage.py test api.tests.test_events api.tests.test_plans api.tests.test_weeks member_api.tests.test_plans_write_api --parallel 4
uv run python scripts/affected_tests.py
```

PostgreSQL-only race and `FOR UPDATE` cases should run in the repository’s PostgreSQL migration/concurrency lane. Do not substitute the full local suite; CI remains responsible for exhaustive coverage.

## Project structure, CLI, tests, and delivery tooling

### Scope and evidence

Reviewed project configuration, dependency manifests, Docker inputs, Make targets,
CI workflow definitions, the Django test runner, representative test-policy
implementations, CLI transport/formatting and representative commands. Repository-wide
AST and size scans supplemented those reads. This is a static audit with three small,
offline CLI reproductions; no application suite or production checks ran.

Priorities: `P1` warrants an early corrective issue, `P2` is planned correctness or
maintenance work, and `P3` needs measurement or a lifecycle decision before implementation.
Effort is estimated engineering time including focused checks, excluding pipeline wait.

<a id="sys-01"></a>

### SYS-01 — Staff CLI sends its credential to an absolute foreign URL

| Field | Assessment |
|---|---|
| Priority / evidence | `P1`; reproduced offline |
| Source | `asl_cli/asl_cli/client.py:37`, `:47`, `:61`; `asl_cli/asl_cli/commands/raw.py:19` |
| Trigger | Pass an absolute URL for another origin as the path, including through `asl raw` |
| Impact | The initial request carries the configured staff `Authorization` header to that origin |
| Effort / reduction | 0.5–1 day; small net addition, justified by credential isolation |

`base_url` does not constrain an absolute request URL. A `MockTransport` experiment
used a synthetic token, configured `https://platform.example`, and requested
`https://unrelated.example/api/users`. The transport observed the unrelated host
and `credential_attached=True`. No network request or real credential was used.
This is an operator-client boundary defect; the audit did not demonstrate a remotely
triggerable website exploit or an actual credential disclosure.

Remediation:

1. Validate paths in the single `Client.request` boundary before creating a request.
2. Prefer slash-prefixed relative API paths. Reject scheme, authority, credentials,
   fragments and scheme-relative URLs. If same-origin absolute URLs are retained for
   compatibility, normalize and compare scheme, hostname and effective port explicitly.
3. Keep credential routing independent from raw-output mode. Review redirect behavior
   separately; never manually reattach credentials after a cross-origin redirect.
4. Give a concise Click error without printing the credential.

Acceptance checks:

- [ ] An offline `httpx.MockTransport` test proves relative API paths reach the configured origin.
- [ ] Foreign hosts, alternate ports, HTTPS-to-HTTP changes and `//host/path` fail before transport invocation.
- [ ] Ordinary JSON and raw response modes retain their behavior.
- [ ] A cross-origin redirect cannot carry staff credentials to its target.

Rollout: CLI-only change; no data migration. Revert the command change if necessary,
but keep the transport origin restriction. Coordinate with the existing dirty CLI work.

<a id="sys-02"></a>

### SYS-02 — CLI path segments are interpolated without URL encoding

| Field | Assessment |
|---|---|
| Priority / evidence | `P2`; reproduced offline |
| Source | `asl_cli/asl_cli/commands/users.py:39`, `:54`, `:65`, `:74` |
| Trigger | An email or tag contains a URL-reserved character such as `#`, `?` or `/` |
| Impact | A command can request a truncated or different route rather than the requested record |
| Effort / reduction | 0.5–1 day; replace repeated interpolation with one small path-segment helper |

With `MockTransport`, `/api/users/person#tag@example.com` reached the transport as
`/api/users/person`; the fragment was discarded. Email local parts can contain `#`.
This reproduction establishes transport truncation, not modification of another real user.

Remediation: encode each data segment with a single shared helper, leaving route
separators intact. Review email, alias, tag, slug and opaque-ID command arguments.
Do not apply percent encoding to an already assembled URL or double-encode segments.

Acceptance checks:

- [ ] Parameterized offline CLI tests preserve `+`, `#`, `?`, `%`, spaces and Unicode as data.
- [ ] Tags containing `/` remain one segment or are rejected according to the server contract.
- [ ] GET, PATCH and DELETE commands address the same encoded identity.
- [ ] Ordinary email commands and the deliberately raw path command preserve documented semantics.

Rollout: no migration; smoke test against a fake API transport, not production user records.

<a id="sys-03"></a>

### SYS-03 — Docker copies scratch data and development machinery into its build context

| Field | Assessment |
|---|---|
| Priority / evidence | `P2`; confirmed configuration gap, image contents not built in this audit |
| Source | `Dockerfile:10`, `:31`; `.dockerignore:1` |
| Trigger | Build from a working checkout containing `.tmp/`, test artifacts or development-only files |
| Impact | Larger build context/image and possible inclusion of operator scratch artifacts |
| Effort / reduction | 1–2 days; image/context reduction must be measured, not counted as source deletion |

Both stages use `COPY . .`. `.dockerignore` excludes `.env`, `.git`, `.claude`,
Playwright tests and most docs, but does not exclude `.tmp`, `.agents`, app tests,
top-level tests or many test caches/artifacts. `.tmp` occupied about 770 MiB at the
point of inspection. That is local disk usage, not a measured image-size increase:
some nested files may match existing exclusions. No scratch contents or secrets were read.

Remediation:

1. Exclude scratch/caches and deliberately identify the runtime files actually needed.
2. Keep app migrations, management commands, email templates, static assets and public
   API documentation. Preserve `docs/member-api` and any skill files served by runtime routes.
3. Review `scripts/entrypoint_init.py` imports before excluding operational scripts.
4. Consider explicit runtime copies only if they simplify the manifest; do not create a
   brittle per-file allowlist for every application module.

Acceptance checks:

- [ ] A clean disposable build excludes a synthetic `.tmp/audit-sentinel.txt` and test caches.
- [ ] The same disposable image and deployment-shaped entrypoint reach web, worker and predeploy readiness boundaries.
- [ ] Release-phase and mixed-version worker/task import compatibility pass; predeploy failure still blocks rollout.
- [ ] Image inventory retains supported task paths, migrations, email templates, entrypoint management commands and static assets.
- [ ] Compare removed image files with the prior image; narrow exclusions incrementally from scratch/caches before broad tree exclusions.
- [ ] Staff/member OpenAPI and member usage documentation routes still return their intended content.
- [ ] Record before/after build-context bytes, compressed image bytes and cold build duration.

Rollout: dev image first, followed by the normal deployment pipeline. Keep the previous
image digest for rollback; this change must not alter the database or boot contract.

<a id="sys-04"></a>

### SYS-04 — Runtime imports rely on undeclared transitive dependencies

| Field | Assessment |
|---|---|
| Priority / evidence | `P2`; confirmed manifest inconsistency |
| Source | `pyproject.toml:6`; `integrations/services/zoom.py:18`; `community/services/slack.py:23`; `integrations/services/github_sync/parsing.py:8` |
| Trigger | A future dependency update removes a currently transitive package |
| Impact | Application imports fail although the direct-dependency lock still resolves |
| Effort / reduction | 0.5 day; dependency ownership becomes explicit, no LOC reduction target |

Application code imports `requests` and `yaml`, but the root dependency list does not
declare `requests` or `PyYAML`. Offline `uv tree --locked --invert` shows `requests`
arriving through Stripe and Logfire's exporter (and test tooling), and PyYAML through
`python-frontmatter`. These packages are installed today; this is not a current missing-import bug.

Remediation: declare packages directly imported by runtime code; audit other top-level
imports the same way. Keep CLI-only dependencies in its own package and development tools
in the dev group. Do not replace a working HTTP library just to reduce dependency count.

Acceptance checks:

- [ ] A clean `uv sync --locked --no-dev` environment imports owned runtime entry points.
- [ ] Every non-stdlib direct import has an intentional direct dependency or documented extra.
- [ ] The lock change is limited to the intended declarations and necessary resolution changes.

Rollout: normal image rollout; lockfile/image revert restores the prior dependency set.

<a id="sys-05"></a>

### SYS-05 — Starting local development can silently author migrations

| Field | Assessment |
|---|---|
| Priority / evidence | `P2`; confirmed Make dependency behavior |
| Source | `Makefile:28`, `:36`, `:40`, `:48` |
| Trigger | Run `make run`, `make worker` or `make dev` with a model change lacking a migration |
| Impact | Startup writes and applies a new migration as an incidental side effect |
| Effort / reduction | Under 0.5 day; separate one explicit authoring command |

The `migrate` target first runs `makemigrations`, and development startup targets depend
on it. That hides the distinction between reviewing a schema change and starting a process.

Remediation: make `migrate` apply committed migrations only; expose migration authoring
as an explicit target. Keep the existing migration-safety checks. Use `makemigrations
--check --dry-run` at the intended verification gate rather than auto-writing during startup.

Acceptance checks:

- [ ] `make -n migrate run run2 worker dev qcache` and setup-script callers show no migration-authoring command.
- [ ] Starting a clean checkout does not add migration files or change tracked files.
- [ ] The explicit authoring target still creates a migration in a disposable checkout.

Rollout: documentation and Make change; no application tests are needed solely for target text.
Follow the affected-tests plan if repository contract tests own the Makefile.

<a id="sys-06"></a>

### SYS-06 — Test debt has accumulated its own substantial maintenance system

| Field | Assessment |
|---|---|
| Priority / evidence | `P2`; measured maintenance issue; deletion eligibility requires per-behavior review |
| Source | `tests/lexical_ast_ratchet.py:470`; `tests/source_inspection_live.py:11`; `tests/status_200_assertion_live.py:1`; `scripts/browser_journey_policy.py:432`; `tests/test_browser_journey_policy.py:38` |
| Size | 24 matching policy/scanner/manifest files contain 20,288 physical lines; this subset overlaps the overall test/tooling counts |
| Effort / reduction | 5–10 days for a first focused debt-removal batch; 3,000–8,000 test/manifest lines is an initial hypothesis, not proven removable code |

There is already a shared lexical comparison engine: adding another generic scanner
framework would increase complexity. The bigger opportunity is to remove the underlying
wrong-layer or redundant tests and shrink their live debt records. For example,
`SOURCE_INSPECTION_LIVE` explicitly records tests that inspect Python source rather than
observable behavior. `BrowserJourneyIdentityTests` also assert implementation properties
such as the absence of named internals and `__closure__` shape. Some checks enforce
deliberately adversarial trust contracts; they cannot all be deleted as style tests.

Remediation:

1. Select one existing live manifest and map each entry to the product behavior it claims to own.
2. Keep one authoritative behavioral test, or delete an assertion only after establishing
   that another test catches the same distinct boundary regression. Temporarily break that exact behavior,
   observe the retained assertion fail, and revert the break before commit; add no mutation framework or pilot.
3. Update live manifests through their current shrink-only process. Do not edit immutable
   ceilings or golden digests to make a cleanup pass.
4. Once a category's debt is zero, separately review whether its archived ceiling machinery
   can retire under an explicit policy change. Maintain the prohibition on new weak tests.
5. Keep the narrow runtime guard against falsely labeled browser tests, test DB isolation,
   and unauthorized-write side-effect checks until an equally effective simpler replacement exists.

Acceptance checks:

- [ ] A before/after ledger maps every deleted test to a retained boundary assertion or explicit PM-accepted non-requirement, preserving privacy/access/release/data-loss invariants.
- [ ] Report collected test IDs/counts, marker/lane and affected-map changes, coverage deltas and retained boundary coverage.
- [ ] Representative deliberate feature breakages fail the retained tests.
- [ ] Existing policy checks pass without raising ceilings, suppressing errors or changing immutable hashes.
- [ ] Record net test/scanner/manifest LOC, collection duration, and affected-suite duration separately.
- [ ] Each changed test tree follows the exact affected-tests selection, including lexical guards.

Rollout: small domain batches; no mass rename/delete commit. Revert a batch if behavioral
coverage or collection changes unexpectedly. Do not lower the 85% CI coverage gate.

<a id="sys-07"></a>

### SYS-07 — The global test runner keeps an expired payment compatibility window open

| Field | Assessment |
|---|---|
| Priority / evidence | `P2`; confirmed test-environment divergence, not an uncovered production bug |
| Source | `website/test_runner.py:52`, `:175`; `tests/test_test_runner_sharding.py:284` |
| Trigger | An ordinary payment test uses a legacy numeric checkout reference |
| Impact | It runs with a 2099 cutoff rather than the configured production compatibility deadline |
| Effort / reduction | 1–2 days; removes payment policy from a generic runner after fixture migration |

The runner explains that older fixtures depended on the compatibility path and sets
`LEGACY_NUMERIC_CHECKOUT_REFERENCE_CUTOFF` globally. Dedicated security tests override
the setting to verify the real cutoff, so it would be incorrect to claim the cutoff has
no coverage. The coupling still makes new ordinary tests exercise legacy behavior by default.

Remediation: update normal checkout fixtures to the canonical signed/opaque reference
format, and keep explicit clock/config overrides inside a small compatibility test class.
Then remove the runner's payment-specific env mutation and restoration code while retaining
the test-only fast password hasher and traceback handling.

Acceptance checks:

- [ ] Ordinary payment tests use canonical references without a global cutoff override.
- [ ] Explicit before/at/after-cutoff and kill-switch tests still exercise legacy rejection/acceptance.
- [ ] Repeated and parallel runner setup/teardown leaves environment variables unchanged.
- [ ] Run emitted affected tests for both runner and payment fixture changes.

Rollout: tests only; no production cutoff change. Revert fixture migration as a unit if needed.

<a id="sys-08"></a>

### SYS-08 — Per-test CI sharding may repeat expensive class fixtures on every shard

| Field | Assessment |
|---|---|
| Priority / evidence | `P3`; confirmed mechanism, unmeasured performance hypothesis |
| Source | `website/test_runner.py:109`, `:217`, `:228`; `.github/workflows/deploy-dev.yml` test matrix |
| Trigger | Methods in a class with expensive `setUpTestData` hash into several shards |
| Impact | Each shard pays the class setup cost; the audit did not measure its runtime share |
| Effort / reduction | 1 day measurement; 1–2 days only if class-based sharding demonstrates a gain |

The runner selects by the full individual test ID, then partitions remaining tests by class.
That is deterministic and complete, but can create the same fixture on all four runners.
Moving to class hashing is not automatically better: a very large class can unbalance shards.

Remediation: collect per-class setup/execution timings from one normal CI run; simulate
current test-ID hashing versus class hashing or a small weighted class assignment. Use
existing sharding machinery rather than building a new scheduler. Keep the current scheme
if the maximum shard time or reliability gets worse.

Acceptance checks:

- [ ] Union of selected IDs equals the original suite and pairwise intersections are empty.
- [ ] Selection is stable under ordering changes, hash seeds and supported parallel start methods.
- [ ] Report fixture invocation counts, maximum shard wall time and imbalance before/after.
- [ ] Do not claim a speedup based only on fewer setups; compare the whole gate duration.

Rollout: a reversible CI experiment; preserve existing cache isolation and parallel limits.

<a id="sys-09"></a>

### SYS-09 — Several delivery and safety systems need explicit retirement boundaries

| Field | Assessment |
|---|---|
| Priority / evidence | `P3`; lifecycle/maintenance finding, not dead-code proof |
| Source | `website/release_phase.py:1`; `scripts/entrypoint_init.py:420`, `:490`, `:549`; `deploy/deploy_dev.sh:685`; `.github/workflows/ci.yml:3`; `_docs/PROCESS.md:140` |
| Size | Eight workflow definitions total 2,488 lines; agent cleanup/retirement helpers plus their two test files total 10,232 lines |
| Effort / reduction | 2–4 days for inventory/decision; deletion estimates only after active-use evidence |

The project carries explicit R1 compatibility, modern/legacy boot dispatch, an extensive
agent-worktree lifecycle system, and a PR-only CI workflow despite the documented local-merge
process. These facts create investigation candidates. They do not prove the fallback paths,
PR workflow or safety checks are unused. R1 is explicitly enabled in source today.

Remediation:

1. For each compatibility path, record its owner, supported deployments, rollback window,
   queue/schema constraints and a checkable retirement condition.
2. Have on-call/infra verify actual deployed boot modes and old-worker/queued-task state through
   the approved interfaces before proposing R1 or legacy removal. Static repository data is insufficient.
3. Review workflow usage and repository branch protections before removing PR-only CI.
   If it stays, share only genuinely identical setup/test steps with Deploy Dev.
4. Keep agent-worktree lifecycle tooling local in this program. Reconsider distribution only
   in a separately groomed decision if at least two repositories demonstrate identical needs,
   with pinned installation, offline availability, compatibility, rollback and ownership defined.
   Moving code out is not total code reduction.
5. Preserve emergency rollback and deployment readiness gates until replacement behavior is proven.

Acceptance checks:

- [ ] Each deletion cites evidence that no supported deploy, rollback or queued work needs the branch.
- [ ] Predeploy failure still prevents service rollout; workers cannot bypass schema compatibility barriers.
- [ ] A retained rollback rehearsal covers exact image/config/schema assumptions.
- [ ] Tool migration retains shared-main protection, active-agent leases, race/drift refusal and recovery artifacts.
- [ ] Report code deleted versus merely moved and external operational steps introduced.

Rollout: separate operational changes from product refactors; use the normal on-call gate.
Never remove a production compatibility path simply to meet a line-count target.

<a id="sys-10"></a>

### SYS-10 — Measure whether advisory complexity reporting needs stronger enforcement

| Field | Assessment |
|---|---|
| Priority / evidence | `P3`; confirmed advisory-only policy; need for another gate is unproven |
| Source | `ruff-advisory.toml:4`; `Makefile:192`; `scripts/lint_advisory_metrics.py:102`; `pyproject.toml` Ruff selection |
| Baseline | 1,063 advisory diagnostics; 227 `C901`, of which 193 are application functions; 142 application functions exceed 100 physical lines |
| Effort / reduction | 1–2 days of reporting across initial packets; fixes belong to domain work |

The advisory target deliberately uses `--exit-zero`; required Ruff rules are mostly
imports/undefined names. Existing metrics mix large tests with application functions and
do not make a new complexity increase fail the gate. Raw warning counts are not defect counts:
broad exceptions and long functions sometimes encode necessary transactional boundaries.

Remediation: keep Ruff advisory and report touched-function complexity during the first
three simplification packets. Record unexplained increases that actually escape review.
Only propose an enforced gate if that evidence demonstrates a recurring material problem,
and the proposal reuses current machinery with an explicit small maintenance budget.
Exclude migrations, vendor code and test scaffolding. A lower McCabe score without fewer
business-rule owners, branches or states is not simplification.

Acceptance checks:

- [ ] Initial packets report touched-function complexity and concrete escaped regressions, if any.
- [ ] No new immutable complexity baseline, exception manifest, golden digest or blocking CI job is added initially.
- [ ] Any later enforcement proposal justifies its maintenance budget and handles moves/renames without rewarding cosmetic extraction.
- [ ] Long functions retained for a coherent invariant have a clear owner and reason.
- [ ] Before/after reports separate runtime, tooling, tests, migrations and vendor code.

Rollout: report-only initially. Enforcement is a separate evidence-dependent decision;
keep review machinery simpler than the code it governs.

<a id="sys-11"></a>

### SYS-11 — CLI table output collapses collection envelopes into one truncated cell

| Field | Assessment |
|---|---|
| Priority / evidence | `P2`; reproduced offline |
| Source | `asl_cli/asl_cli/output.py:69`; `asl_cli/asl_cli/commands/campaigns.py:26`; compare `asl_cli/asl_cli/commands/users.py:27` |
| Trigger | Use `--format table` on a list command that passes the API envelope directly |
| Impact | Multiple records appear as one abbreviated JSON cell, hiding most rows |
| Effort / reduction | 0.5 day; one explicit collection-output helper replaces command-specific unwrapping |

`print_output` wraps any dictionary in a one-element list. A synthetic two-campaign
envelope produced one row with a truncated `campaigns` cell and a count of two. The users
command already unwraps its list explicitly, so CLI surfaces disagree.

Remediation: pass an explicit collection key or extracted row list from list commands;
keep JSON/raw envelopes unchanged. Do not heuristically flatten every nested dictionary,
because detail responses need different formatting.

Acceptance checks:

- [ ] Two collection records produce two table rows with useful columns.
- [ ] Empty collections produce the documented empty output.
- [ ] JSON/raw retain pagination/count metadata; detail commands retain their object shape.
- [ ] All list-command adapters use the same tested formatting boundary.

Rollout: CLI only; no migration. Coordinate with ongoing CLI edits.

### Retain these existing choices

- Django's modular monolith and database transactions remain appropriate; a microservice
  split would add network and deployment complexity without evidence of benefit here.
- `uv.lock`, a separate dev group, pinned Tailwind, the CSS build stage and generated
  OpenAPI drift checks already remove important forms of environment ambiguity.
- `affected_tests.py`, four-worker limits, isolated Playwright databases, CI sharding and
  the CI coverage gate are useful safeguards. Simplify their internals only with parity checks.
- The cleanup and release compatibility code protects destructive boundaries. Its size is
  a reason to review ownership and lifecycle, not a license to weaken safeguards.

### Actual checks

| Check | Observed result |
|---|---|
| Git status before audit | Two pre-existing modified CLI files; no changes to them by this audit |
| Tracked-Python AST parse | No syntax errors across 2,576 tracked Python files |
| Exact AST-body duplicate scan | 16 candidate groups of functions at least 10 physical lines; signatures/decorators/call context still require review |
| Ruff advisory, `uv run --no-sync ruff check --config ruff-advisory.toml --output-format json --exit-zero .` | 1,063 diagnostics; informational scan, not a passing quality gate |
| Offline CLI origin reproduction | Synthetic credential attached to a foreign initial request |
| Offline CLI path reproduction | Fragment-bearing email truncated before transport |
| Offline CLI table reproduction | Two rows collapsed into a truncated envelope cell |
| `uv tree --offline --locked --invert --package requests` and `pyyaml` | Both are transitive rather than root direct dependencies |
| Application/browser/production checks | Not run; they are proposed verification in each remediation packet |
