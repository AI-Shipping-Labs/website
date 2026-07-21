# Layout audit: /courses, /sprints, /activities as nav landing pages

| Field | Value |
|---|---|
| Audit date | 2026-07-21 |
| Pages | `/courses` (Courses), `/sprints` (Community Sprints), `/activities` (Activities, nav-linked as `/activities#access-by-tier`) |
| Viewports | Desktop 1280x900, Pixel 7 393x851 |
| Auth variants | Anonymous, `designer-free@test.com`, `designer-main@test.com` |
| Screenshot directory | `.tmp/lp-audit-courses-community/` |
| Server | Dev server `127.0.0.1:8766`, synced content: 3 courses, 4 sprints (2 current, 0 future, 2 past), 7 activities, 2 events, 3 workshops |

Scope: layout only. Copy is covered by a separate audit. Outer container widths are out of scope by instruction — all three pages are Frame tier (`max-w-7xl px-4 sm:px-6 lg:px-8`) per `content/tests/test_container_widths.py`; internal layout only below.

## Capture caveat: the running server serves a stale /sprints template

The dev server on 8766 has cached the pre-change template (cached template loader; process runs `--noreload --insecure`). Served HTML for `/sprints` still contains `mx-auto max-w-5xl px-6 lg:px-8`, while the working tree has the uncommitted change to `mx-auto max-w-7xl px-4 sm:px-6 lg:px-8` (`templates/content/sprints_index.html:13`). The same applies to the `/activities` gutter-only diff at `templates/content/activities.html:16`.

To audit the real change, the 7xl class string was applied to the live DOM via Playwright before capturing (directories prefixed `sim7xl-`). All Tailwind utilities involved already exist on the page, so the simulated render is pixel-faithful to the new template. A clean served 5xl baseline was captured for comparison. No repo files were touched.

All captures are dark theme (the capture script pins `color_scheme="dark"`); light theme is assessed from class strings only.

## Screenshots

| Capture | URL |
|---|---|
| /sprints desktop full, new 7xl (simulated), anonymous | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/67c1dea2f5ee42e58c85b29908d2db3e-4a5fbcce4d764e55.png |
| /sprints desktop full, served 5xl baseline, anonymous | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/dbe43702a17e4c269ca51cd27b20565d-9948c71395184f47.png |
| /sprints Pixel 7 full, 7xl, anonymous | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/756ff8c8625f407884cc53b2677e34ee-672a9cd91df51c29.png |
| /sprints desktop fold, 7xl, signed-in Main | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/c9ee2ceb8d544925a6b9c61c2c67593f-47965e553893ad6c.png |
| /sprints desktop full, 7xl, signed-in Free (upgrade CTA states) | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/c7b63d4dd4fb4c26a6fce9ac461b5ad8-531ca1d0b3abfc30.png |
| /sprints desktop full, simulated fully-empty state, 7xl | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/cbc314f01c414618a06eccd4a4a858c6-e3d107053dca52e4.png |
| /sprints Pixel 7 full, simulated fully-empty state, 7xl | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/1318d3e429994393aeae5399b4a53ef4-e4a625b685d8c2f5.png |
| /courses desktop full, anonymous | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/347ad7e704e04be187e93b4583696e8a-7c42617d016ac62e.png |
| /courses Pixel 7 full, anonymous | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/18495cd063884353b8b33a0ffd1c2885-7fdf7a54563e709a.png |
| /courses desktop fold, signed-in Free | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/06cbf8994b6d484abc95d922bde3f744-49de8063d17e7292.png |
| /activities desktop full, anonymous | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/d31cfc88ee2046c6b641aead9fc45ab1-861dd62e7c3fc146.png |
| /activities Pixel 7 full, anonymous | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/2b57d3519a874010a3f0ae497f792507-b17977bb1e73a7e7.png |
| /activities Pixel 7 fold, landed via `#access-by-tier` anchor | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/0b81ee716d7a46e78ae973c90bd66d23-28a7bdc72055998f.png |
| /activities desktop crop, sprint card sparseness | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/f1c0b240bb1842e5b16b766deed40b52-3695784b505c1d8f.png |

