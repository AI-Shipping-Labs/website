# Layout audit: /projects and /interview as nav landing pages

| Field | Value |
|---|---|
| Audit date | 2026-07-21 |
| Pages | `/projects` (Project Ideas), `/interview` (Interview Prep) |
| Viewports | Desktop 1280x900, Pixel 7 393x851 |
| Auth variants | Anonymous and signed-in (`designer-member@example.com`) |
| Screenshot directory | `.tmp/lp-audit-projects/` |
| Server | Dev server `127.0.0.1:8766`, freshly synced content (12 published projects, 6 interview categories) |

Scope: layout only. Copy is covered by a separate audit. Container widths are out of scope by instruction — both pages already use the sanctioned Frame tier (`max-w-7xl px-4 sm:px-6 lg:px-8`) and pass `content/tests/test_container_widths.py`.

Data caveat: content re-synced mid-audit; the first capture had 11 projects, later captures 12 (a `Test Project` appeared). Where a screenshot and template source disagree, the screenshot is authoritative.

## Screenshots

| Capture | URL |
|---|---|
| /projects desktop full page | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/ef6950e6554846f3a96124c9e1d6983e-d9354cd47695a74c.png |
| /projects desktop above the fold | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/79f4e38964e544009193e1f8b6800388-4b25be757500ca80.png |
| /projects Pixel 7 full page | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/6088182223da48b09b695b0fc9a171e6-bca0ae27fcdb8cfa.png |
| /projects Pixel 7 above the fold | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/967cab179ca44829b685c545b7848d28-5b9f285a807f5196.png |
| /interview desktop full page | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/aa15a89b10364787a7d4d30921ba8668-0c78989d672aefa2.png |
| /interview desktop above the fold | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/f487a79cb8d44fb88cd291b7e73794aa-100fb92d2d947984.png |
| /interview Pixel 7 full page | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/87e27f246e2b488690603b6cc595d959-f5713118bf464f42.png |
| /interview Pixel 7 above the fold | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/978fb02227ec4a2da83db728012f8a80-a56deeccdd3d744b.png |
| /projects desktop fold, signed-in member | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/955d80743418469781636a0487f48986-f99418d831b3ebe6.png |
| /interview desktop fold, signed-in member | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/d502094ab51243658818516f8de9e68f-d5b022896372cb8e.png |
| /projects desktop full, dark theme (consent panel visible mid-image is a full-page capture artifact of the fixed-position dialog) | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/a80cdf1e2a7d4b9187b65a2fff39de1a-44ea479ec167098f.png |

Signed-in state changes only the header chrome (avatar, bell, verify banner); both page bodies are byte-identical for anonymous and member visitors. Dark theme renders correctly on both pages; token classes are used throughout.

## Summary

Both pages open with a correct title-first header stack and then drop straight into a card wall with no CTA row, no statement of what AI Shipping Labs is, and no next step until the footer newsletter. `/projects` additionally hides its only wayfinding aid (tags are computed in the view but never rendered) and currently renders a fallback-icon placeholder wall in every card media band. `/interview` is dominated by disabled UI: five of six equal-weight cards are `Coming soon` at `opacity-60`, so the one live destination does not read as the entry point.

## 1. Above the fold, anonymous first-time visitor

### /projects, 1280x900

Visible: header nav, eyebrow, H1 `Pet & Portfolio Project Ideas`, two-line lead, `Filter by difficulty` pills, and the top ~60% of card row 1 — which is three identical rocket-icon fallback bands. Not visible: any card title except partial, any CTA other than the header `Join free`, any explanation of the community. Roughly 145px of empty band sits between the fixed header and the eyebrow (`pt-24` on `main` at `templates/content/projects_list.html:10` plus `lg:py-24` section padding at line 11); that is sanctioned rhythm, but with no CTA row the fold spends its budget on whitespace and placeholder tiles.

### /projects, 393x851

Visible: eyebrow, H1 (two lines), four-line lead, difficulty pills, and one card's media band plus title (`QA Banner Project 815`, which is QA test content — see Bugs). One partially visible card; zero body CTAs; the first real community project is below the fold.

### /interview, 1280x900

