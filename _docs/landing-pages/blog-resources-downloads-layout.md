# Designer audit - /blog, /resources, /downloads layout

| Field | Value |
|---|---|
| Audit date | 2026-07-21 |
| Pages | `/blog`, `/resources` (Curated Links), `/downloads` |
| Viewports | Desktop 1280x900, Pixel 7 393x851 |
| States | Anonymous both viewports; signed-in (`designer-free@test.com`) desktop |
| Screenshot directory | `.tmp/lp-audit-blog/` (fold crops in `.tmp/lp-audit-blog/folds/`) |
| Theme captured | Dark (capture script default) |

## Screenshots

| Capture | URL |
|---|---|
| `/blog` desktop 1280x900 | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/b4bf2545041e44eba1febd3a79484a60-1a324bdc69e93496.png |
| `/resources` desktop 1280x900 | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/f3e8c2add63e4762a35f12659ce8aaf6-8e691a3c62213895.png |
| `/downloads` desktop 1280x900 | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/815a07c1a9534b7fb9adcbae682c4ee7-2c92ad775b69cf06.png |
| `/blog` Pixel 7 393x851 | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/dcfb5d1092dc4903bcc79ce46a8adeb4-25a8396f5eff3da3.png |
| `/resources` Pixel 7 393x851 | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/177368869c874b78a321ce40b56f9198-b45a69ce62ff1e9d.png |
| `/downloads` Pixel 7 393x851 | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/d26c2d89688a450c803f3ff17c51208f-3818b1192f035c69.png |
| `/downloads` signed-in free, desktop | https://d31nukezbn4e3o.cloudfront.net/2026/07/21/075ec033848f43bab4a98da3e81e8f85-3b9e0508d08b34a9.png |

Gating note: the current dev dataset has zero gated curated links and zero gated downloads, so the only signed-in difference is that the anonymous-only footer newsletter block disappears (`templates/includes/footer.html:2`). The gated card variants audited below were reviewed from template code.

## Summary

All three pages open with a thin eyebrow/h1/lead stack followed immediately by cards; none of them tells a first-time visitor what AI Shipping Labs is or offers anything to do next, and the three sibling pages use three unrelated card shapes, badge conventions, and grid rhythms. The recommended direction is a shared list-page grammar: a hero with a CTA row, one card vocabulary (access badge, clickable tag chips, consistent grid), a working filter row on `/blog`, and an in-body conversion band instead of relying on the footer newsletter.

## 1. Above the fold, anonymous first-time visitor

### /blog

Desktop 1280x900: header, then roughly 210 px of empty space (`pt-24` on `templates/content/blog_list.html:13` plus `lg:py-24` on line 14), eyebrow `BLOG`, h1 `Insights & Updates`, one-line lead, then exactly one article row. That first row currently leads with the grey `file-text` fallback thumbnail because the newest article has no cover. No CTA, no signal of what the site is, no filter UI.

Pixel 7 393x851: header, eyebrow, h1, lead, then the top of card 1 — which is a full-width `aspect-video` empty fallback rectangle (`blog_list.html:44-46`). Zero article titles are visible above the fold on mobile. The first meaningful content pixel is below the fold.

### /resources

Desktop: eyebrow, h1 `Curated links for AI builders`, a five-line lead, then the `Courses` section header and one row of cards. This is the best of the three folds — real content is visible — but there is still no CTA and the lead is a wall of qualifying text rather than a reason to stay.

Pixel 7: eyebrow, h1, six-line lead, `Courses` header; the first card is below the fold. The lead paragraph alone consumes about a quarter of the viewport.

### /downloads

Desktop: eyebrow, h1, lead, then a single half-width card (`QA Banner Download 815` — QA test data leaking into the public catalog) followed by roughly 900 px of empty page before the newsletter band. Reads as a broken or abandoned page.

Pixel 7: same, one card, then empty space. The page is 2365 px tall with one item.

## 2. Hierarchy and scannability

### Heading scale