## Summary

The 7xl move on `/sprints` fixes the inset-from-chrome bug and aligns the page with `/events`, but the sprint card internals were designed for a 960px column and now render as a ladder of 1216px-wide cards whose right half is empty. All three pages share the deeper landing-page gap: hero without a CTA row, then a card list, then nothing — no access explainer, no pricing path (`/courses`, `/sprints`), and no static content that survives an empty database (`/sprints`).

## 1. Priority one: how /sprints reads at max-w-7xl

Verdict: the outer width is right, the internals are not.

What works at 7xl:

- The hero and section headings now left-align with the header logo and footer columns; the old 5xl render sat inset 128px from the chrome above it, which the design system explicitly calls a layout bug for index pages. Compare the 7xl and 5xl full-page captures above.
- The intro stays readable because it is already capped by the inner `max-w-3xl` at `templates/content/sprints_index.html:14`.
- Pixel 7 is unchanged and healthy: cards stack, CTAs go full width, no horizontal scroll.

What breaks at 7xl (desktop capture, all four cards):

- Each card in the single-column `grid gap-4` (`sprints_index.html:36`) now spans ~1216px while its content column (`sprints_index.html:40-66`) occupies only the left ~700px. The CTA floats alone at the top right (`sprints_index.html:67-72`). Roughly 40% of every card is empty surface, and the two-line facts list (`dl` with a single `Sprint window` fact, `sprints_index.html:54-59`) plus two boilerplate paragraphs make every card ~200px tall with very low information density. The page reads stretched, not spacious.
- Sibling comparison: `/events` at 7xl uses full-width stacked rows too, but its rows are dense (title, inline datetime, badges, attendee meta on one or two lines). The sprint card has neither the density of an event row nor the shape of a catalog card.

Concrete internal adjustment (see Recommendations R1 for diffs): switch the per-section list to a two-column card grid at `lg` with the sanctioned `gap-6`, make the card a full-height column with the badge row, title, one inline meta row, and a bottom-left CTA. At ~576px per card the current content fills the box, section scan order stays left-to-right, and four sprints occupy two rows instead of a four-screen ladder. If single-column rows are preferred instead, the card must be flattened toward the `/events` row shape (inline meta, no reserved right column), not left as-is.

## 2. Above the fold, anonymous first-time visitor

### /sprints, 1280x900 (7xl)

Visible: header, eyebrow, H1 `Community Sprints`, two-line lead, `Current sprints` heading, and the full first card including its `Log in to join` CTA. That is a decent fold: one real next action is visible. Missing: any page-level CTA row in the hero (design-system hero order is eyebrow, H1, lead, CTA row) and any hint that a free tier exists beyond the card badge.

### /sprints, 393x851

Visible: eyebrow, H1, four-line lead, `Current sprints`, and the badge row plus title of card 1. CTA below the fold. Acceptable.

### /courses, 1280x900

Visible: header, eyebrow, H1 `Structured Learning Paths`, one-line lead, and the three media bands plus the top of each card body. Because all three cards currently render the decorative fallback (book icon on `bg-secondary`), the fold is dominated by a placeholder wall — the design system calls a grid of all-fallback media bands a defect. About 140px of empty band sits between the header and the eyebrow (`pt-24` on `main` at `templates/content/courses_list.html:12` plus `lg:py-24` at line 13); sanctioned rhythm, but with no hero CTA the fold spends its budget on whitespace and placeholders.

### /courses, 393x851

Visible: eyebrow, H1, lead, and the first card's fallback media band only. The first course title is below the fold; the `aspect-video` fallback band costs the entire first screen its content.

### /activities, 1280x900