Visible: `Back to home` link, eyebrow, H1, lead, and four cards — of which three are greyed-out `Coming soon` cards. The single live card (`Theory Interview Questions`) sits top-left but has the same size, border, and title scale as the disabled cards; only opacity distinguishes them, so the fold reads as a mostly-inactive page.

### /interview, 393x851

Visible: back link, eyebrow, H1, lead, the live Theory card, and the first `Coming soon` card. Proportionally the best fold of the four, but still no CTA and 50% of the visible cards are disabled.

## 2. Hierarchy and scannability

- Header stacks follow the design system: eyebrow `text-sm font-medium uppercase tracking-widest text-accent`, page H1 `text-3xl font-semibold tracking-tight sm:text-4xl` with `text-wrap: balance`, lead `text-lg text-muted-foreground` (`projects_list.html:14-20`, `interview_hub.html:16-22`). Correct classes, correct scale.
- Neither hero completes the sanctioned hero order (eyebrow, H1, lead, CTA row — design system, Hero Layout). The CTA row is missing on both pages, which is the single biggest reason they read as index pages rather than landing pages.
- `/projects` grid: `grid gap-6 sm:grid-cols-2 lg:grid-cols-3` (`projects_list.html:48`) — correct gap and correct card partial reuse (`content/_project_card.html`, titles `text-lg font-semibold leading-snug`, `line-clamp-3`). Card internals are well-ordered: badges, title, author, description clamp, meta icons, tag chips.
- The eye has no entry point on `/projects` in the current dev render because every media band shows the identical rocket fallback (`_project_card.html:10` passes `preview_decorative_fallback=True`; the synced covers point at `https://cdn.example.com/...`, which fails to load, and `_content_preview.html:18` swaps in the fallback via `onerror`). The design system calls a grid of all-fallback bands a defect. In an environment with a working CDN this becomes real covers; see Bugs for the dev-data note.
- At the current 12 items the flat grid is fine; at 50+ items it becomes an undifferentiated four-plus-screen wall with no featured row, no grouping, no pagination, and no sort control. Sort order is implicit `Meta.ordering = ['-date']` (`content/models/project.py:91`), which is why QA/test content dated newest lands in position 1.
- On Pixel 7 the single-column card with a full-width `aspect-video` fallback band makes the page ~6700px tall for 12 cards; over half the scroll distance is empty media band. The mobile carousel convention (design system, Breakpoints and Mobile Carousels) is the documented alternative for a `sm:grid-cols-2` start, but a simpler win is a featured row plus denser remainder — see recommendation 4.
- `/interview` grid: `grid gap-6 sm:grid-cols-2` (`interview_hub.html:25`). Live and coming-soon cards share identical geometry, border, padding (`p-6`), and title scale (`text-lg font-semibold`); the only differentiators are `opacity-60` and title color (`interview_hub.html:28-31`). Hierarchy inverts the product reality: 83% of the page's visual weight is content that cannot be visited.
- The `Back to home` link (`interview_hub.html:12-15`) exists on no sibling index page (`/projects` has none) and pushes the hero down ~44px on a page reached from the top nav, where the logo already serves that role.

## 3. Filtering, sorting, wayfinding

- `/projects` difficulty filter works, keeps `aria-current="page"` on the active pill, has `min-h-[44px]` targets, and shows a `Clear filter` pill when active (`projects_list.html:24-45`). Legible at both viewports; pills wrap correctly at 393px.
- Difficulty pills are ordered alphabetically — `advanced`, `beginner`, `intermediate` — because the view sorts the set (`content/views/pages.py:411`). Progression order (beginner, intermediate, advanced) is the scannable order.
- Tag filtering is built but unreachable: the view computes `all_tags` (45 distinct tags, `pages.py:403-411`) and filters by `?tag=` (`pages.py:418`), yet the template renders no tag control, and the tag chips on each card are static `<span>` elements (`_project_card.html:56`). A visitor cannot narrow to `agents` or `rag` from any UI. Dead context plus dead-end chips.
- One published project has an empty difficulty; it is reachable only in the unfiltered view and silently excluded from every difficulty filter. No count is shown per pill, so the visitor cannot tell.
- No sort control on `/projects` (newest-first only). Acceptable at 12 items; a liability at 50+.
- `/interview` has no filtering and needs none at 6 categories; wayfinding instead suffers from the disabled-card wall (section 2). The live card shows `7 sections` (`interview_hub.html:51-55`) but no question count and no access badge, so a visitor cannot judge depth or cost before clicking.
- Difficulty badges on cards are legible at both viewports and use the sanctioned compact-badge recipe `bg-<color>-500/15 text-<color>-800 dark:text-<color>-400` (`content/models/project.py:113-117`); however `advanced` maps to red, and the tone table reserves red for cancelled/error semantics. See Design-system questions.

