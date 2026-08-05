# Book Club prototype — todo (2026-08-05)

Branch: `prototype/book-club`. Live at `localhost:8009/books`.
Design references: `_docs/audits/2026-08-05-book-detail-design-audit.md`,
`_docs/audits/2026-08-05-book-club-per-chapter-page-spec.md`,
`_docs/audits/2026-08-05-design-system-gaps-and-enforcement.md`.

## In progress / next

- [ ] Rewrite `book_detail` to single column (remove the 2-column sidebar) — per the Fable audit.
- [ ] Member progress placed inline (no second column): one-row callout with stats + next-action button + link to the Progress page.
- [ ] Reading roadmap: chapters are whole-row clickable anchors to per-chapter pages; no per-row buttons or repeated "Join to…" hints.
- [ ] Build per-chapter page (`/books/<slug>/chapters/<n>`): header, mark-as-read, your-note editor, group notes feed, prev/next.
- [ ] Move per-chapter summaries + "compile summary" onto the chapter page (remove the summaries widget from `book_detail`).
- [ ] Leaderboard → its own page, renamed "Progress"; remove the widget from `book_detail`.
- [ ] Weekly meetings rows link to real events (event series).
- [ ] Make every button/link clickable (no dead `#`).
- [ ] Finished-book card → design-system centered CTA box; guest vs member differ on past pages.
- [ ] Rename "Book Club" → "Books" across all bookclub templates.

## Platform / follow-ups

- [ ] Create the book-club weekly event series (prod API + local) and link it.
- [ ] Design-system: add lint rules (hand-rolled button, `rounded-2xl`, hand-rolled pills) — `test_design_system_lint.py`.
- [ ] Update nav Playwright/Django tests for the redesigned header.

## Filed issues

- #1353 Book Club feature (tracking)
- #1355 Remove Project Ideas + Curated Links
- #1357 Auto-enroll event-series registrants into events added later