Visible: header, centered eyebrow `ACCESS BY TIER`, H1 `Membership benefits by tier`, two leads, the three anchor pills, and the top of the first two activity cards. This is the strongest fold of the three pages: it states what the page is and offers three in-page destinations.

### /activities, 393x851

Visible: eyebrow, H1, both leads, and the three anchor pills (wrapping 2+1). The first card is just below the fold. Good.

## 3. Hierarchy, card density, grid rhythm, media treatment

### /sprints

- H1 `text-3xl ... sm:text-4xl lg:text-5xl` (`sprints_index.html:19`) uses the detail-hero scale rather than the page-H1 scale; tolerable, but see the same problem amplified on `/activities`.
- Section h2 `text-2xl font-semibold tracking-tight` (`sprints_index.html:32`) lacks the `sm:text-3xl` step of the smaller-section-h2 pattern; minor.
- `grid gap-4` at `sprints_index.html:36` violates the spacing rule that repeated card grids use `gap-6` (`gap-4` is for tight operational rows).
- Every card repeats the same two boilerplate sentences (`sprints_index.html:60-65`), so cards are tall but carry only three real facts: name, window, required tier. Density problem is layout-visible even though sentence wording is the copy audit's domain.
- All CTAs render at primary accent weight regardless of state — current, past, `View sprint`, `Log in to join`, `Upgrade to Main` all look identical (see signed-in captures). Past-sprint cards should carry secondary-weight actions so current sprints dominate.
- Media: none, correctly — no media policy exists for sprints and no fake band is rendered.

### /courses

- Grid rhythm is correct: `gap-6`, `sm:grid-cols-2 lg:grid-cols-3`, with the count-adaptive narrowing for 1-2 courses computed at `content/views/courses.py:54-59`. Card partial usage (`_clickable_card_classes`, access badges, `line-clamp`) follows the system.
- Media treatment is the defect: every card passes `preview_decorative_fallback=True` (`courses_list.html:32-36`) and no synced course has `cover_image_url`, producing an all-fallback wall at both viewports. Per the design system, either real covers must exist for courses (policy: Render) or the band should not render.
- H1 `Structured Learning Paths` (`courses_list.html:18`) is title case; casing rule requires sentence case.
- Hierarchy after the grid simply ends: no access explainer, no pricing CTA, no cross-links. The page is an index, not a landing page.

### /activities

- Heading scale is inverted in places. The section h2 `Active community sprints` uses `text-3xl ... sm:text-4xl lg:text-5xl` (`activities.html:197`), the same size as the page H1 (`activities.html:22`) — two competing h1-scale headings on one page. Meanwhile the `Quick comparison` h2 is only `text-xl` (`activities.html:108`), smaller than any sanctioned section-h2 pattern, so the page's best conversion module has the weakest heading on the page.
- Activity card grid (`activities.html:45`, inner `max-w-6xl md:grid-cols-2 gap-6`) is healthy, but 7 cards in 2 columns leaves `Mini-courses` orphaned bottom-left. Rhythm break, not a defect.
- Sprint cards in the `#community-sprints` section (`activities.html:211-254`) have the same 7xl sparseness as `/sprints`, worse: a full-width card with a fully vertical `space-y-5` stack pinned left, entire right half empty (see crop capture). With exactly 2 current sprints, `md:grid-cols-2` would fill the band.
- Events and workshops grids (`activities.html:278`, `activities.html:312`) use correct `gap-6 md:grid-cols-2 lg:grid-cols-3`; with 2 events one slot stays empty — acceptable.
- Tier badges on activity cards mix three one-off recipes (`bg-muted-foreground/20`, `bg-accent`, `bg-foreground text-background`, plus struck variants at `activities.html:67-92`) rather than the `member_badges` tags; visually effective, but it is a third badge dialect not owned by the component index.

## 4. /activities anchor landing and mobile tier legibility

