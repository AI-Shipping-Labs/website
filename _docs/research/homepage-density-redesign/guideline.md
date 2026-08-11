# Logged-in dashboard density redesign — implementation guideline

Status: approved direction, pending implementation

## 1. Current behavior and evidence

- [FACT logged-in-dashboard-render] The clean active-Main-member dashboard is 1,988px tall at 1280px and 4,069px tall at 393px.
- [FACT logged-in-dashboard-template] Continue learning, upcoming events, six Quick action cards, conditional onboarding/plan/Slack/sprint modules, five Recent content rows, and up to two Active poll cards use similar bordered-card treatment.
- [FACT logged-in-dashboard-template] Book Club is not part of the dashboard context; logged-in members discover it only through site navigation/footer.
- [INFERENCE logged-in-dashboard-render,logged-in-dashboard-template] The page offers many destinations but weakly prioritizes the member's next consequential action.

## 2. Desired behavior and acceptance criteria

1. The authenticated header says `Here's what to focus on this week.` below the greeting.
2. A shared active sprint plan, when present, renders as the first dominant task card after urgent/startup notices and before the Continue/Up next grid.
3. The dominant plan card preserves real sprint metadata, progress, `Open my plan`, cohort, recap, feedback, and next-action branches; no invented checkpoint data is introduced.
4. Continue renders at most three progress cards. An accessible current Book Club book occupies one slot, leaving at most two course/workshop cards.
5. The Book Club card shows the current book, author, completed/total chapters, percentage, next unread chapter and deadline when present, cadence, and a `Start reading`, `Continue reading`, or `View book` CTA.
6. Members below the book's required level, or when no current book exists, see no dashboard Book Club card and retain up to three learning cards.
7. Up next renders at most two registered upcoming events while the starting-soon urgency card remains unchanged.
8. Broad destinations render in one compact `Explore` icon-link row. Base destinations are Courses, Workshops, Events, Resources, and Projects; eligible `Request a call` remains available.
9. Poll, Slack, and sprint/cohort discovery render as one compact Community strip rather than large standalone dashboard cards. Existing access/dismissal rules remain authoritative.
10. Latest from the community renders at most three accessible article/recording rows.
11. Free activation, onboarding prompt, plan-preparing, starting-soon, tier override, and empty-state behavior remains functional.
12. Desktop and 393px layouts have no horizontal overflow, visible focus treatment, and 44px page-level targets.

## 3. Chosen design and rejected alternatives

- [HUMAN] Use the generated logged-in concept as the approved hierarchy and visual direction.
- [INFERENCE logged-in-dashboard-template] Reuse existing token classes, button tags, badges, and dashboard context instead of introducing a dashboard-only design system.
- [INFERENCE logged-in-dashboard-template] Keep the shared site footer unchanged; changing it would expand this dashboard task into a sitewide navigation change.
- [INFERENCE logged-in-dashboard-template] Keep real conditional member states instead of hard-coding the concept's illustrative sprint/event data.
- Rejected: adding Book Club as a seventh Quick action. It increases directory density and does not surface reading progress.
- Rejected: removing Free/onboarding guidance to match the active-Main mockup. Those are required lifecycle states, not optional visual clutter.
- Rejected: inventing a persisted `next action` model. Existing plan, learning, reading, and event data is enough for this layout pass.

## 4. Affected interfaces, modules, data, and dependencies

- `content/views/home.py`
  - Add one bounded Book Club dashboard selector.
  - Limit visible learning to two when Book Club is present, otherwise three.
  - Expose at most two scheduled events and three recent-content rows.
  - Reduce base Explore actions to five.
- `templates/content/dashboard.html`
  - Reorder the existing sprint-plan card above the primary grid.
  - Add the Book Club progress card.
  - Restyle Quick actions as Explore.
  - Replace standalone Slack/sprint/poll modules with Community strip links.
  - Retain conditional activation/onboarding/preparation surfaces.
- Focused Django and Playwright dashboard tests.
- No schema migration, background task, production API, or integration-setting change.

## 5. Compatibility and migration strategy

- No data migration is required; Book Club progress remains derived from `ChapterRead` rows.
- Book access continues to use `required_level` and the already-computed effective dashboard tier, including tier overrides.
- Existing URLs and form actions remain unchanged.
- Existing `data-testid` values are preserved where the semantic component still exists; obsolete large-card selectors are replaced with Community-strip selectors in tests.

## 6. Failure modes, security, and observability

- A current book with zero chapters renders a safe `View book` action and zero progress.
- A current book above the viewer's tier is omitted without leaking participation data.
- Multiple current books select the newest by start date/created date, matching the Book Club hub convention.
- Per-member read state is fetched with a constant-query annotated chapter query, avoiding an N+1.
- External Slack invite URLs remain hidden; the dashboard links only to the gated internal Slack endpoint.
- The dashboard query budget may rise by one query when no current book exists and two when a current book exists; the bounded query test is updated explicitly.

## 7. Ordered implementation tasks

1. Add `_get_dashboard_book_club(user, user_level)` with derived progress and next-chapter CTA data.
2. Wire Book Club and display limits into authenticated dashboard context.
3. Recompose the dashboard template around plan → Continue/Up next → guidance → Community → Latest/Explore.
4. Update and add focused Django tests for Book Club, compact content limits, Explore, Community, and query bounds.
5. Update focused Playwright expectations for the new hierarchy and interactions.
6. Run focused Django tests, focused Playwright dashboard tests, and capture desktop/mobile authenticated screenshots.

## 8. Verification mapping

- Criteria 1–3: dashboard header and sprint-plan Django/Playwright tests.
- Criteria 4–6: new Book Club dashboard tests plus learning-limit assertions.
- Criterion 7: upcoming-events context/render tests.
- Criteria 8–10: Quick action, Slack, sprint, poll, and recent-content tests updated to compact surfaces.
- Criterion 11: existing Free activation, onboarding, and plan-preparing tests.
- Criterion 12: focused Playwright screenshots at desktop and Pixel 7 plus overflow assertion.
- Query behavior: `content/tests/test_dashboard_performance.py` and helper query-count test.

## 9. Documentation

- Preserve the implementation result and deviations in this project's `critique.md` and log.
- No ADR is warranted because no new architectural boundary or persistence model is introduced.

## 10. Blocking questions

None. The user's `let's do it` explicitly approves the logged-in concept. Conditional member-state behavior follows existing code and tests.