## 4. Empty, sparse, and overflow states

- `/projects` empty states are correctly wired through `{% member_empty_state %}` with distinct filter-empty (tags, difficulty) and fresh-empty variants and a `View all projects` CTA (`projects_list.html:53-61`). Verified by template read; not reachable with current data.
- Sparse: `?difficulty=beginner` yields one card in a 3-column grid — a lone card with two empty columns. Acceptable; no fix proposed.
- Overflow: card tag rows slice to 3 chips plus a `+N` counter chip (`_project_card.html:55-60`); chips truncate with `max-w-full truncate`; the `+N` chip is never clipped at 393px. Titles clamp at 3 lines, descriptions at 2. Good.
- `/interview` has no empty state: `interview_hub` raises 404 when no categories exist (`content/views/interview.py:83-84`). A nav-linked page that 404s on a content-sync gap is a poor failure mode; a `{% member_empty_state %}` fresh variant would be safer.
- No horizontal page scroll on either page at either viewport, anonymous or signed-in (measured `scrollWidth - clientWidth` = 0px in all 8 captures).

## 5. Conversion path

- On both pages the only conversion elements are the header `Join free` button and the footer newsletter card. The page bodies contain zero CTAs. On desktop `/projects` the newsletter is ~4 screens down; on mobile ~7 screens.
- Anonymous gated treatment on `/projects` is limited to the `Basic or above` lock badge on the one gated card (`_project_card.html:17-21`, correct `member_access_badge` usage). Nothing on the page explains what a tier is or links to `/pricing`. The interview hub cards carry no access badge at all, despite the design-system rule that every public content card shows one.
- Neither page tells a first-time visitor what AI Shipping Labs is. The lead sentences describe the content collection, not the community; the next step after browsing is undefined. A signed-in members-only affordance also goes unused on `/projects`: the platform has a project-submission endpoint (`content/views/api.py:85`), but the page never invites members to submit.

## 6. Recommendations, ranked by impact

New sections introduced below on these pages should use the marketing rhythm `py-12 sm:py-20 lg:py-28` and the marketing header/action-row pattern from the design system; the existing `py-8 sm:py-16 lg:py-24` section can remain for the grid itself.

### R1. Add a hero CTA row to both pages (high impact, small change)

Completes the sanctioned hero order and puts a conversion action above the fold at both viewports. `templates/content/projects_list.html:18-21` (add after the lead; add `{% load accounts_extras %}` at line 2):

```diff
         <p class="mt-4 text-lg text-muted-foreground">
           Project ideas and real projects from people who've taken courses. End-to-end AI applications and agentic workflows you can learn from and build on.
         </p>
+        <div class="mt-6 flex flex-wrap items-center gap-3">
+          <a href="/accounts/register/" class="{% button_classes 'primary' size='lg' %}">Join free</a>
+          <a href="/pricing" class="{% button_classes 'secondary' size='lg' %}">View pricing</a>
+        </div>
       </div>
```

Same structure in `templates/content/interview_hub.html:20-23`, with the primary CTA deep-linking to the live category (`/interview/theory`) and the secondary being `Join free`. Exact copy is the copy audit's call; the layout requirement is one `lg` primary plus at most one secondary, per the button-size table.

### R2. /interview: split live categories from coming-soon (high impact)

Restructure `interview_hub.html:25-59` into two sections. Live categories keep the current clickable card treatment in the top grid. Coming-soon categories move below an h2 into compact static rows, dropping both `opacity-60` and the equal-weight card geometry:

```diff
-      <div class="grid gap-6 sm:grid-cols-2">
-        {% for cat in categories %}
-        {% if cat.status == "coming-soon" %}
-        <div class="rounded-lg border border-border bg-card p-6 opacity-60">
-          ...
-        </div>
-        {% else %}
-        <a href="/interview/{{ cat.slug }}" class="...">...</a>
-        {% endif %}
-        {% endfor %}
-      </div>
+      <div class="grid gap-6 sm:grid-cols-2">
+        {% for cat in categories %}{% if cat.status != "coming-soon" %}
+        <a href="/interview/{{ cat.slug }}" class="...unchanged live card...">...</a>
+        {% endif %}{% endfor %}
+      </div>
+      <div class="mt-16">
+        <h2 class="text-2xl font-semibold tracking-tight sm:text-3xl">Coming soon</h2>
+        <ul class="mt-6 divide-y divide-border rounded-lg border border-border bg-card">
+          {% for cat in categories %}{% if cat.status == "coming-soon" %}
+          <li class="flex min-h-[44px] items-center justify-between gap-3 px-3 py-2">
+            <div class="min-w-0">
+              <p class="text-base font-semibold leading-snug text-foreground">{{ cat.title }}</p>
+              <p class="mt-1 text-sm text-muted-foreground">{{ cat.description }}</p>
+            </div>
+            <span class="inline-flex items-center gap-1 rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium text-muted-foreground shrink-0">Coming soon</span>
+          </li>
+          {% endif %}{% endfor %}
+        </ul>
+      </div>
```

This fixes the double de-emphasis contrast problem (`text-muted-foreground` at `opacity-60` is below 4.5:1 in the light theme) by using full-opacity muted text, and makes the one live destination visually dominant. Requires the view to pass categories or the template to partition, as sketched.

### R3. /projects: render the tag filter that already exists (high impact)

The view already computes and filters; only the control is missing. After the difficulty block (`projects_list.html:45`), render the clickable tag chips using the canonical clickable-chip class string from the design system:

```diff
       {% endif %}
+      {% if all_tags %}
+      <div class="mb-8">
+        <p class="mb-2 text-sm font-medium text-muted-foreground">Filter by topic</p>
+        <div class="flex flex-wrap gap-1.5">
+          {% for tag in all_tags %}
+          <a href="/projects?tag={{ tag }}{% if current_difficulty %}&difficulty={{ current_difficulty }}{% endif %}"
+             class="inline-flex items-center gap-1 rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-secondary/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background{% if tag in selected_tags %} bg-accent/10 text-accent{% endif %}"
+             {% if tag in selected_tags %}aria-current="true"{% endif %}>{{ tag }}</a>
+          {% endfor %}
+        </div>
+      </div>
+      {% endif %}
```

45 chips wrap into ~3 rows at 1280px and ~6 rows at 393px; if that is too heavy, cap to the top N by count (view change in `content/views/pages.py:410`) — a product decision, see Open questions. Optionally also make the per-card chips (`_project_card.html:56`) links to `?tag=`, which turns dead chips into wayfinding.

### R4. /projects: featured row before the full grid (medium impact)

Give the eye an entry point and keep the page scannable at 50+ items: a 3-up `Featured projects` row (Official-badged or hand-picked) above the grid, then the full grid under a `Smaller section h2` (`text-2xl font-semibold tracking-tight sm:text-3xl`) reading `All projects`. Structural insert between `projects_list.html:45` and `:47`; requires the view to pass a `featured_projects` list. No new card dialect — reuse `content/_project_card.html` for both sections.

### R5. Closing conversion band on both pages (medium impact)

Between the grid and the newsletter include, add a marketing-rhythm section (`border-t border-border bg-card py-12 sm:py-20 lg:py-28`, matching `templates/home.html:376`) with a `Smaller section h2`, one line of body, and a `{% button_classes 'primary' size='lg' %}` CTA. On `/projects` the member variant can point at project submission (see Open questions). Insert after `projects_list.html:63` and `interview_hub.html:61`.

### R6. /projects: order difficulty pills by progression (low impact, trivial)

`content/views/pages.py:411`:

```diff
-    all_difficulties = sorted(all_difficulties)
+    difficulty_order = ['beginner', 'intermediate', 'advanced']
+    all_difficulties = sorted(all_difficulties, key=lambda d: difficulty_order.index(d) if d in difficulty_order else len(difficulty_order))
```

