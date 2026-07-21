# Designer audit — workshops and events nav landing pages (layout)

| Field | Value |
|---|---|
| Audit date | 2026-07-21 |
| Pages | `/workshops`, `/workshops/catalog`, `/events` (including `/events?filter=past`) |
| Viewports | Desktop 1280x900, Pixel 7 393x851 |
| Auth states | Anonymous, free tier (`designer-free@test.com`), premium tier (`designer-premium@test.com`) |
| Screenshot directory | `.tmp/lp-audit-workshops-events/` (raw runs) and `.tmp/lp-audit-workshops-events/clean/` (consent panel dismissed; `_fold` = viewport-height, `_full` = full page) |
| Scope | Layout only. Copy is covered by a separate agent. No files other than this report were modified. |

## Screenshots

| Capture | URL |
|---|---|
| `/workshops` desktop fold, anon | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/aaab9779299341a6a023ce2e7e04f2ca-e72c657f04a88eb7.png |
| `/workshops` Pixel 7 fold, anon | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/56261ddb18a34d839671093da5b675b3-863c0ad434149fe4.png |
| `/workshops` desktop full, anon | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/78953833ad784325afdf1a699e41b323-06a60886eb158a67.png |
| `/workshops/catalog` desktop fold, anon | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/65a2783b285f458c8acd1bf80e338de2-7bc9ab4f58b67a50.png |
| `/workshops/catalog` Pixel 7 fold, anon | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/f803801df34e48b5aa7457a9d6498ac4-92da6463d4162673.png |
| `/workshops/catalog` desktop full, anon | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/4a1c7bde00fb45dfbe28b39e8ef39a87-9245f513c3f6b673.png |
| `/events` desktop fold, anon | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/103945b904464d16b15ec20e5904950b-bc04de5c20f90286.png |
| `/events` Pixel 7 fold, anon | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/bd92a07126ce46b494a69afe54b70d5a-c927cad964565fa8.png |
| `/events?filter=past` desktop full, anon | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/37035e13ffde411db7e5fc88657e477b-99f1d08b3dee5326.png |
| `/events` desktop full, signed in free | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/f062abcdf7de4ce7841a713db81ec7e2-181de58cfa9d886a.png |

All 48 local captures (3 auth states x 2 viewports x 4 URLs x fold/full) are in `.tmp/lp-audit-workshops-events/clean/{anon,free,premium}/`.

## Mid-audit template churn (trust-the-screenshot notes)

Another session was editing these templates during the audit. Three places where the rendered page and the on-disk template disagreed at audit time:

1. Series occurrence card on `/events`: the live render puts an accent-colored `Series: <name>` link as the first line of the card, above the badge row and outside the card anchor. The on-disk `templates/events/_upcoming_event_card.html:42-49` instead renders the series name as plain muted text below the title. The on-disk comment in `templates/events/_upcoming_series_card.html:9-13` documents lockstep anatomy (badge row first, `text-lg` title, muted series line) — the live render violates it. Findings below describe the rendered state.
2. Workshop cards on both workshop pages render a `Workshop` type badge on every card; the on-disk `templates/content/_workshops_catalog.html:162-164` has a comment removing it as redundant. The removal appears to be in flight.
3. Individual cards intermittently render with an accent border in captures (the series card on `/events`, one free card and the first past-recording card): `templates/events/_upcoming_series_card.html` and `_upcoming_event_card.html` on disk specify `border-border` only. Not chased, per instructions.

Re-verify findings 8 and 12 after the other session merges.

## Verdict: should both `/workshops` and `/workshops/catalog` exist?

Yes — keep both, because they do different jobs badly when merged, but each currently does its own job only halfway.

Evidence: `/workshops` desktop full (screenshot 3) vs `/workshops/catalog` desktop full (screenshot 6). Both render the shared partial `templates/content/_workshops_catalog.html`; the split lives in `content/views/workshops.py:654-697` (`workshops_list` passes `show_filters=False, limit=3`; `workshops_catalog` passes `show_filters=True`).

| Aspect | `/workshops` (landing) | `/workshops/catalog` (archive) |
|---|---|---|
| Job | Nav front door: orient, sell, route | Workhorse: filter and find |
| Header | Hero: eyebrow, h1, 2 paragraphs, 2 CTAs, 3 value cards | Compact: eyebrow `Archive`, h2-scale heading, 1 paragraph |
| Content | 3 newest cards | Full grid (28 cards in current data) |
| Filters | None | Access pills + Topics box (24 chips) + Technologies box (12 chips) |
| In nav | Yes (`Resources > Workshops`, `templates/includes/header.html:58`) | No — reachable only via the two landing CTAs |