- Page h1s are the correct Frame-tier pattern (`text-3xl font-semibold tracking-tight sm:text-4xl`) on all three pages. Good.
- `/resources` category headers use `text-xl font-semibold` (`templates/content/collection_list.html:35`). This size is not in the typography scale; the smaller section h2 is `text-2xl font-semibold tracking-tight sm:text-3xl`. At `text-xl` the category header is barely larger than the `text-lg` card titles directly under it, so section boundaries are weak.
- `/blog` h1 copy `Insights & Updates` is title case; the design system requires sentence case for headings. Wording itself is the copy agent's scope; the casing rule is flagged here.

### Three card patterns across three sibling pages

The three pages sit in the same `Resources` nav dropdown and are the same page type (Frame-tier index), but each invents its own card:

| Aspect | `/blog` (`blog_list.html:36-93`) | `/resources` (`collection_list.html:46-113`) | `/downloads` (`downloads_list.html:34-54`) |
|---|---|---|---|
| Shape | Full-width horizontal row, `space-y-8` stack | Text card, `sm:grid-cols-2 lg:grid-cols-3` grid | Text card, `sm:grid-cols-2` grid |
| Media | Thumbnail + fallback band | None | None |
| Access signal | `member_access_badge` | None on open cards; two lock icons on gated cards | `member_tier_badge` + separate lock icon |
| Title | `text-lg` h2, no clamp | `text-lg` h3, no clamp | `text-lg` h2, `line-clamp-3` |
| Tags | Clickable chips, slice 3 + overflow badge | Static `<span>` chips, slice 3 | Clickable chips, slice 3 + overflow badge |
| Card padding | `p-4 sm:p-6` | `p-4 sm:p-5` | `p-4 sm:p-5` |
| In-card CTA | Trailing arrow icon (desktop only) | `external-link` icon | `View download` text link |

This is three different vocabularies for the same job. A visitor moving between the three pages has to re-learn what a card is each time. Recommendation: keep the row layout for `/blog` (it suits date-ordered editorial) and the grid for the other two, but unify the badge, tag-chip, title, and padding grammar (see R4).

### Design-system violations in the card patterns

- `blog_list.html:39-47` renders a per-card media band with a hand-rolled fallback. The Card Media Slots table in `_docs/design-system.md` says blog list rows: do not render a media band. It also bypasses the owning partial `templates/content/_content_preview.html` and ships a fallback wall (2 of 16 articles are coverless today; every coverless article renders the grey `file-text` box, which the design system calls a defect). The covers that do exist are mostly title-card images, so at `sm:w-48 sm:h-32` they render as unreadable micro-text that duplicates the adjacent h2.
- `downloads_list.html:39-40` uses `member_tier_badge` plus a separate lock icon. The component index mandates `member_access_badge` on every public/member content card (green check for free, accent lock for paid), which `/blog` already uses correctly.
- `collection_list.html` gated card (75-113): no tier badge at all, the lock icon appears twice (inline in the title at line 85 and top-right at line 87), and the reveal CTA is a hand-rolled button (line 108) instead of `{% button_classes %}`, with title-case label `View Plans` (should be `View plans`).
- `collection_list.html:41` uses `items-start` plus `self-start` on cards, so every row is ragged — visible in the desktop screenshot as uneven card bottoms in each row. This was presumably to keep the gated-card expansion from stretching siblings, but with equal-height cards the grid would read far calmer.

### Behaviour at scale

- `/blog` with 16 articles is already 5716 px (desktop) / 10181 px (mobile). Each mobile card costs ~640 px, most of it the `aspect-video` media band. At 50+ articles the page passes 30000 px on mobile with no pagination, no year/month grouping, and no filter row.
- `/resources` renders whole categories in source order; at 50+ links per category there is no in-page navigation between category sections and no way to jump past a long section.
- `/downloads` at 50+ items would be a `sm:grid-cols-2` wall of same-looking text cards with the filter row as the only aid; at 1280 px two columns of ~600 px-wide text cards is low-density for scanning — `lg:grid-cols-3` fits the content better.

## 3. Filtering, sorting, tags, pagination, wayfinding