- Landing at `/activities#access-by-tier` produces a sensible view at both viewports: the target section (`activities.html:15`) is the first element in flow, so the browser lands at the top of the page with the hero fully visible under the fixed header (see anchor fold capture). No fix needed for this specific anchor today, but it is order-dependent: if any section is ever inserted above it, the fixed header will overlap the target.
- The in-page pills `#community-sprints`, `#live-events`, `#workshops` (`activities.html:33-41`) do land mid-page, and those sections have no scroll margin. Desktop survives because `sm:py-20` padding exceeds the ~64px fixed header; at 393px the sections have `py-12` (48px), so the header overlaps the top ~16px of the section and the eyebrow arrives partially hidden. Add `scroll-mt-24` to the anchored sections (R6).
- Tier comparison at 393px is legible: the three `Quick comparison` cards stack single-column with full-size text and the accent-bordered Main card reads as the anchor tier. The cost is repetition — three near-identical 7-row lists in a row on mobile — but nothing clips or truncates. Activity-card tier badge rows (`Basic / Main / Premium` chips) wrap cleanly at 393px.

## 5. /courses tier gating and locked-state treatment

- Signals present: each card carries `member_access_badge` (`courses_list.html:40`) — green `Free` check or accent `Premium` lock — and the media band repeats the access label. A free or anonymous visitor can tell which cards are open.
- What is missing is the consequence path. Nothing on the page says what `Premium` is, what it costs, or where to get it; there is no pricing link anywhere in the body. The lock is a wall, not a door — closer to frustrate than motivate. Signed-in Free renders byte-identical card bodies (fold capture), so the gap applies to the whole funnel.
- Locked cards are fully clickable through to the course detail (good; the paywall lives there), but on the list page the only difference between a free and a locked card is the badge color. There is no de-emphasis or upgrade affordance on the card itself. Minimum fix at the page level: an access strip under the grid — one sentence on Free vs Premium plus a `View pricing` CTA and a link to `/activities#access-by-tier` (R3). Whether locked cards additionally get an on-card upgrade hint is a product decision.

## 6. /sprints empty states

- Fully-empty branch (`sprints_index.html:86-102`, simulated capture): hero, one card saying `Next sprint coming soon` with `Events` and `Workshops` buttons, then newsletter, then footer. At 7xl the card is a 1216px-wide box with a small left-aligned cluster. As a nav destination this is one screen of content with no way to be notified about the next sprint — the newsletter card sits directly below but the empty state never points at it.
- The empty card is hand-rolled: it does not use `{% member_empty_state %}` (mandatory owner for every member/public collection empty state) and its two buttons hand-roll secondary chrome instead of `{% button_classes 'secondary' %}`. The sibling empty state on `/activities` (`activities.html:258`) already does this correctly — copy that call.
- Section-empty rows (`sprints_index.html:78-80`) render a bare one-line box, e.g. `No future sprints are scheduled yet.` between two populated sections. Empty `Future` between populated `Current` and `Past` adds scaffold noise; hiding empty sections (or at minimum the `Future` one) reads better than announcing three taxonomies on every visit.
- The structural fix for the dead-end risk is static content: a `How sprints work` explainer section that renders regardless of data (R5), so the page always explains the offering, and the empty card gains a `Get notified` CTA anchoring to the newsletter section plus the existing events/workshops cross-links.

## Bugs observed (not fixed, per instructions)

1. Stale template cache: the 8766 dev server serves the pre-change `/sprints` markup; anyone eyeballing the page to verify the 7xl change will see 5xl until the server restarts. Operational note for whoever owns the server.
2. Ended sprints receive join/upgrade CTAs. Anonymous visitors get an accent `Log in to join` on `ENDED` cards (label set unconditionally for anonymous at `content/views/pages.py:153-155`); signed-in ineligible users get `Upgrade to Main` on ended sprints (`content/views/pages.py:175-177`). Both invite action on a sprint that cannot be joined.
3. Light-theme contrast: the hand-rolled enrolled badge `bg-emerald-500/15 ... text-emerald-300` (`sprints_index.html:45`) will be near-invisible on light backgrounds; the badge recipe requires `text-<color>-800 dark:text-<color>-400`. `/activities` already uses `member_status_badge` for the same state (`activities.html:219`).
4. All-fallback media wall on `/courses` (see section 3) — defect by design-system definition.
5. `Structured Learning Paths` title-case H1 (`courses_list.html:18`) violates the sentence-case rule.