Why not merge: the catalog's two facet boxes (about 320px tall on desktop, more than a full screen on mobile) would destroy a landing fold, and the landing's marketing hero would push the archive's grid below two screens. The split is the right structure.

What must change for the split to earn its keep:

- Each direction needs exactly one link. Today the landing has two stacked CTAs to the same catalog (`Browse all workshops` at `templates/content/workshops_list.html:24` and `View all workshops` at `templates/content/_workshops_catalog.html:53`) within ~1.5 screens — redundant. Keep the hero CTA, keep the section-level `View all workshops` (it sits next to the cards it completes), but the two labels should not read as two different destinations (copy scope).
- The catalog has no route back to the landing other than global nav, which is fine; but the catalog is invisible to a visitor who skips the landing hero. Since the catalog is the workhorse, see open PM question 1 about which URL the nav item should target.
- The landing preview and archive currently show the same three newest cards at the top — a visitor clicking `Browse all workshops` sees the exact same first row again, which reads as a broken link. Differentiating the catalog fold (filters visible, more rows) mitigates this; finding 2 does that.

## 1. Above-the-fold, anonymous first-time visitor

### `/workshops`

- Desktop 1280x900 (screenshot 1): eyebrow, h1, two lead paragraphs (~95 words), CTA row, three value-prop cards. Zero actual workshop content in the fold. The hero copy is capped at `max-w-3xl` (`templates/content/workshops_list.html:12`) so roughly the right 40% of the frame is empty for the first ~550px. Single-column hero is per `_docs/design-system.md` Hero Layout, but two lead paragraphs is one more than the eyebrow/H1/lead/CTA order allows.
- Pixel 7 393x851 (screenshot 2): the two paragraphs consume the entire fold; the primary CTA lands at the very bottom edge; value cards and all workshop cards are below the fold. A first-time mobile visitor sees only text.

### `/workshops/catalog`

- Desktop 1280x900 (screenshot 4): header, access pills, the full Topics box, the full Technologies box, and only the top ~120px of the first card row. Filters outweigh content roughly 3:1 in the fold.
- Pixel 7 393x851 (screenshot 5): the fold ends inside the Topics chip wall. No workshop card is visible; the visitor must scroll past ~36 chips (roughly 1.5 screens) before the first card. Full mobile page is ~12,270px tall.

### `/events`

- Desktop 1280x900 (screenshot 7): header, both control rows, `Upcoming` section heading, and the first event card fully visible. This is the best fold of the three pages.
- Pixel 7 393x851 (screenshot 8): header, lead, both control rows, `Upcoming` heading, and the top of the first card. Acceptable; the two stacked pill rows cost ~130px.

## 2. Hierarchy, card density, media, grid rhythm

1. Workshop grid rhythm is correct at the frame level: `grid gap-6 sm:grid-cols-2 lg:grid-cols-3` (`templates/content/_workshops_catalog.html:153`) matches the design-system `gap-6` card-grid rule, and cards use `h-full flex-col` so row heights equalize.
2. Interior raggedness is the real issue. Card sections are all optional (description, Tools, Includes, Topics), so a sparse card (for example `QA Include Workshop 1101`, row 2 of screenshot 6) renders title + one line + one chip, then a ~200px void before the bottom edge, next to fully loaded neighbors. The Topics footer sits outside the anchor at the card bottom (`_workshops_catalog.html:245-265`), which correctly pins it, but cards without topics leave the void inside the anchor instead.
3. Media: in the current dataset no workshop has `card_image_url`, so no card renders a media band — consistent with the conditional-explicit policy in `_docs/design-system.md` Card Media Slots (no fallback wall, no reserved `aspect-video`). The moment some workshops gain covers, rows will mix media and no-media cards; with `items-stretch` rows the coverless cards will stretch and the void problem in point 2 doubles. This is the accepted cost of the documented policy; it reads acceptably as long as covers cluster (newest first) rather than alternate.
4. Chip min-height is inconsistent within one card: tools `min-h-[30px]` (`_workshops_catalog.html:202`), deliverables `min-h-[32px]` (line 217), topic overflow `min-h-[36px]` (line 261), topic links `min-h-[32px]` (line 252). On `/events?filter=past` the same role (tag chip) uses `min-h-[44px]` (`templates/events/events_list.html:190,193`), which the design-system Tap Targets table explicitly exempts tag chips from. Four sizes for one visual role.
5. Hero CTAs on the landing are hand-rolled with the forbidden `px-5 py-2.5` pair (`templates/content/workshops_list.html:24,28`) instead of `{% button_classes %}`; they also use `focus-visible:ring-ring` where the button tag uses `ring-accent`.
6. Section rhythm differs across the three sibling nav pages: `/workshops` sections use `py-10 sm:py-14 lg:py-16` (`workshops_list.html:9`, `_workshops_catalog.html:6`); `/events` uses the reader/detail rhythm `py-8 sm:py-16 lg:py-24` (`events_list.html:11`). Neither is the sanctioned marketing rhythm `py-12 sm:py-20 lg:py-28`. Flag for a design-system ruling on whether nav landing pages count as marketing sections (see open PM questions).