| Capability | `/blog` | `/resources` | `/downloads` |
|---|---|---|---|
| Filter pill row | None (51 tags exist) | None | Canonical row (`downloads_list.html:22-29`), but 0 tags in data so it never renders |
| Card tag chips | Clickable, add tag filter | Static spans, not links | Clickable, add tag filter |
| Active-filter indicator | None | None | `All` + selected pill, `aria-current` — correct |
| Clear filters | Only via filter-empty state | Only via filter-empty state | `All` pill |
| Sorting | Fixed | Fixed category + sort_order | Fixed |
| Pagination | None (unbounded queryset, `pages.py:331`) | None (`pages.py:456`) | None (`pages.py:578`) |

Specific problems:

- `/blog`: clicking a card tag chip filters the list, but the resulting page gives no indication a filter is active — no pill row, no selected state, no clear control. The only escape is the browser back button or hitting a filter-empty state. With 51 tags this is the page that most needs the canonical filter row `/downloads` already has.
- `/resources`: the view computes `all_tags` and supports `?tag=` filtering (`pages.py:462-480`), but the template renders no filter UI and its card tags are static spans (`collection_list.html:61`). The entire filter code path is unreachable from the UI — dead weight or missing UI, one or the other.
- Wayfinding between the three siblings: nothing on any page acknowledges the other two exist. The nav dropdown is the only link between `Blog`, `Curated Links`, and `Downloads`.
- Mobile filter row (`/downloads` pattern): a flat `flex-wrap` list of 44 px pills is fine at 5 tags but will stack many rows deep at 20+; acceptable for now, worth a scroll-row or top-N treatment when tags grow.

## 4. Empty, sparse, and overflow states

- Empty states: all three templates correctly use `{% member_empty_state %}` with distinct fresh and filter variants (`blog_list.html:96-102`, `collection_list.html:120-126`, `downloads_list.html:57-64`). Correct per the design system.
- Sparse state, `/downloads` today: one card in a two-column grid leaves the right half of the frame empty and ~900 px of dead vertical space. There is no between state — the design assumes a populated grid. The single item is QA test data (`QA Banner Download 815`), which also means the real production sparse state is unverified.
- Sparse state, `/resources`: category sections hide when empty (`collection_list.html:512-518` view-side), which is right; but see the category bug below — most of the catalog is currently invisible.
- Overflow: tag chips slice to 3 with a `+N` overflow badge and `aria-label` on all pages that render them — good. Blog card titles have no clamp; a very long title wraps and grows the row (acceptable in a row layout). `/downloads` titles clamp at 3 lines with `break-words` — good. `/resources` descriptions clamp at 3 lines — good.
- Data-visibility bug (report only, not fixed here): `collection_list` filters `category__in=['workshops', 'courses', 'articles', 'other']` (`content/views/pages.py:462-465`), but the model defines six categories including `tools` and `models` (`content/models/curated_link.py:18-25`). The synced dataset has 41 published links: 21 `tools`, 11 `courses`, 8 `other`, 1 `models`. The page silently drops 22 of 41 links — more than half the catalog, including its largest category. The screenshots show only `Courses` and `Other`.

## 5. Conversion path

- Anonymous visitors get exactly one capture point on all three pages: the footer newsletter block (`templates/includes/footer.html:2-29`), at the very bottom of pages that are 2000-10000+ px tall. There is no in-body CTA, no membership mention, and no `Join free` reinforcement outside the header button.
- `/blog` specifically: an organic-search visitor who lands here sees a heading, a wall of article rows, and (on mobile) an empty grey rectangle first. Nothing above the fold says what AI Shipping Labs is, that it has a community, sprints, or membership, or that a newsletter exists. Newsletter capture is buried roughly 5000 px (desktop) / 9500 px (mobile) down. The reusable `templates/includes/subscribe_form.html` partial (with `heading`/`description`/`redirect_to` parameters) already exists and is used nowhere on these pages.
- `/downloads` is naturally the lead-magnet page (detail pages do email capture), yet the list page itself has no capture or membership framing at all above the footer.
- Signed-in members lose the footer newsletter and get zero page-level next-step guidance on any of the three.

## 6. Recommendations, ranked by impact

All diffs are internal layout only; the Frame-tier outer container on all three pages is correct and untouched.

### R1. Show the whole curated-links catalog (bug, highest impact)