### R7. /interview: drop the back link (low impact, trivial)

Delete `interview_hub.html:12-15`. No sibling nav landing page has one; the fixed header already provides the route home.

### R8. /interview: access badge and richer meta on live cards (low impact)

Add `{% member_access_badge %}` (Free renders as the green check) and a question count to the live card meta row (`interview_hub.html:51-55`), aligning with the every-public-card badge rule and giving the card a scannable value signal.

## Proposed section order

### /projects

1. Hero: eyebrow, H1, lead, CTA row (R1).
2. Wayfinding: difficulty pills in progression order (R6), then tag chips (R3).
3. Featured projects, 3-up (R4).
4. All projects grid under a smaller section h2 (existing grid, unchanged cards).
5. Conversion band (R5).
6. Newsletter, footer (existing).

### /interview

1. Hero: eyebrow, H1, lead, CTA row deep-linking to the live category (R1, R7 removes the back link).
2. Live categories grid with access badges and counts (R2, R8).
3. Coming soon: compact list rows under a smaller section h2 (R2).
4. Conversion band (R5).
5. Newsletter, footer (existing).

## Bugs and data issues (noted, not fixed)

- QA/test content is published and ranked first on `/projects`: `QA Banner Project 815` (slug `qa-banner-project-815`, banner URL `https://cdn.example.com/banners/project/qa-815.jpg`) and `Test Project` both render as real cards. Newest-first ordering (`content/models/project.py:91`) puts them at the top. If these exist in the production content repo they are visible to real visitors; they should be unpublished or excluded from sync.
- Dev fallback wall: all synced project covers point at `cdn.example.com`, every image 404s, and the `onerror` handler in `templates/content/_content_preview.html:18` swaps in the rocket fallback — producing the exact all-fallback grid the design system labels a defect. Verify production covers resolve; if many real projects lack covers, revisit the Projects media policy.
- One published project has an empty `difficulty`, making it unreachable through the difficulty filter with no indication.
- `/interview` 404s when categories are absent (`content/views/interview.py:83-84`) instead of showing an empty state.
- Coming-soon card text at `opacity-60` over `text-muted-foreground` (`interview_hub.html:28-32`) falls below the 4.5:1 contrast expectation in the light theme.

## Open product decisions

- Interview hub: show coming-soon categories at all, and if so how many? Hiding them leaves a one-card page; the compact-list treatment (R2) is the layout hedge, but visibility is a roadmap-communication call.
- Tag filter density: all 45 tags versus a curated/top-N subset with a `More` affordance.
- Featured projects source: `Official` badge, a curated flag in the content repo, or manual Studio selection. Content-repo-derived is most consistent with the content architecture.
- Conversion band actions per audience: anonymous (join/pricing) versus signed-in member (`Submit your project` using the existing `submit_project` endpoint) — needs a product call on whether submission is ready to promote.
- Hero CTA destinations and copy (owned by the copy audit).

## Design-system change candidates

- The `/projects` difficulty filter pills use a `border border-border bg-card ... hover:border-accent/50` dialect (`projects_list.html:30,37`) instead of the canonical filter-pill string (`bg-secondary text-muted-foreground hover:bg-secondary/80 hover:text-foreground`). Either migrate them or document the card-outline pill as a sanctioned variant.
- `advanced` difficulty renders in the red badge recipe (`content/models/project.py:117`); the tone table reserves red for cancelled/error. Difficulty is categorical — consider orange or another non-error tone, or document the exception.
- Hybrid index/landing pages: clarify whether hero and conversion-band sections added to Frame index pages use the marketing rhythm (`py-12 sm:py-20 lg:py-28`) while the grid section keeps `py-8 sm:py-16 lg:py-24`, as assumed in section 6.

## Out of scope

- All copy (headline, lead, CTA labels) — separate copy audit.
- Outer container width tiers — just normalized and enforced; both pages compliant.
- `/interview/<slug>` and `/projects/<slug>` detail pages, gating cards on detail views, and the reader experience.
- Backend ordering/featuring implementation beyond the file:line pointers above.