## 3. `/events`: upcoming vs past, `?filter=past`, single vs series cards

1. Upcoming vs past distinction relies on two `h2` section headings with different icons (`events_list.html:75-78` calendar/accent vs `:105-112` archive/muted) plus badge noise on past rows (`Recording available`, `Workshop`, tier). Row anatomy is otherwise identical full-width cards, so when scrolling the boundary is easy to miss. The stronger problem is proportion: in current data the default view is 2 upcoming rows against 20 past rows plus `Page 1 of 3` pagination — the nav landing reads as an archive with a small live section on top, inverted from what an events landing should be.
2. Full-width single-column rows at 1280px put the trailing arrow ~1,100px from the text block; each past row is ~90% whitespace on the right at desktop. Density is fine on mobile.
3. `?filter=past` as a destination (screenshot 9): it is more coherent than a bare filtered list — it has its own section heading (`Past event recordings`), an explainer paragraph (`events_list.html:117-119`), richer cards with a `Watch recording` primary CTA and tag chips, tag filtering, and pagination. What keeps it feeling like a filtered list rather than a page: the H1 block above it still says `Live community events` with the live-sessions lead, and the only above-the-fold signal of where you landed is the third pill being active. A visitor arriving from nav `Past Recordings` (`templates/includes/header.html:42`) sees a hero about live events. Verdict: keep it as a filter-mode of `/events` (one URL space, shared chrome is right), but swap the header copy block by `filter_mode` so the destination names itself — structural change, copy TBD by the copy agent.
4. Single vs series cards side by side (rendered state, screenshots 7 and 10): the single event card starts with its badge row; the series occurrence card starts with an accent `Series:` link line above the badge row, and in captures also carried an accent border. The two shapes therefore misalign at every horizontal line: badge rows, titles, and date rows sit at different y-offsets in adjacent cards, and the accent border makes the series card read as selected or featured rather than merely grouped. The on-disk templates document the intended fix (identical anatomy); see the churn note. Whichever version wins, the acceptance bar should be: adjacent upcoming cards align badge row to badge row and title to title.

## 4. Tier gating and locked-state treatment

1. Vocabulary and badges are compliant: green check `Free` / `Free with sign-in`, accent lock `Basic or above` / `Main or above` via `{% member_access_badge %}` — matches the component index and gate vocabulary in `_docs/design-system.md`.
2. For an anonymous visitor the catalog reads honestly: roughly half the cards carry the accent lock, the `Free` filter pill offers an immediate self-serve path, and the landing hero pairs `Browse all workshops` with `View membership options`. This motivates rather than frustrates: free content is discoverable in one tap, and locked cards still show their full Includes/Topics anatomy so the value of upgrading is visible.
3. The gap is signed-in members: free and premium captures are pixel-identical on all four URLs (only header chrome and timezone suffixes differ from anonymous). A premium member still sees `Basic or above` lock badges everywhere — the lock icon reads as locked-for-you, when everything on the page is actually included in their plan. There is no `Included in your plan` state. This is a product/design-system decision, not a template fix (open PM question 3).
4. Locked cards link into detail pages where `_gated_access_card.html` takes over — correct; no dead-end lock at the card level.

## 5. Empty states