`content/views/pages.py:462` — the category allowlist hides 22 of 41 links. Extend `category_order` to the model's canonical six categories (labels, descriptions, and icons already exist in `content/models/curated_link.py:18-34` and the view's `category_icons` map needs `tools` and `models` entries, both already defined on the model as `category_icon_name`).

```diff
-    category_order = ['workshops', 'courses', 'articles', 'other']
+    category_order = ['workshops', 'courses', 'articles', 'tools', 'models', 'other']
```

If the omission of `tools`/`models` was a deliberate product decision, it needs a visible trace; nothing in the template or view comments says so.

### R2. Remove the blog media band (design-system compliance, biggest mobile win)

`templates/content/blog_list.html:38-48` — the Card Media Slots contract says blog list rows render no media band, and the current band produces a fallback wall for coverless posts plus unreadable micro-covers for the rest. Removing it cuts ~200 px per mobile card (~3000 px of page height today) and puts article titles above the fold on Pixel 7.

```diff
-            <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
-              <div class="sm:w-48 sm:flex-shrink-0" data-testid="blog-card-thumbnail">
-                {% if article.cover_image_url %}
-                <img src="{{ article.cover_image_url }}" alt="{{ article.title }}"
-                     class="w-full aspect-video sm:aspect-auto sm:h-32 object-cover rounded-lg border border-border">
-                {% else %}
-                <div class="flex aspect-video w-full items-center justify-center rounded-lg border border-border bg-secondary sm:h-32 sm:aspect-auto" data-testid="blog-card-thumbnail-fallback" aria-hidden="true">
-                  <i data-lucide="file-text" class="h-8 w-8 text-muted-foreground/50"></i>
-                </div>
-                {% endif %}
-              </div>
-              <div class="flex-1">
+            <div class="flex items-start justify-between gap-4">
+              <div class="min-w-0 flex-1">
```

If the PM instead wants covers kept, that is a design-system change (flip the per-type decision to conditional-explicit and route through `_content_preview.html`); do not keep the current hand-rolled band either way.

### R3. Add a hero CTA row to all three pages (landing-page conversion)

Each header block (`blog_list.html:16-24`, `collection_list.html:15-26`, `downloads_list.html:16-20`) follows the sanctioned hero order eyebrow, h1, lead — but stops before the CTA row. Add the fourth element per the Hero Layout section, e.g. on `/blog` after line 23:

```diff
         <p class="mt-4 text-lg text-muted-foreground">
           Articles on AI engineering, production ML, and building real systems.
         </p>
+        {% if not user.is_authenticated %}
+        <div class="mt-6 flex flex-col gap-3 sm:flex-row">
+          <a href="/accounts/register/" class="{% button_classes 'primary' size='lg' extra='w-full sm:w-auto' %}">Join free</a>
+          <a href="/#newsletter" class="{% button_classes 'secondary' size='lg' extra='w-full sm:w-auto' %}">Get the newsletter</a>
+        </div>
+        {% endif %}
```

(`{% load accounts_extras %}` is already present on `downloads_list.html`; add it to the other two.) Exact copy and destinations are the copy agent's and PM's call; the layout point is that a CTA row belongs in the hero on all three pages.

### R4. Unify the card grammar across the three pages

- `templates/content/downloads_list.html:36-41` — replace the tier badge + lock pair with the mandated access badge:

```diff
-              {% member_label_badge item.download.file_type_label %}
-              {% if item.download.human_file_size %}<span class="text-xs text-muted-foreground">{{ item.download.human_file_size }}</span>{% endif %}
-              {% member_tier_badge item.download.required_level %}
-              {% if item.download.required_level > 0 %}<i data-lucide="lock" class="h-4 w-4 text-muted-foreground"></i>{% endif %}
+              {% member_label_badge item.download.file_type_label %}
+              {% if item.download.human_file_size %}<span class="text-xs text-muted-foreground">{{ item.download.human_file_size }}</span>{% endif %}
+              {% member_access_badge item.download.required_level testid="download-access-badge" %}
```