## Recommendations, ranked by impact

### R1. Re-lay the /sprints card list for the 7xl frame

`templates/content/sprints_index.html:36`:

```diff
-            <div class="mt-4 grid gap-4">
+            <div class="mt-4 grid gap-6 lg:grid-cols-2">
```

`templates/content/sprints_index.html:38-39` — make the card a full-height column so CTAs align across a row:

```diff
-              <article class="rounded-lg border border-border bg-card p-5 shadow-sm sm:p-6" data-testid="sprints-sprint-card">
-                <div class="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
+              <article class="h-full rounded-lg border border-border bg-card p-5 shadow-sm sm:p-6" data-testid="sprints-sprint-card">
+                <div class="flex h-full flex-col gap-5">
```

`templates/content/sprints_index.html:67-68` — CTA becomes a bottom action, pushed down with `mt-auto`, and takes its chrome from the owning tag (add `{% load accounts_extras %}` at the top):

```diff
-                  <a href="{{ item.cta_url }}"
-                     class="inline-flex min-h-[44px] w-full shrink-0 items-center justify-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition-opacity hover:opacity-90 sm:w-auto"
+                  <a href="{{ item.cta_url }}"
+                     class="{% button_classes 'primary' extra='mt-auto w-full sm:w-fit' %}"
```

Use `'secondary'` for past-section cards so ended sprints stop competing with current ones. Also replace the two-line `dl` (`sprints_index.html:54-59`) with a single inline meta row (`calendar` icon + date range, `text-sm text-muted-foreground`) matching event cards. This also fixes the missing focus-visible ring and non-standard `hover:opacity-90` on the current hand-rolled CTA. If the PM prefers keeping single-column rows, the alternative is the `/events` row shape: inline meta, badges and CTA on the same visual band, no reserved right column — but do not keep the current card at full width unchanged.

### R2. Complete the hero per the design system on /sprints and /courses

Both heroes stop at the lead; the sanctioned order is eyebrow, H1, lead, CTA row. Add a CTA row after `sprints_index.html:24` and `courses_list.html:22`, e.g. anonymous: `{% button_classes 'primary' size='lg' %}` `Join free` plus a secondary `How membership works` linking `/activities#access-by-tier`. This is the single biggest lever for making these read as landing pages rather than indexes.

### R3. Give /courses an access path and fix the media wall

- After the grid (below `courses_list.html:69`), add a full-width access strip: one-sentence Free vs Premium explanation plus `View pricing` (`{% button_classes 'primary' size='lg' %}`) and a text link to `/activities#access-by-tier`, using the marketing header/action row pattern.
- Casing fix at `courses_list.html:18`: `Structured Learning Paths` to `Structured learning paths`.
- Media: either ship real `cover_image_url` values for the published courses in the content repo, or drop `preview_decorative_fallback=True` from `courses_list.html:32-36` and let coverless course cards start with their badge row (mirrors the workshops conditional-explicit precedent). Which of the two is a product decision (below).

### R4. Normalize /activities heading scale and sprint-card density

`templates/content/activities.html:197`:

```diff
-          <h2 class="mt-4 text-3xl font-semibold tracking-tight text-foreground sm:text-4xl lg:text-5xl" style="text-wrap: balance;">
+          <h2 class="mt-4 text-3xl font-semibold tracking-tight text-foreground sm:text-4xl" style="text-wrap: balance;">
```

`templates/content/activities.html:108`:

```diff
-        <h2 class="mb-8 text-center text-xl font-semibold text-foreground">Quick comparison</h2>
+        <h2 class="mb-8 text-center text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">Quick comparison</h2>
```

`templates/content/activities.html:211` — same density fix as R1:

```diff
-          <div class="space-y-4">
+          <div class="grid gap-6 md:grid-cols-2">
```

with `h-full` on the card at `activities.html:213` and `mt-auto` on the button row at `activities.html:239` so the two cards align.

### R5. Make /sprints safe to land on when empty

- Replace the hand-rolled empty card at `sprints_index.html:86-102` with `{% member_empty_state %}` exactly as `/activities` does at `activities.html:258`, adding a primary `Get notified` CTA that anchors to the newsletter section (`/sprints#newsletter` — the shared newsletter include already carries `id="newsletter"`), with events/workshops as secondary links.
- Add a static `How sprints work` section (three compact steps: pick a project, ship in the window, demo to the community) between the sprint sections and the newsletter so the page explains the offering even with zero rows. Reuse the smaller-section-h2 pattern and default `p-6` cards.
- Hide empty sections instead of rendering the one-line boxes at `sprints_index.html:78-80`, or at minimum suppress `Future sprints` when empty.

### R6. Anchor scroll margins on /activities

Add `scroll-mt-24` to the anchored sections at `activities.html:15`, `activities.html:189`, `activities.html:265`, `activities.html:299` so pill and nav anchor jumps clear the fixed header at 393px (48px mobile section padding is less than the ~64px header).

### R7. Component-ownership cleanups (bundle with whichever issue touches these files)

- `sprints_index.html:45-49` enrolled badge to `{% member_status_badge "You're enrolled" status="registered" icon="check" %}` (fixes the light-theme contrast bug too).
- `sprints_index.html:93-100` empty-state buttons to `{% button_classes 'secondary' %}`.
- CTA states for ended sprints (`content/views/pages.py:150-177`) need a `View recap`/`View sprint` label instead of join/upgrade — view logic, pair with R1.

## Proposed section order

| Page | Order |
|---|---|
| `/sprints` | Hero (eyebrow, H1, lead, CTA row) → Current sprints (2-col grid) → Future sprints (only when non-empty) → How sprints work (static explainer) → Past sprints (secondary-weight CTAs) → Newsletter → Footer |
| `/sprints` (empty) | Hero with CTA row → How sprints work → `member_empty_state` with Get notified + events/workshops links → Newsletter → Footer |
| `/courses` | Hero (eyebrow, H1, lead, CTA row) → Course grid → Access by tier strip with pricing CTA → Newsletter → Footer |
| `/activities` | Keep current order (tier hero + anchor pills → activity cards → quick comparison + pricing CTA → sprints → events → workshops → newsletter); apply R4 scale/density fixes within it |

## Open product decisions

1. `/courses` media policy in practice: invest in real cover images for the three published courses (policy stays Render) or switch course cards to conditional-explicit and drop the decorative fallback. Design system change required only for the second option.
2. Whether locked `/courses` cards get an on-card upgrade affordance (e.g. compact `View pricing` hint) or the page-level access strip is sufficient.
3. `/sprints` past-section prominence: keep past sprints on the landing page (with secondary CTAs) or collapse them behind a `View past sprints` link once the list grows.
4. Hero CTA targets for anonymous visitors on `/sprints` and `/courses`: `Join free` versus `View pricing` as the primary action.
5. Whether the `Future sprints` empty row has enough operational value (signaling cadence) to keep against the recommendation to hide empty sections.

## Out of scope

- All copy: boilerplate sentence repetition on sprint cards, lead wording, empty-state phrasing (separate copy audit owns these).
- Outer container widths (just normalized, enforced by test).
- The consent dialog's fixed-position rendering artifact in full-page captures.
- Sort order of QA-named content appearing first in the course grid (data, not layout).