1. `/events` upcoming-empty is the critical one (page is frequently empty between cohorts) and it is a hand-rolled box: `templates/events/events_list.html:94-98` renders a bespoke `p-8 text-center` card with icon and one line, not `{% member_empty_state %}`. This violates the empty-state ownership rule and, worse, has no CTA: on `/events?filter=upcoming` between cohorts the entire page under the pills is this box — a nav destination that dead-ends. It should route to the two journeys that exist when nothing is scheduled: past recordings and the calendar subscribe.
2. Past-side empty states are compliant: `events_list.html:290-296` uses `{% member_empty_state %}` with fresh/filter variants and a clear-filter CTA.
3. Workshops empty states are compliant: `_workshops_catalog.html:271-277` covers fresh and filter-zero with `View all workshops` CTA.

## 6. Recommendations (ranked by impact)

### R1. Collapse the catalog facet boxes behind disclosures

`templates/content/_workshops_catalog.html:60-109`. Biggest fold win on both viewports; puts cards on the first screen of the archive. Wrap each facet box body in a `<details>` (or cap chips at one row with a `Show all N topics` toggle). The section-header accordion owner is `templates/includes/_accordion.html`; if PM judges facet boxes outside its role, this needs a documented new pattern (open PM question 4).

```diff
-    <div class="mb-6 rounded-lg border border-border bg-secondary/20 px-4 py-4 sm:px-5" data-testid="workshop-facet-topic">
-      <div class="mb-3 flex flex-col gap-1">
-        <h3 class="text-sm font-semibold text-foreground">Topics</h3>
+    <details class="mb-6 rounded-lg border border-border bg-secondary/20 px-4 py-4 sm:px-5" data-testid="workshop-facet-topic" {% if selected_topic_summary %}open{% endif %}>
+      <summary class="flex min-h-[44px] cursor-pointer items-center justify-between text-sm font-semibold text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background">Topics</summary>
```

(Same treatment for the Technologies box at line 89. Keep a facet auto-open when one of its filters is active.)

### R2. Cap the past list on the `/events` default view

`events/views/pages.py:380-385` and `templates/events/events_list.html:102-287`. In `all` mode render only the 5 most recent past rows and one CTA `View all past event recordings` -> `/events?filter=past` (use `{% button_classes 'secondary' %}`); keep pagination only in `past` mode. Turns the nav landing back into a landing and gives `?filter=past` a real reason to exist as the archive.

### R3. Tighten the `/workshops` hero and fix its CTA chrome