- `templates/content/collection_list.html:50` (and the gated variant) — add `{% member_access_badge item.link.required_level %}` above the title, drop the inline title lock at line 85 (keep the top-right one), and make card tags clickable chips using the same `tag_add_url` anchor string as `blog_list.html:86`.
- `templates/content/collection_list.html:108` — replace the hand-rolled CTA with `{% button_classes 'secondary' %}` and sentence case `View plans`.
- `templates/content/downloads_list.html:32` and `collection_list.html:41` — same grid: `grid gap-6 sm:grid-cols-2 lg:grid-cols-3`; on `collection_list.html:41` also drop `items-start` (and `self-start` on lines 47/75) so rows are equal height.
- `templates/content/collection_list.html:35` — category headers to the scale:

```diff
-            <h2 class="text-xl font-semibold text-foreground">{{ category.label }}</h2>
+            <h2 class="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">{{ category.label }}</h2>
```

### R5. Give /blog the canonical filter row and active-filter visibility

Copy the exact pill-row block from `templates/content/downloads_list.html:22-29` into `blog_list.html` after the header block (line 24), swapping `/downloads` for `/blog` and the aria-label wording. With 51 tags a full row is too long, so render a curated/top-N subset or the selected tags plus top tags — see PM question Q3. Minimum viable version: always render the `All` pill plus any `selected_tags` so an active filter is visible and clearable.

### R6. Add an in-body conversion band to /blog (and reuse it on the other two)

Insert `{% include "includes/subscribe_form.html" %}` (already parameterized, `templates/includes/subscribe_form.html`) as a full-width band after the article list and before the footer for anonymous visitors, so an organic-search reader who scrolls the list hits capture before the footer. On `/downloads` pass `redirect_to` to feed the existing lead-magnet flow. Placement (after N cards vs after the list) is a product decision — Q4.

### R7. Pagination for /blog and /downloads

Unbounded querysets (`pages.py:331`, `578`). Introduce pagination at ~20 items per page. There is no existing member-page pager partial to copy on these surfaces, so this needs a pattern decision — Q5.

### Proposed section order per page

| Order | `/blog` | `/resources` | `/downloads` |
|---|---|---|---|
| 1 | Hero: eyebrow, h1, lead, CTA row | Hero: eyebrow, h1, shorter lead, CTA row | Hero: eyebrow, h1, lead, CTA row |
| 2 | Tag filter pill row | Category jump chips (in-page anchors to the six sections) | Tag filter pill row (when tags exist) |
| 3 | Article rows, paginated | All six category sections, unified grid | Download grid `sm:grid-cols-2 lg:grid-cols-3`, paginated |
| 4 | Newsletter band (`subscribe_form.html`) | Newsletter band | Newsletter band with `redirect_to` |
| 5 | Footer | Footer | Footer |

## Open PM questions

- Q1. `/resources` categories: was hiding `tools` and `models` intentional? If yes, document it; if no, R1 is a one-line fix that doubles the visible catalog.
- Q2. Blog covers: comply with the current design-system decision (no media band, R2) or change the decision to conditional-explicit covers via `_content_preview.html`? The latter is a design-system change.
- Q3. Blog tag taxonomy: 51 tags is too many for a pill row. Curate a top-level tag set for the filter row, or render top-N by article count?
- Q4. Conversion band placement on `/blog`: after the full list, or interleaved after the first ~6 rows? Interleaving converts better for long lists but interrupts scanning.
- Q5. Pagination pattern: no pager exists on these member/public list pages; choosing page size and pager chrome is a new-pattern decision that should be specced once and reused.
- Q6. Cross-linking the three siblings (e.g. a small "More resources" row linking the other two pages) — worth it, or does the nav dropdown suffice?

## Out of scope

- All heading/lead/CTA wording, including replacing `Insights & Updates` — separate copy agent owns wording; only the sentence-case rule is flagged here.
- `QA Banner Download 815` test data in the dev catalog — data hygiene, not layout; flagged so someone verifies production is clean.
- The analytics consent banner overlapping content in screenshots — separate surface, not part of these pages' layout.
- Download detail-page email capture flow — only its list-page entry point is discussed.
- Header/footer chrome and the nav dropdown contents themselves.
