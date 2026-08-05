# Book Club prototype — todo (2026-08-05)

Branch: `prototype/book-club`. Live at `localhost:8009/books`.
Design references: `_docs/audits/2026-08-05-book-detail-design-audit.md`,
`_docs/audits/2026-08-05-book-club-per-chapter-page-spec.md`,
`_docs/audits/2026-08-05-design-system-gaps-and-enforcement.md`,
and the synthesized IA plan (implemented).

## Done (committed)

- [x] Single-column `book_detail` (no sidebar), member progress inline callout.
- [x] Reading roadmap: chapters are whole-row clickable anchors; tick is the only read indicator; no per-row buttons or repeated hints.
- [x] Per-chapter page (`/books/<slug>/chapters/<n>`): mark-as-read, your-note composer, group notes feed, prev/next.
- [x] Per-chapter summary lives on the chapter page (`#summary`); summaries widget removed from `book_detail`.
- [x] Leaderboard → its own page, renamed "Progress"; widget removed from `book_detail`.
- [x] Member reading page: progress chip strip + notes feed.
- [x] Weekly meetings rows link to events (`/events`; prod intent: series occurrence pages).
- [x] Every button/link clickable (no dead `#`); interactive states via `?read=`/`?edit=`/`?notify=`.
- [x] Finished/upcoming secondary book pages; guest vs member throughout.
- [x] "Book Club" → "Books" everywhere; "Leaderboard" vocabulary removed.
- [x] Guards green (container-widths, design-system-lint, member-badges); all routes 200 member + guest.

## Follow-ups

- [x] Design-system: lint rules (hand-rolled button, `rounded-2xl`, hand-rolled pills) added to `test_design_system_lint.py` (shrink-only ratchet; new drift fails CI).
- [x] Nav Playwright/Django tests updated for the redesigned header (all pass).
- [ ] Create the book-club weekly event series (prod API + local) and link real events — BLOCKED on the meeting cadence (day/time), which the kickoff sets on the call. Prototype meetings link to `/events` meanwhile.

## Filed issues

- #1353 Book Club feature (tracking)
- #1355 Remove Project Ideas + Curated Links
- #1357 Auto-enroll event-series registrants into events added later