`templates/content/workshops_list.html:17-31`. Drop to one lead paragraph (move the second into a section below the fold or cut — copy agent's call), and replace both hand-rolled CTAs:

```diff
-          <a href="/workshops/catalog" class="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md bg-accent px-5 py-2.5 text-sm font-medium text-accent-foreground transition-colors hover:bg-accent/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2" data-testid="browse-workshops-cta">
+          <a href="/workshops/catalog" class="{% button_classes 'primary' size='lg' extra='w-full sm:w-auto' %}" data-testid="browse-workshops-cta">
             Browse all workshops
             <i data-lucide="arrow-right" class="h-4 w-4"></i>
           </a>
-          <a href="/pricing" class="inline-flex min-h-[44px] items-center justify-center rounded-md border border-border bg-background px-5 py-2.5 text-sm font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2" data-testid="view-membership-options-cta">
+          <a href="/pricing" class="{% button_classes 'secondary' size='lg' extra='w-full sm:w-auto' %}" data-testid="view-membership-options-cta">
             View membership options
           </a>
```

Requires `{% load accounts_extras %}` at the top of the template. Removes the forbidden `px-5 py-2.5` pair and the wrong focus ring token; on mobile this pulls the primary CTA well above the fold edge.

### R4. Replace the upcoming-events empty box with the owned component and CTAs

`templates/events/events_list.html:94-98`:

```diff
-        <div class="rounded-lg border border-border bg-card p-8 text-center">
-          <i data-lucide="calendar" class="mx-auto h-10 w-10 text-muted-foreground"></i>
-          <p class="mt-4 text-muted-foreground">No upcoming events scheduled. Check back soon!</p>
-        </div>
+        {% button_classes 'primary' as upcoming_empty_primary_class %}
+        {% button_classes 'secondary' as upcoming_empty_secondary_class %}
+        {% member_empty_state title='No upcoming events scheduled' body='New sessions are announced between cohorts. Browse recordings or subscribe so the next one lands on your calendar.' icon='calendar' kind='fresh' primary_cta_label='Browse past event recordings' primary_cta_url='/events?filter=past' primary_cta_class=upcoming_empty_primary_class secondary_cta_label='Subscribe to all events' secondary_cta_url='/events/calendar.ics' secondary_cta_class=upcoming_empty_secondary_class %}
```

(Exact secondary URL/behavior should reuse whatever `_subscribe_popover.html` exposes; body copy is the copy agent's.) `{% load accounts_extras %}` is required.

### R5. Re-align single and series cards in Upcoming

`templates/events/_upcoming_event_card.html` / `_upcoming_series_card.html`. Acceptance: adjacent upcoming cards share y-offsets for badge row, title, and date row; the series indicator lives inside the shared anatomy (pill in the badge row plus the muted `Series:` line under the title); border stays `border-border` unless a documented featured state exists. The on-disk versions already encode this — verify after the concurrent session merges rather than editing now.

### R6. Give `?filter=past` its own header block

`templates/events/events_list.html:13-21`: branch the eyebrow/h1/lead on `filter_mode == 'past'` so the nav `Past Recordings` destination names itself above the fold instead of `Live community events`. Structure only; strings from the copy agent.

### R7. Normalize chip min-heights

Pick one compact chip height (the `30-32px` band) for tools/deliverables/topic chips in `_workshops_catalog.html:202,217,252,261` and drop `min-h-[44px]` from the tag chips in `events_list.html:190,193` (tag chips are exempt from the 44px rule per the Tap Targets table).

### R8. Mobile catalog length

After R1 the mobile catalog is still a ~10,000px single column (28 cards). Add pagination (mirror the events pager pattern, `events_list.html:263-287`) or accept the scroll for now; low priority while the count is ~28, worth revisiting past ~40.

### Proposed section order per page

| Page | Order |
|---|---|
| `/workshops` | Hero (eyebrow, h1, one lead, CTA row) -> Latest workshops (3 cards + `View all workshops`) -> Value-prop trio -> Newsletter |
| `/workshops/catalog` | Compact header -> Access pills + collapsed facet disclosures -> Active-filter row -> Grid -> (Pager when added) -> Newsletter |
| `/events` (all) | Header -> View toggle + subscribe -> Filter pills -> Upcoming (or R4 empty state) -> Recent past, capped at 5 + `View all past event recordings` -> Newsletter |
| `/events?filter=past` | Past-specific header (R6) -> Controls -> Explainer -> Tag filter row -> Recording cards -> Pager -> Newsletter |

Rationale for moving the value trio below the cards on `/workshops`: real content is the strongest above-the-fold argument; the trio currently occupies the fold while every actual workshop sits below it.

## Open PM questions

1. Which URL should nav `Resources > Workshops` target — the marketing landing or the catalog? Repeat visitors pay one extra click per visit today. If the nav stays on `/workshops`, the landing must stay lean (R3); if it moves to `/workshops/catalog`, the landing becomes a pure marketing page reached from home/pricing.
2. Should `/events` in `all` mode paginate past events at all once R2 caps them, or is the cap plus the `?filter=past` archive enough?
3. Viewer-aware access badges: should a signed-in member whose tier covers an item see `Included in your plan` (or no lock) instead of `Basic or above`? Requires a design-system change to `member_badges`; currently free and premium members see identical lock badges.
4. Facet disclosure pattern (R1): is `templates/includes/_accordion.html` the right owner for filter facet boxes, or does this need a new documented pattern?
5. Section-rhythm ruling: do nav landing pages (`/workshops`, `/events`) count as marketing sections (mandatory `py-12 sm:py-20 lg:py-28`), or is the current mixed rhythm acceptable? Today the two pages use two different non-marketing rhythms.

## Out of scope

- All copy: hero paragraphs, CTA labels, empty-state strings, `Past Recordings` nav casing (title case vs sentence case), `Check back soon!` tone. A separate agent covers copy.
- `/events/calendar` view and event/workshop detail pages.
- The consent panel (`templates/includes/analytics_consent.html`) overlaying mid-page in default captures — noted only because raw captures in `.tmp/lp-audit-workshops-events/{anon,free,premium}/` include it; clean captures dismiss it.
- The concurrent session's in-flight edits to `_upcoming_event_card.html`, `_upcoming_series_card.html`, `workshops_list.html`, `_workshops_catalog.html` — findings 8 and the churn notes record the discrepancies; nothing was touched.
- Light-theme captures: all captures used dark scheme; both workshop and event surfaces use token classes throughout, no raw hex found in the audited templates, so light-theme risk is low but unverified by screenshot.
