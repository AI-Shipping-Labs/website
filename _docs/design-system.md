# AI Shipping Labs Design System

This is the authoritative UI contract for the Django template application. It is imperative, not an inventory or redesign brief: new and edited UI must follow the ownership, semantics, and class-string rules below.

## Document Scope

This reference documents presentation contracts only. It does not introduce production API work, models, migrations, views, URLs, integrations, settings, agent definitions, lint rules, or template migrations. Implementation changes require their own scoped issues. The workflow-enforcement and lint-ratchet work that built on this contract has shipped.

## Stack and Sources of Truth

- Templates: Django templates under `templates/`, with public pages extending `templates/base.html`.
- Tailwind: v3.4.17 is pinned and compiled with `make css-build` from
  `assets/css/tailwind.css`; `templates/base.html` loads the generated bundle
  through Django staticfiles.
- Tokens: HSL CSS variables live in `assets/css/tailwind.css` on `:root` and `.dark`.
- Theme mapping: `tailwind.config.js` maps variables to `bg-*`, `text-*`,
  `border-*`, `ring-*`, and related utilities. It scans complete utility
  strings in runtime templates, Python, and first-party JavaScript. Do not
  construct utility fragments dynamically or add broad safelists.
- Fonts: Inter for UI text and JetBrains Mono for code, loaded from Google
  Fonts with `display=optional`; do not reintroduce a late font swap that can
  shift the faster build-time-CSS first paint.
- Icons: Lucide via CDN with `<i data-lucide="...">`; brand marks such as GitHub use inline SVG partials.
- Theme: a blocking head script reads `localStorage['theme']` or `prefers-color-scheme` and toggles `<html class="dark">`.

## Color Tokens

All theme-aware color should come from the variables in
`assets/css/tailwind.css`. Use opacity slash syntax such as `bg-accent/10`,
`border-accent/30`, and `text-muted-foreground/40` instead of raw hex values.

| Variable | Light | Dark | Tailwind classes | Primary use |
|---|---:|---:|---|---|
| `--background` | `0 0% 100%` | `0 0% 4%` | `bg-background`, `text-background` | Page background, ring offsets |
| `--foreground` | `0 0% 9%` | `0 0% 98%` | `text-foreground`, `bg-foreground` | Primary text |
| `--card` | `0 0% 98%` | `0 0% 7%` | `bg-card` | Cards and alternate surfaces |
| `--card-foreground` | `0 0% 9%` | `0 0% 98%` | `text-card-foreground` | Text on card surfaces |
| `--secondary` | `0 0% 96%` | `0 0% 12%` | `bg-secondary` | Secondary buttons, tags, code |
| `--secondary-foreground` | `0 0% 9%` | `0 0% 98%` | `text-secondary-foreground` | Text on secondary surfaces |
| `--muted` | `0 0% 96%` | `0 0% 15%` | `bg-muted` | Subtle backgrounds and hover fills |
| `--muted-foreground` | `0 0% 45%` | `0 0% 60%` | `text-muted-foreground` | Body copy, captions, metadata |
| `--accent` | `75 100% 35%` | `75 100% 50%` | `bg-accent`, `text-accent`, `border-accent`, `ring-accent` | Brand actions, links, active states |
| `--accent-foreground` | `0 0% 100%` | `0 0% 4%` | `text-accent-foreground` | Text on filled accent buttons |
| `--destructive` | `0 84.2% 60.2%` | `0 84.2% 60.2%` | `text-destructive`, `bg-destructive` | Error/destructive states |
| `--border` | `0 0% 90%` | `0 0% 28%` | `border-border` | Hairline borders |
| `--input` | `0 0% 90%` | `0 0% 28%` | `border-input` | Form input borders |
| `--ring` | `45 100% 41%` | `45 100% 51%` | `ring-ring` | Tokenized focus rings when not using accent |
| `--hero-gradient-mid` | `0 0% 96%` | `0 0% 12%` | `.hero-gradient` CSS | Hero gradient midpoint |
| `--hero-gradient-end` | `0 0% 100%` | `0 0% 4%` | `.hero-gradient` CSS | Hero gradient end |

Raw Tailwind palette colors are allowed only when their meaning matches the tone table in [Pills, Badges, and Chips](#pills-badges-and-chips). Existing usage is not precedent by itself; check its semantic meaning. Theme-aware non-state UI continues to use design tokens.

Compact semantic status and categorical badges on public/member surfaces use
`bg-<color>-500/15 text-<color>-800 dark:text-<color>-400`. Their normal-size
text must reach at least 4.5:1 against the browser-computed, alpha-composited
effective background in both themes. A raw `text-<color>-600` class is not
sufficient contrast evidence. This recipe is owned by
`content.templatetags.member_badges` where a shared tone exists; alerts,
buttons, links, standalone headings, and large decorative/status icons retain
their own component-specific border, focus, live-region, and contrast recipes
instead of copying badge colors.

## Typography Scale

Inter uses weights 300 through 700. Headings always use `font-semibold`, never `font-bold`. Do not copy `font-bold` from a legacy page; fix a touched legacy heading instead. Every heading at `text-2xl` or larger includes `tracking-tight`. Eyebrows use `tracking-widest`, never `tracking-wider`.

| Role | Common class pattern |
|---|---|
| Homepage hero h1 | `text-4xl font-semibold tracking-tight sm:text-5xl lg:text-6xl` |
| Page h1 | `text-3xl font-semibold tracking-tight sm:text-4xl` |
| Detail hero h1 | `text-3xl font-semibold tracking-tight sm:text-4xl lg:text-5xl` |
| Compact page h1 | `text-2xl font-semibold tracking-tight sm:text-3xl` |
| Section h2 | `text-3xl font-semibold tracking-tight sm:text-4xl` |
| Smaller section h2 | `text-2xl font-semibold tracking-tight sm:text-3xl` |
| Large card title | `text-lg font-semibold text-foreground` |
| Compact card title | `text-base font-semibold leading-snug text-foreground` |
| Body lead | `text-lg leading-relaxed text-muted-foreground` |
| Body default | `text-base leading-relaxed text-muted-foreground` |
| Body small | `text-sm text-muted-foreground` |
| Caption/meta | `text-xs text-muted-foreground` |
| Eyebrow | `text-sm font-medium uppercase tracking-widest text-accent` |

Use `tracking-tight` on `text-2xl` and larger headings. Many hero and section headings also use `style="text-wrap: balance;"` for calmer two-line wrapping.

Markdown content is styled by `.prose` in `templates/base.html`. Workshop/tutorial contexts can add `.prose-tight` when list rhythm needs to match compact rows.

### Casing

Use sentence case on public, member, and Studio surfaces for headings, buttons, tab titles, HTML `<title>` blocks, pre-transform eyebrow copy, empty-state titles, and badge labels.

- `Create account`, not `Create Account`.
- `Sign in`, not `Sign In`.
- `View pricing`, not `View Pricing`.
- Proper nouns and initialisms keep their casing, including `GitHub`, `CSV`, and `Slack`.

This extends the Studio CTA convention below. It does not replace Studio's established noun and verb rules.

## Date and Time Vocabulary

Templates use semantic date/time helpers from `accounts.templatetags.date_formatting`, registered as template builtins. Do not add raw `|date:"..."` display filters in templates; `accounts.tests.test_template_date_vocabulary.TemplateDateVocabularyGuardTest` fails when a new raw template date filter appears.

| Context | Helper | Format | Example | Use |
|---|---|---|---|---|
| Member full date | `member_full_date` | `F j, Y` | `March 21, 2026` | Detail pages, certificates, cohort starts, and prominent prose dates. |
| Member short date | `member_short_date` | `M j, Y` | `Mar 21, 2026` | Public/member cards and list rows. |
| Member compact date | `member_compact_date` | `M j` | `Mar 21` | Dense dashboard/list chips where the year is clear from context. |
| Member short datetime | `member_short_datetime` | `M j, Y H:i` | `Mar 21, 2026 16:00` | Non-event member timestamps where recipient timezone conversion is not meaningful. |
| Operator date | `operator_date` | `Y-m-d` | `2026-03-21` | Studio tables, CRM rows, plan metadata, imports, logs, and machine-readable admin views. |
| Operator datetime | `operator_datetime` | `Y-m-d H:i` | `2026-03-21 16:00` | Studio/admin timestamps where minute precision is enough. |
| Operator datetime with seconds | `operator_datetime_seconds` | `Y-m-d H:i:s` | `2026-03-21 16:00:07` | Worker/import/debug surfaces that need second precision. |
| Operator datetime with timezone | `operator_datetime_tz` | `Y-m-d H:i:s T` | `2026-03-21 16:00:07 UTC` | Debug tooltips/titles where the timezone token is intentionally shown. |
| Form date value | `form_date_value` | `Y-m-d` | `2026-03-21` | HTML `<input type="date">` values and browser/form-control payloads. This is value serialization, not display copy. |
| Split operator time | `operator_time` | `H:i` | `16:00` | Time-only value in a documented split date/time cell or form-control value. Do not use as a standalone display format. |

Event and session start times are not ordinary date formatting. Visible event/session datetimes that depend on the signed-in viewer or registered recipient must call `accounts.services.timezones.format_user_datetime` directly or use the `user_event_datetime` template tag, which delegates to it and appends an unambiguous timezone token.

Documented event/session exceptions:

- Anonymous public event detail/list surfaces use `events.services.display_time.build_event_time_display`, which renders a site fallback timezone server-side and lets browser JavaScript replace it with the visitor's detected timezone when allowed.
- Public event series cards and rows use `event_source_short_datetime` / `event_source_full_datetime` for anonymous source-timezone display. Signed-in viewer-specific series/session rows use `user_event_datetime`.
- Multi-zone broadcast strings use `events.services.display_time.format_event_tz_strip`; they intentionally show a fixed broadcast strip rather than a recipient timezone.
- Studio/operator event rows use operator helpers because staff tables are operational records, not member-local event reminders.

## Spacing and Layout

Tailwind's default 4px scale is the baseline. Bare classes are mobile values; `sm:`, `md:`, and `lg:` progressively enhance wider screens.

Standard horizontal frame:

```html
mx-auto max-w-7xl px-4 sm:px-6 lg:px-8
mx-auto max-w-5xl px-4 sm:px-6 lg:px-8
mx-auto max-w-3xl px-4 sm:px-6 lg:px-8
mx-auto max-w-2xl px-4 sm:px-6 lg:px-8
```

Four tiers, chosen by content shape rather than per-page taste. These are the only sanctioned outer page widths; Studio pages use their own admin layout.

| Tier | Class | Use for |
|---|---|---|
| Frame | `max-w-7xl` | Surfaces that visibly consume the full width: 3+ column card grids with enough items to fill them, the 4-column pricing grid, the month calendar, the member dashboard, sidebar-plus-content layouts, and multi-section marketing pages. Matches the header and footer chrome. Being an index page does not by itself earn the Frame — only the content shape does. |
| Detail | `max-w-5xl` | Detail pages with mixed layout: media embed plus metadata plus cards or CTAs (course, plan, poll detail; account; notifications). Also the home for sparse 2-column hubs that read as empty on the right at 7xl. |
| Reader | `max-w-3xl` | Long-form and single-column detail surfaces, including blog/article, tutorial, project, workshop, sprint, event, event-series, and interview; also text-first editorial listings such as blog, workshop catalog, and interview, plus multi-step single-column forms. 48rem keeps the measure near the 65-75ch readable band while leaving code blocks usable width. |
| Narrow | `max-w-2xl` | Terminal status and confirmation interstitials, and single-purpose forms (subscribe, join-state, cancel registration, verify/unsubscribe result, peer review). |

Pick the narrowest sanctioned tier that fits the widest repeated element on the page. Text-first editorial feeds use `max-w-3xl`; mixed row feeds and sparse 2-column hubs use `max-w-5xl`. Reserve the 7xl Frame for genuine grids that fill it.

Listing/index pages follow their content shape: text-first editorial feeds such as `/blog` and `/events` use Reader (`max-w-3xl`); ordinary catalog grids and mixed indexes such as `/courses` and `/projects` use Detail (`max-w-5xl`). The dense Events month calendar is a distinct Detail-width (`max-w-5xl`) surface relative to the 3xl Events rows. A page keeps one outer width throughout its title, controls, populated, filtered, and empty states. See [Content Listing Pages](#content-listing-pages), which is authoritative for those routes.

The outer frame always sets the tier; narrower inner columns (a `max-w-3xl` intro inside a Frame index, a `max-w-md` auth card) are normal and live inside it. A page column may be narrower than the header chrome when its content is single-column or sparse: the shared `px-4 sm:px-6 lg:px-8` gutters keep it left-aligned below the max width and centered above it, exactly like `/about`. A 7xl Frame may still cap individual inner sections, but those inner caps must use sanctioned widths (no `max-w-6xl`).

Enforced by `content/tests/test_container_widths.py`. Rationale, the full route table, and the 2026-07-21 remediation are in [`audits/2026-07-21-container-widths.md`](audits/2026-07-21-container-widths.md).

Common vertical rhythm:

- `py-12 sm:py-20 lg:py-28` is the only marketing-section rhythm, including shared FAQ and newsletter sections rendered inside marketing pages. Alternate marketing rhythms such as `py-16 sm:py-24 lg:py-32` are forbidden.
- Reader/detail sections: `py-8 sm:py-16 lg:py-24`.
- Hero/detail blocks often use `py-16 sm:py-20 lg:py-24`.
- Repeated card grids use `gap-6`; tight operational rows use `gap-4`. Do not use `gap-5`, `gap-8`, or another gap size on a repeated card grid. Purpose-specific page-layout gaps that are not card grids may use the spacing their layout requires.
- Common stack jumps: `mt-1`, `mt-2`, `mt-4`, `mt-6`, `mt-10`, `mt-16`.

Common card padding is bound to the card role, not to the page (see [Cards](#cards)):

- Tier/hero and callout-spotlight cards: `p-5 sm:p-8`.
- Info / static and default testimonial cards: `p-6`.
- Content-catalog cards: `p-4 sm:p-5`; compact-rail cards: `p-4`.
- List rows: `px-3 py-2` with `min-h-[44px]`.
- Studio table cells: `px-4 py-3`.

Card padding is a role decision, not a per-page taste. There is no `p-4 sm:p-6`
card padding: a repeated content card is `p-4 sm:p-5` and a static/testimonial
card is `p-6`.

Public/member page headers use a title-first stack. Marketing collection
sections with one clear destination CTA use a responsive header/action row:
stack the copy and button on mobile, then use `sm:flex-row sm:items-end
sm:justify-between` with a non-growing CTA on wider screens. Render that CTA
with `{% button_classes %}` so it reads as an action, not a continuation of the
intro text. Filters and groups of actions stay below the title and wrap rather
than being pinned opposite it. Narrative discovery links immediately following a
marketing-section intro use
`mt-2 inline-flex items-center gap-2 text-sm font-medium text-accent hover:underline`
plus the canonical focus-visible ring and a trailing `h-4 w-4` decorative
`arrow-right` icon. This rule does not apply to alert/banner dismissal rows,
content/action callout cards, repeated cards, table/list rows, pager rows,
pricing tier-name/meta rows, badge/arrow rows, or other card-internal metadata
layouts. The separate Studio stacked-header contract below remains authoritative
for Studio surfaces.

Variable-height detail cards:

- Do not force related detail content into competing multi-column layouts when sections can have very different content height. A single vertical flow is often clearer than side-by-side blocks with awkward empty space.
- When a section combines explanatory copy with one primary detail card, stack them as two rows: intro/description first, primary card second.
- Inside detail cards, stack variable facts such as dates, duration, status, requirements, and next-step guidance as rows instead of splitting them into equal columns.
- Use compact grouped rows (`gap-3` or `gap-4`) for facts; reserve grids for repeated cards of the same visual weight and predictable height.

Comparison and progress lists:

- Use tables or table-like rows for member progress, status comparisons, reviewer queues, or any surface where users compare the same fields across many records. Avoid two-column card grids for these scenarios because reading order and relative progress become ambiguous.
- Put the current user's own record first when the page is a signed-in member surface. The rest of the rows can follow the product sort order.
- Keep table columns predictable and scannable: identity first, then primary metric/progress, then status, then details/actions.
- Wrap wide tables in `overflow-x-auto` on small screens instead of collapsing them into uneven cards when comparison is the primary task.

## Hero Layout

Page heroes are single-column and use this order: eyebrow, H1, lead, CTA row.

Do not use arbitrary-fraction or two-column heroes for decorative value-point stacks or feature bullets. Move that content into its own full-width section below the hero. A side column is allowed only for a functional artifact the visitor can act on immediately, such as a registration form, video embed, or event-registration card.

## Cards

Cards are a system of five roles. Bind radius, surface, padding, hover, and shadow to the card's ROLE, never to the page it sits on. The same content type must render as the same card on its home preview and its catalog page.

Three principles:

1. Radius drift is role drift. `rounded-lg` is anything repeated in a grid or list; `rounded-xl` is a singular spotlight surface only (the spotlight exception below); `rounded-2xl` is full-page focus panels (out of scope here).
2. Surface is a band-contrast rule. A card's surface must contrast its section band. Default `bg-card`; pass `bg-background` on bands that are themselves `bg-card`. This generalizes `_project_card.html`'s original `project_card_surface_class`.
3. Hover affordance is reserved for clickable cards. A static card with `hover:border-accent/50` promises navigation that does not exist. Static cards get no `group`, no hover, and no arrow.

### Role contract table

| Role | Radius | Surface | Padding | Hover / group | Shadow | Title | Interactive |
|---|---|---|---|---|---|---|---|
| Content-catalog (clickable; media optional; badges; chips; one CTA) | `rounded-lg` | `bg-card` default / `bg-background` on card bands | `p-4 sm:p-5` | `hover:border-accent/50` + `group`, `group-hover:text-accent` title | none | `text-lg font-semibold` | whole-card anchor |
| Related-content row | none; `border-b border-border/70` | transparent | `py-4 sm:py-5` | `hover:bg-secondary/20` + title accent | none | `text-base sm:text-lg font-semibold` | whole-row anchor + arrow |
| Info / static (feature bullets, activity blurbs, sprint-story, testimonials) | `rounded-lg` | per band | `p-6` | none | none | `text-lg font-semibold` | not a link, no CTA inside |
| Callout / action (featured sprint, dashboard next-step, gated card, starting-soon; accent variant `border-accent/30 bg-accent/5`) | `rounded-lg` (spotlight exception) | `bg-card` / `bg-accent/5` | `p-6`; spotlight `p-5 sm:p-8` | none on container | none | `text-lg` / spotlight `text-2xl` | CTA is an explicit `{% button_classes %}` button |
| Tier / pricing | `rounded-xl` | `bg-background` | `p-5 sm:p-8` | none | `shadow-xl` on the highlighted tier only | `text-lg font-semibold` | `Join` button, full width, no arrow |

Spotlight exception: only tier/pricing cards and the home featured-sprint card keep `rounded-xl` (hero-scale, singular). Everything repeated in a grid is `rounded-lg`.

Shadow rule: repeated cards never carry a shadow. Do not add `shadow-sm` to grid cards (testimonials, sprint cards, etc.); `shadow-xl` is reserved for the highlighted tier card.

### CTA affordance (three affordances, not seven)

| Affordance | Meaning | Owner |
|---|---|---|
| Top-right translating arrow | This whole card navigates | `templates/content/_content_card.html` renders it; never hand-place it at a call site |
| `{% button_classes %}` button | Explicit action on a callout / tier / dashboard / gated card | `{% button_classes %}` from `accounts_extras` |
| None | Info / static card | — |

`_content_card.html` owns the only navigation arrow for both grid cards and editorial rows. Editorial arrows align to the far right of the first badge/status row so blog, workshop, interview, and sprint feeds share one scanning pattern. Eliminated affordances: trailing "Read article ->" / "View download ->" text spans, the `pointer-events-none` fake-button span inside a clickable card, `arrow-up-right` on the gated CTA, and no-affordance clickable cards.

### Owners

Clickable cards render through `templates/content/_content_card.html` (the container: `<article>` shell, wrapping anchor, optional media band, badge cluster, title and arrow placement, and post-anchor tag row). Static cards use the `templates/content/_info_card_classes.html` class string. Both are layered on the existing `templates/content/_clickable_card_classes.html` anchor a11y contract. See the [Partials and Component Index](#partials-and-component-index) for the canonical usage.

## Card Media Slots

Choose one documented media policy per content type and render every media band through `templates/content/_content_preview.html`: always render a real cover or fallback, never render a media band, or use a specifically documented conditional-explicit policy. Do not introduce per-card cover branching unless the per-type decision below explicitly requires it.

A grid in which every card shows the fallback is a defect. If real images do not exist for that content type, remove the media band rather than ship a placeholder wall. For a conditional-explicit content type, a card without an approved explicit image starts directly with its existing signals and body; it gets no fallback, empty wrapper, or reserved `aspect-video` space.

Current per-type decisions:

| Content type | Media band |
|---|---|
| Workshops | Conditional explicit media: render exactly one slot for an authored `cover_image_url` or operator `custom_banner_url`; render no slot for coverless or auto-only cards. Generated `auto_banner_url` remains social/Studio media only. |
| Courses | Render |
| Projects | Render |
| Downloads | Do not render |
| Curated Links (`/resources`) | Do not render |
| Blog list rows | Do not render |

Workshops are the deliberate conditional-explicit exception. Public cards use `Workshop.card_image_url` (`cover_image_url` → `custom_banner_url` → empty) and include `_content_preview.html` exactly once only when that value is nonempty. Never pass a decorative fallback for a coverless or auto-only workshop. `Workshop.display_image_url` keeps cover → custom → auto precedence for SEO, social sharing, and Studio; suppressing an auto banner on a public card does not delete or disable that sharing asset.

## Content Listing Pages

This section is the site-wide standard for public content listing/index pages: `/blog`, `/workshops`, `/events` (list and calendar), `/books`, `/courses`, `/projects`, and any future catalog surface (tutorials, downloads, resources). A listing page presents a collection of one content type with optional filters. Every rule below is mandatory; the blog list is the reference implementation.

Where this section conflicts with earlier guidance in this document, this section wins for listing pages specifically: the width-tier table's "Frame for 3+ column grids" rule and the `bg-secondary` unselected filter-pill recipe are both superseded here. `content/tests/test_container_widths.py`'s route table updates as part of adoption.

### Page skeleton

Exactly one `<section>`, one container, one width. No mid-page width changes, no `border-b` section dividers, no alternating band backgrounds (`bg-secondary/20` hero bands are not used on listing pages).

```html
<main class="min-h-screen pt-24">
  <section class="py-8 sm:py-16 lg:py-24">
    <div class="mx-auto max-w-5xl px-4 sm:px-6 lg:px-8">
      <!-- 1. page header -->
      <!-- 2. toolbar (optional) -->
      <!-- 3. list or grid (or grouped sub-sections) -->
      <!-- 4. supporting info cards (optional, last) -->
    </div>
  </section>
</main>
```

### Canonical width

Listing grids and mixed-layout indexes use `max-w-5xl`; `/courses`, `/projects`, and the dense Events month **calendar** are reference surfaces. Text-first single-column editorial feeds use `max-w-3xl` when readable line length and calm vertical scanning matter more than side-by-side comparison; `/blog`, `/events`, and `/workshops/catalog` are the reference feeds. Frame remains appropriate for the member dashboard, membership grid, and marketing pages. Inner columns may be narrower; the outer container never changes mid-page.

Detail/content pages are narrower: prose/reader pages (a blog article, a book chapter, the learning path) use `max-w-3xl` (Reader tier); mixed-layout detail pages keep `max-w-5xl` (Detail tier). Only the listing/index widths change under this section.

### Page header

One format for every listing page: eyebrow, H1, subtitle, optional CTA row. Wrapper: `<div class="mb-12">`.

```html
<div class="mb-12">
  <p class="text-sm font-medium uppercase tracking-widest text-accent">Workshops</p>
  <h1 class="mt-4 text-3xl font-semibold tracking-tight sm:text-4xl" style="text-wrap: balance;">
    Hands-on AI workshops
  </h1>
  <p class="mt-4 max-w-3xl text-lg text-muted-foreground">
    One or two sentences. No second paragraph.
  </p>
  <div class="mt-6 flex flex-wrap gap-3">
    <a href="..." class="{% button_classes 'primary' %}">Primary action</a>
    <a href="..." class="{% button_classes 'secondary' %}">Secondary action</a>
  </div>
</div>
```

Rules:

- Eyebrow is the plain section name, one or two words: `Blog`, `Workshops`, `Events`, `Books`, `Courses`, `Projects`. No composed eyebrows (`Community · Books`) and no renamed sections (`Project Showcase`).
- H1 is always `text-3xl font-semibold tracking-tight sm:text-4xl`. No `lg:text-5xl` on listing pages — that is the detail-hero scale.
- Subtitle is one `text-lg text-muted-foreground` paragraph capped at `max-w-3xl`. Merge or cut longer intros; explanatory prose moves to info cards at the bottom of the page.
- CTA row is optional and holds at most two `{% button_classes %}` buttons. Use it only for a real action that is not "scroll down". Never link to the list the user is already on.
- No `hero-gradient` backgrounds on listing pages.

### Toolbar

The toolbar sits between header and content: `<div class="mb-8 space-y-3">`. Up to three pill rows, in this fixed order; omit rows that do not apply.

1. Mode row — view toggles that change how the same collection renders (`List` / `Calendar`) plus at most one compact utility control (the events Subscribe popover).
2. Scope row — mutually exclusive collection scopes (`All` / `Upcoming` / `Past recordings`, or difficulty).
3. Topic row — `All` plus curated topic pills.

Every pill in every row uses one base class string:

```html
inline-flex min-h-[44px] items-center justify-center rounded-full px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background
```

- Active: `bg-accent text-accent-foreground` plus `aria-current="page"`.
- Inactive: `border border-border bg-transparent text-muted-foreground hover:border-accent/50 hover:text-foreground`.

The outlined inactive style (from the blog redesign) replaces the older `bg-secondary` inactive fill everywhere on listing pages; a page must not mix the two dialects. Each row is `flex flex-wrap gap-2` with an `aria-label`.

#### Topic filter

The topic row is the curated-topics pattern from `/blog`, powered by `content/topics.py` (`topics_with_matches`, `filter_by_topic`, `primary_topic`) — never a raw-tag wall:

- Pills are a small ordered curated set (about 5-7), first pill `All`, labels humanized via `humanize_tag` (`AI Engineering`, not `ai-engineering`).
- Only topics that match at least one item render (no dead pills).
- One topic selected at a time via `?topic=<slug>`; multi-tag query combinatorics (`?tag=a&tag=b`), tag accordions, `More topics (+N)` disclosures, and active-filter chip strips are all retired from listing pages. Deep raw-tag filtering, if kept at all, lives on `/tags`.
- Generalize `BLOG_TOPICS` into per-surface curated maps of the same shape so workshops, projects, courses, and event recordings reuse the same helpers.

Filtered-to-empty states use `{% member_empty_state ... kind='filter' %}` with a `View all <things>` CTA back to the unfiltered page.

### The content card

Every listing item renders through `templates/content/_content_card.html` (the existing owner: `<article>` shell, whole-card anchor from `_clickable_card_classes.html`, title hover, and top-right translating arrow). Use its standard rounded-card mode for grids and `card_editorial=True` for calm divider-led feeds. Unavailable entries such as interview categories that are coming soon use `card_static=True`, retaining the same anatomy without a misleading link or hover state. One anatomy, ordered slots; every slot except title is optional per content type:

1. Media — thumbnail per the Card Media Slots policy table. List rows: left-aligned `sm:w-48 sm:h-32 rounded-lg border border-border object-cover`. Grid cards: `_content_preview.html` band.
2. Signal row — `mb-3 flex flex-wrap items-center gap-2`, in order: `{% member_access_badge %}` (always, `sm` size), at most one status/type badge (`Recording available`, `Cancelled`, `Enrolled`), then the topic eyebrow.
3. Topic eyebrow — one non-clickable `<span class="text-xs font-medium uppercase tracking-wide text-accent">` with the item's `primary_topic` label. This is the only topic representation on a card. Raw-tag chip rows are removed everywhere, as the blog did.
4. Title — `text-lg font-semibold text-foreground group-hover:text-accent transition-colors`, `line-clamp-3` in grids.
5. Description — `mt-2 text-sm text-muted-foreground line-clamp-2`, markdown stripped.
6. Meta row — icon+text pairs (author, date, reading time, attendee count). List rows: `mt-4 flex flex-wrap items-center gap-4 text-sm text-muted-foreground` with `h-4 w-4` icons. Grid cards: `mt-2 gap-x-3 gap-y-1.5 text-xs` with `h-3.5 w-3.5` icons.
7. Type-specific slot — at most one extra row per content type, static tag chips (`rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium text-muted-foreground`): workshop `Includes` deliverable pills (max 4); book progress/schedule line; nothing for blog, courses, projects.
8. CTA — at most one explicit `{% button_classes %}` button when the card has a distinct action besides navigation (event `Watch recording`). Cards never carry both a CTA button and trailing link text.

#### List vs grid

- List rows (horizontal entry, full container width, optional thumbnail left): time-ordered or text-first feeds where recency and title scanning organize the page — Blog, Events, Workshops.
- Grid cards (vertical, media on top): visual catalogs where side-by-side comparison organizes the page — Courses, Projects, Books.
- Grid: `grid gap-6 sm:grid-cols-2 lg:grid-cols-3` (1 / 2 / 3 columns). A grid page with fewer than 3 items still uses the grid.
- Editorial list: one `border-t border-border/70` wrapper; shared cards supply the matching bottom borders and `py-6 sm:py-7` row rhythm.

### Section rhythm and grouped listings

When a listing groups items (`Reading now` / `Coming up` / `Past reads`; `Upcoming` / `Past events`), groups are sub-sections inside the single container:

- Group heading: `<h2 class="text-2xl font-semibold tracking-tight sm:text-3xl">`, plain text — no decorative icons inside listing H2s.
- First group starts immediately after the toolbar; each subsequent group heading gets `mt-12`; its list/grid follows with `mt-6`.
- Never use `<hr>`, `border-b` bands, or background-color changes to separate groups — the `mt-12` + H2 rhythm is the only divider.
- Supporting info cards (`How it works`, workshop value props) are one optional trio at the bottom of the page: `mt-16`, H2 as above, then `grid gap-6 sm:grid-cols-3` of static cards using `_info_card_classes.html`. Info cards never sit between the header and the content list.

### Empty states and pagination

Every zero state renders `{% member_empty_state %}`: `kind='fresh'` when the collection is empty, `kind='filter'` with a `View all <things>` primary CTA when filters produced zero results. No hand-rolled `p-8 text-center` cards. Pagination uses the shared pager row: `mt-12 flex flex-wrap items-center justify-center gap-2`, Previous/Next as `{% button_classes 'secondary' %}` with chevron icons, `Page X of Y` in `text-sm text-muted-foreground`.

### Per-page application snapshot

| Page | Layout | Toolbar rows | Type-specific slot |
|---|---|---|---|
| Blog | List | Topic | — |
| Workshops | Editorial list (`max-w-3xl`) | Topic (curated, replaces access/skill/tag facets) | — |
| Interview | Editorial list (`max-w-3xl`) | — | section count for available categories |
| Events | List | Mode (List/Calendar + Subscribe), Scope (All/Upcoming/Past recordings), Topic (recordings only) | `Watch recording` CTA |
| Books | Grid (grouped) | — | schedule/progress line |
| Courses | Grid | Topic (when >6 courses) | — |
| Projects | Grid | Scope (difficulty), Topic | — |

## Breakpoints and Mobile Carousels

Tailwind defaults apply: `sm` 640px, `md` 768px, `lg` 1024px, `xl` 1280px.

Mobile carousel convention:

- Pick `max-{breakpoint}:` to match the lowest breakpoint where the desktop grid begins.
- If desktop starts at `md:grid-cols-2`, the carousel uses `max-md:*`.
- If desktop starts at `lg:grid-cols-3` or `lg:grid-cols-4`, the carousel uses `max-lg:*`.
- Use `-mx-4` plus `px-4` so the carousel can bleed to the viewport edge while content still aligns to the page frame.
- Use `snap-x snap-mandatory overflow-x-auto scroll-smooth [scrollbar-width:none]`.
- Give cards an explicit mobile width such as `max-md:w-[min(84vw,24rem)]` or `max-lg:w-[min(82vw,22rem)]`.
- If a card has an absolute badge above it, add mobile top padding to the scroll container because `overflow-x-auto` also clips vertical overflow.

## Before You Write a Class String

1. Identify the UI role being added or changed.
2. Find that role in the [Partials and Component Index](#partials-and-component-index).
3. If the index has no owner, search a sibling surface and copy its exact established class string.
4. Only then create a new class string, and document the reason in the implementation issue.

Hand-rolling markup or classes for a role owned by the index is a review-blocking defect, even when the duplicate renders identically.

Django `{# #}` comments are single-line only; use `{% comment %} ... {% endcomment %}` for anything spanning more than one line. A multi-line `{# #}` renders as visible text and is blocked by the `content/tests/test_template_comment_lint.py` lint.

## Partials and Component Index

The documented owner is mandatory for every instance of its named role, subject only to the explicit constraints in that component's existing documentation.

| Owner | Use for | Canonical usage |
|---|---|---|
| `{% page_seo_tags %}` from `seo_tags` | Complete canonical, Open Graph, and Twitter metadata for public collection/static pages that do not have a model instance for `{% og_tags %}`. Pass the same document title and meta description rendered by the page, its stable no-trailing-slash route, and only a view-normalized canonical query string when the route taxonomy requires one. The tag resolves the absolute host through the IntegrationSetting-aware site URL and owns the default social-image fallback. | `{% load seo_tags %}` then `{% block page_head_metadata %}{% page_seo_tags "Blog | AI Shipping Labs" "Articles on AI engineering and building with data." "/blog" %}{% endblock %}` |
| `templates/content/_gated_access_card.html` | Every paid/tier-gated content block, after the view supplies the documented gate context including `required_tier_name`. | `{% include "content/_gated_access_card.html" %}` |
| `{% member_empty_state %}` from `member_empty_state` | Every member/public collection or section empty state. | `{% load member_empty_state %}` then `{% member_empty_state title='No items yet' body='Check back soon.' icon='inbox' kind='fresh' %}` |
| `{% studio_empty_state %}` from `studio_filters` | Every Studio list empty state. Use `fresh` when no records exist and `filter` when active filters yield no rows. | `{% load studio_filters %}` then `{% studio_empty_state 'fresh' entity_label='article' entity_label_plural='articles' %}`; filter variant: `{% studio_empty_state 'filter' entity_label='article' entity_label_plural='articles' clear_url='/studio/articles/' colspan=6 %}` |
| `{% member_access_badge %}`, `{% member_tier_badge %}`, `{% member_label_badge %}`, and `{% member_status_badge %}` from `member_badges` | Use `member_access_badge` on every public/member content card: Free and Free-with-sign-in render as a green check badge; paid tiers render as an accent lock badge; all use the public access vocabulary and `sm` size. Use the lower-level tier tag only outside cards when a different documented treatment is required. | `{% load member_badges %}` then `{% member_access_badge item.required_level testid='item-access-badge' %}`, `{% member_label_badge 'Workshop' %}`, and `{% member_status_badge 'Upcoming' status='upcoming' %}` |
| `{% button_classes %}` from `accounts_extras` | Every non-Studio product/public/member/marketing CTA. | `{% load accounts_extras %}` then `class="{% button_classes 'primary' size='lg' extra='w-full sm:w-auto' %}"` |
| `templates/content/_content_preview.html` | Every rendered catalog media band for a content type whose contract includes media. Workshop cards use it only for explicit cover/custom media; coverless and auto-only workshops omit the slot entirely. | `{% if workshop.card_image_url %}{% include "content/_content_preview.html" with preview_cover_url=workshop.card_image_url preview_title=workshop.title preview_label="Workshop" preview_icon="graduation-cap" preview_testid="workshop-card-preview" %}{% endif %}` |
| `templates/content/_clickable_card_classes.html` | Every fully clickable catalog/preview card anchor. | `class="{% include 'content/_clickable_card_classes.html' %} rounded-lg"` |
| `templates/content/_content_card.html` | Every fully clickable content-catalog / compact-rail card container (the `<article>` shell, wrapping anchor, optional media band, header row with badge cluster + top-right arrow, and post-anchor tag row). Pass the per-type chrome as `card_*` partial paths. | `{% include "content/_content_card.html" with card_url=item.get_absolute_url card_testid="item-card" card_body_template="content/_item_card_body.html" card_badge_template="content/_item_card_badges.html" card_surface_class="bg-background" %}` |
| `templates/content/_info_card_classes.html` | Every static / info card class string (`rounded-lg border border-border p-6`). The caller appends the band-contrast surface token; never combine with `group`, hover, or an anchor. | `class="{% include 'content/_info_card_classes.html' %} bg-card"` |
| `templates/includes/_list_row.html` | Every reader, drawer, or numbered navigation row. | `{% include "includes/_list_row.html" with href=item.get_absolute_url title=item.title is_current=False marker_kind="circle" %}` |
| `templates/accounts/includes/_auth_card.html` | Every standard full-page authentication form. Titles use sentence case; pass the subtitle, form partial, OAuth copy, and legal-action input. | `{% include "accounts/includes/_auth_card.html" with auth_title="Create account" auth_subtitle="Join AI Shipping Labs and start building" form_template="accounts/includes/_register_form.html" oauth_action="Sign up" oauth_divider_text="sign up with" legal_action="creating an account" %}` |
| `templates/includes/_accordion.html` | Every section-header accordion. | `{% include "includes/_accordion.html" with summary="Show details" body=details_html %}` or `{% include "includes/_accordion.html" with summary="Show transcript" body_template="events/_recording_transcript_body.html" %}` |
| `templates/includes/testimonial_cards.html` | Every testimonial card collection, with `testimonials` in context. | `{% include "includes/testimonial_cards.html" %}` |
| `templates/includes/_icon_github.html` | Every GitHub brand mark. | `{% include 'includes/_icon_github.html' with css='h-4 w-4' %}` |
| `{% studio_header_actions %}` backed by `templates/studio/_partials/header_actions.html` | Every Studio list, detail, and form page header. Callers supply local actions as the block body. | `{% load studio_filters %}` then `{% studio_header_actions title=object.title subtitle="Entity details" %}...{% endstudio_header_actions %}` |
| `{% studio_overflow_menu %}` backed by `templates/studio/_partials/overflow_menu.html` | Every Studio page-header overflow menu. Callers supply local link and POST items as the block body. | `{% studio_overflow_menu %}...{% endstudio_overflow_menu %}` |
| `{% studio_list_action %}` backed by `templates/studio/includes/list_action.html`, and `{% studio_action_class %}` from `studio_filters` | Every Studio table/list row action. Use `studio_list_action` for links and `studio_action_class` for buttons, forms, or markup the inclusion tag cannot express. Row navigation and disclosure use `secondary`; `primary` is reserved for at most one state-changing action per rendered row. Keep destructive, async, and special-purpose mutations in their established variants. | `{% load studio_filters %}` then `{% studio_list_action detail_url 'View' 'secondary' %}`; for non-link markup: `class="{% studio_action_class 'destructive' %}"` |
| `templates/content/reader/_mobile_progress_bar.html` | Every eligible ungated mobile course/workshop reader progress control, after its documented context is supplied. Never include it on a gated reader. | `{% include "content/reader/_mobile_progress_bar.html" %}` |

### Deprecated

- `templates/includes/content_gated.html` was deleted in #1335. Every gated
  surface now renders `templates/content/_gated_access_card.html`, and all
  banner copy is assembled once in `content.access.build_gating_context` /
  `content.access.build_gated_access_copy`. Do not reintroduce a second
  gated-card dialect or hand-assemble gated headings/CTAs at a call site.

## Buttons

Every non-Studio CTA on product, public, member, and marketing surfaces gets its classes from `{% button_classes %}`. New or edited templates must not hand-roll equivalent button class strings. Studio retains the separately documented Studio action and button patterns.

Public and marketing hero, pricing, and conversion CTAs use `size='lg'`. Other public/member CTAs use `size='md'` unless they are genuinely compact row actions (`sm`). Touched legacy hand-rolled buttons migrate to the tag.

Canonical call:

```django
{% load accounts_extras %}
<a href="/pricing" class="{% button_classes 'primary' size='lg' extra='w-full sm:w-auto' %}">View pricing</a>
```

There are exactly three sizes. The button tag, not a padding pair, is the unit of reuse. `px-5 py-2.5`, bare `py-2.5`, and `px-4 py-2.5` button chrome are forbidden everywhere.

### Button size scale

Issue #598 — product buttons use one of three named sizes. Pick by role, not by visual taste.

| Name | Padding         | Text size  | `min-h-[44px]` | Use case                                                                  |
|------|-----------------|------------|----------------|---------------------------------------------------------------------------|
| `sm` | `px-3 py-1.5`   | `text-xs`  | no             | Compact per-row table actions, inline edit controls, narrow card chrome.  |
| `md` | `px-4 py-2`     | `text-sm`  | yes            | Default public/member CTA outside conversion roles.                       |
| `lg` | `px-6 py-3`     | `text-base`| yes            | Public/marketing hero, pricing, and conversion CTAs.                      |

Both `md` and `lg` include `min-h-[44px]` and the complete focus-visible ring. Reuse the tag, not an extracted padding pair.

Tag signature:

```django
{% load accounts_extras %}
{% button_classes variant size='md' extra='' %}
```

- `variant` is positional and required. One of `primary`, `secondary`, `destructive`.
- `size` defaults to `md` so every existing call site stays byte-for-byte unchanged.
- `extra` appends after the canonical classes so per-call overrides win the cascade (used by the amber verification banner and the emerald Join sprint button).
- A positional second argument is treated as `extra` when it is not one of `{sm, md, lg}` — preserves backward compatibility with `{% button_classes 'secondary' 'shrink-0' %}` calls.

Additional use examples:

```django
{% load accounts_extras %}
<a href="..." class="{% button_classes 'primary' %}">Open</a>
<a href="..." class="{% button_classes 'secondary' 'shrink-0' %}">View</a>
<button type="button" class="{% button_classes 'destructive' %}">Cancel</button>
<a href="/pricing" class="{% button_classes 'primary' size='lg' extra='w-full sm:w-auto' %}">Upgrade</a>
<button type="button" class="{% button_classes 'primary' size='sm' %}">Ping</button>
```

The transparent destructive variant uses `text-red-700 dark:text-red-400`
against product backgrounds. Keep this theme split when extending the helper;
`text-red-400` alone is not readable enough in the light theme.

Joining order in the rendered class string is `base size variant extra`. Do not reshuffle: per-call overrides rely on appearing last.

Rendered class strings, per (variant, size):

Primary, `sm`:

```html
inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background px-3 py-1.5 text-xs bg-accent text-accent-foreground hover:bg-accent/90
```

Primary, `md` (default):

```html
inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background min-h-[44px] px-4 py-2 text-sm bg-accent text-accent-foreground hover:bg-accent/90
```

Primary, `lg`:

```html
inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background min-h-[44px] px-6 py-3 text-base bg-accent text-accent-foreground hover:bg-accent/90
```

If a rendering context cannot invoke the tag and must inline the output, copy it whole.

Secondary uses the same base and size classes and swaps only the trailing variant cluster for:

```html
border border-border bg-transparent text-foreground hover:bg-secondary
```

Destructive likewise differs only in the variant cluster:

```html
border border-red-500/30 bg-transparent text-red-400 hover:bg-red-500/10
```

Per-row cohort table actions in `cohort_board.html` use `size='sm'` deliberately because they are compact row actions, not page-level CTAs. The three-size contract is site-wide for non-Studio CTAs.

### Studio page header (stacked)

Every Studio page—list, detail, and form—uses one stacked header block. The title never shares a row with controls; pinning actions to the right of the H1 is an anti-pattern and must not be reintroduced.

Structure, top to bottom, is identical at all viewport widths:

1. Optional back link or breadcrumb (`&larr; Back to <parent>`, `text-sm text-muted-foreground hover:text-foreground`).
2. Title block: optional eyebrow, `<h1 class="text-2xl font-semibold text-foreground break-all">`, and optional `text-sm text-muted-foreground` subtitle. Status pills and other entity metadata live in a `mt-2 flex flex-wrap items-center gap-2` meta row under the H1—never in the action row.
3. Optional action row: `flex flex-wrap items-center gap-2`, left-aligned and full-width.

The header wrapper is `mb-8 space-y-4`. Page headers must not use `justify-between`, `sm:flex-row`, `sm:justify-end`, `shrink-0`, or `space-x-*`. A header with no actions omits the action row entirely.

New headers render through the shared `{% studio_header_actions %}` block tag from `studio_filters`, backed by `templates/studio/_partials/header_actions.html`. The tag preserves the context names `eyebrow`, `title`, `subtitle`, `back_url`, `back_label`, `testid`, and `actions_testid`; callers supply only those values and the local action body. This block-tag API works from pages that already extend `studio/base.html`, unlike partial inheritance or a plain include with no caller-defined body.

Action-row composition:

- At most one primary (`bg-accent text-accent-foreground px-4 py-2 rounded-lg text-sm font-medium hover:opacity-90 transition-opacity`), always first.
- Up to two visible secondaries (`bg-secondary border border-border text-foreground px-4 py-2 rounded-lg text-sm font-medium hover:bg-muted transition-colors`).
- Everything else goes into the overflow menu, rendered last.
- Header buttons and menu items append `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background`.
- List-page CTAs use a leading Lucide `h-4 w-4` icon and button-level `gap-2` (no `mr-2`). Vocabulary: `plus` create, `upload` import, `download` export, and `refresh-cw` re-sync. Labels are sentence case: `New <noun>`, `Import <noun>`, `Export CSV`, and `Re-sync <noun>`.
- List pages keep per-entity actions in the table's Actions cell, never in the page header.
- Form-edit pages keep the sticky Save/Cancel bar. Header actions are not duplicated there, in sidebars, or in bottom action cards.
- `View on site` stays visible when the public page is the entity's main artifact (workshops, articles, courses, marketing pages); otherwise it goes in overflow.

#### Studio overflow menu

Secondary and rare entity actions (`Open in Django admin`, exports, carry-over and duplication tools, archive/unarchive, delete) live in a kebab menu at the end of the action row. Use the shared `{% studio_overflow_menu %}` block tag backed by `templates/studio/_partials/overflow_menu.html`.

The partial renders `<details data-studio-overflow>` with an `ellipsis-vertical` Lucide summary trigger. The trigger is 38px square with secondary-button chrome and `aria-label="More actions"`. Its panel is anchored `left-0` and uses `w-64 bg-card border border-border rounded-lg shadow-lg`; items have `min-h-[44px]`.

- Link items are full-width flex anchors with icon, label, hover state, and the canonical focus-visible ring.
- POST items are full-width `<form method="post">` elements with CSRF and a full-width, left-aligned submit button using the same item geometry.
- Destructive items render last, after a `border-t border-border` divider, using `text-red-400 hover:bg-red-500/10`.
- Existing `confirm()` guards remain on destructive or otherwise guarded forms.
- File uploads never go into overflow; they get a section in the page body.
- If the current Lucide CDN build cannot resolve `ellipsis-vertical`, use the legacy alias `more-vertical`.

Section headers inside cards follow the same rule: the section title gets its own row and any section-level button row sits below it, left-aligned.

Documented exceptions: `/studio/crm/` keeps its filter-chip/search control row because it is controls versus controls, with no title in the row. Pager rows and in-table control rows may retain `justify-between`.

Status badge palette (`STATUS_BADGE_CLASSES` in `studio/templatetags/studio_filters.py`):

- `published`, `active`: `bg-green-500/20 text-green-400` — live/in-progress states.
- `draft`: `bg-yellow-500/20 text-yellow-400`.
- `upcoming`: `bg-blue-500/20 text-blue-400`.
- `completed` (archived): `bg-secondary text-muted-foreground`.
- `cancelled`: `bg-red-500/20 text-red-400`.

#### Studio list-page empty states

List-page empty states have two flavours, and both ship from the shared `templates/studio/includes/empty_state.html` partial via the `{% studio_empty_state %}` inclusion tag (issue #756):

- Filter-zero — a search or status filter is active and produced zero rows. Use `{% studio_empty_state 'filter' entity_label='sprint' entity_label_plural='sprints' clear_url=... colspan=N %}` inside the table's `<tbody>`. The partial renders an inline `<tr><td colspan>` row with the message `No <plural> match your filters.` and a `Clear filters` link, keeping the table header visible so the operator can adjust filters in place.
- Fresh-install zero — the entity has no rows at all (no filter active). Wrap the table in `{% if rows or <filter_vars> %}...{% else %} {% studio_empty_state 'fresh' entity_label='sprint' entity_label_plural='sprints' create_url=... %} {% endif %}`. The partial renders a separate `bg-card border border-border rounded-lg p-12 text-center` empty card with an inbox icon, the message `No <plural> yet.`, and a primary `New <noun>` CTA. Use `cta_label=...` only when a domain-specific sentence-case action is required.

Distinguish the two cases in the view by exposing the search / status / tag filter variables to the template (most list views already do) or by adding a `filters_active` boolean to the context. The pattern is: when the queryset is empty AND filters are active, the table chrome stays visible with the filter-row; when the queryset is empty AND no filter is active, the table is omitted entirely in favour of the fresh card.

Sync-managed entities (workshops, courses, articles, projects, downloads, recordings) call the partial with `create_url` omitted so the fresh-zero card renders without a CTA — content for those tables comes from the GitHub content sync, not from a Studio create form.

#### Member/public empty states

Every zero-collection or zero-filter-result state on a member/public page renders through `{% member_empty_state %}` from `content.templatetags.member_empty_state`. Never hand-roll the icon-plus-muted-copy card or a `p-12 text-center` box. Absence of one optional field inside an otherwise populated record is not a collection empty state.

This component is intentionally separate from Studio empty states: it always renders member-page card chrome (`bg-card border border-border rounded-lg`), uses Lucide icons with consistent sizing, and exposes `data-testid="member-empty-state"` for regression tests. If an existing page has a page-specific selector, pass `testid="..."`; the outer card keeps that selector and the component still emits the canonical marker inside it.

- Fresh-empty — no content exists for the section or catalog yet. Use `kind="fresh"` and copy that does not mention filters or user error, for example `No tutorials yet` with `Check back soon for step-by-step guides.` CTAs are optional and should point to the next useful member journey when one exists.
- Filter-empty — active filters produced zero results. Use `kind="filter"`, keep the user's context in the message, and include a clear CTA back to the unfiltered list such as `View all articles`, `View all courses`, `View all workshops`, or `View all recordings`.

Pass CTAs as explicit parameters: `primary_cta_label`, `primary_cta_url`, optional `primary_cta_icon`, plus the matching `secondary_*` values when a second action is needed. Compute non-Studio CTA classes with `{% button_classes ... as ... %}` and pass `primary_cta_class` / `secondary_cta_class`. Fresh and filter variants keep the same copy and CTA guidance: a filter-empty state provides a clear route back to the unfiltered collection, while a fresh-empty CTA is optional and points to the next useful journey.

## Form Controls

### `<select>` chrome

Every `<select>` element must carry one of two equivalent class names so the global CSS rule in `templates/base.html` can apply the shared chrome (hidden native chevron, CSS-gradient caret, theme-aware colors):

- `app-select` — for any `<select>` reachable outside `/studio/` (public pages, `/account`, plan pages).
- `studio-select` — for any `<select>` inside `/studio/`.

Both names map to the same CSS rule (`select.app-select, select.studio-select { ... }`) defined once in `templates/base.html`. The split is a naming convention so a contributor reading a template can tell which surface the control belongs to; it does NOT produce a visual difference.

Canonical class string to pair with `app-select` / `studio-select` on a Studio select:

```html
class="studio-select w-full bg-secondary border border-border rounded-lg px-4 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
```

For a `<select>` that sits inside a `bg-secondary` panel and should look visually inset (the `/account` timezone field is the reference), use `bg-background` and `rounded-md` instead:

```html
class="app-select w-full rounded-md border border-border bg-background px-4 py-2.5 text-base text-foreground focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
```

### Chevron technique

The custom caret is rendered with two `linear-gradient` backgrounds in the
global CSS rule in `assets/css/tailwind.css` (`select.app-select,
select.studio-select`). It is theme-aware, requires zero extra DOM nodes, and
needs no JS init. Do not replace it with a Lucide overlay or a per-template SVG
— the gradient approach is in production, accessible, and already covered by
`studio.tests.test_form_components.GlobalSelectStyleTest`.

Do not add per-template `appearance: none` overrides or background-image rules to `<select>` elements. If a new visual variant is needed, extend the global rule, not the local template.

### Regression coverage

The unit test `studio.tests.test_form_components.GlobalSelectStyleTest.test_every_select_in_templates_has_canonical_class` walks every `*.html` file under `templates/` and asserts each `<select>` opening tag carries `app-select` or `studio-select`. If you add a new `<select>` and CI fails on that test, add the canonical class — do not allowlist the file.

## Pills, Badges, and Chips

Member/public pills use the tags in `content/templatetags/member_badges.py`; Studio status pills use the established Studio badge tag. Never inline a pill whose meaning an owning tag covers, even if the classes would be identical.

Canonical badge shape: `inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium`.

| Meaning | Tone |
|---|---|
| Access or tier, including Free | Accent |
| Success, completed, registered, or live-positive | Green |
| Finished, past, or expired | Neutral/muted |
| Upcoming or informational schedule state | Blue |
| Cancelled or error | Red |
| Draft | Yellow |

Green is reserved for success semantics. Access/Free and Past are never green. `STATUS_TONES['past']` resolves to neutral (`'muted'`) in `content/templatetags/member_badges.py`.

This document defines the named tier-badge component contract; the badge migrations that adopted it have shipped.

Gate vocabulary is `Basic or above required`, `Main or above required`, and terminal-tier `Premium required`. Never use `Basic tier required`, `Main tier required`, `Premium tier required`, or `Premium or above required`.

Clickable tag chip (no `min-h-[44px]`):

```html
inline-flex items-center gap-1 rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-secondary/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background
```

Static tag chip (no `min-h-[44px]`):

```html
inline-flex items-center gap-1 rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium text-muted-foreground
```

The canonical page-level filter-pill/view-toggle base, matching `templates/content/_workshops_catalog.html`, is:

```html
inline-flex min-h-[44px] items-center justify-center rounded-full px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2
```

The selected state adds `bg-accent text-accent-foreground` and `aria-current="page"`. The unselected state adds `bg-secondary text-muted-foreground hover:bg-secondary/80 hover:text-foreground`.

On content listing pages specifically (`/blog`, `/workshops`, `/events`, `/books`, `/courses`, `/projects`), the unselected filter pill uses the outlined variant `border border-border bg-transparent text-muted-foreground hover:border-accent/50 hover:text-foreground` instead of the `bg-secondary` fill above — see [Content Listing Pages](#content-listing-pages), which is authoritative there. Do not mix the two dialects within one page.

Pill icons are usually `h-3 w-3` or `h-3.5 w-3.5`.

## Tap Targets

| Role | 44px decision |
|---|---|
| `md` and `lg` buttons | Apply `min-h-[44px]`. |
| Page-level filter pills and view toggles | Apply `min-h-[44px]`. |
| Interactive list/navigation rows | Apply `min-h-[44px]`. |
| Calendar navigation controls | Apply `min-h-[44px]`. |
| Icon-only action links | Apply `min-h-[44px]` and `min-w-[44px]`. |
| Tag chips or metadata pills, even when linked | Do not apply a 44px minimum. |
| `sm` buttons | Do not apply a 44px minimum. |
| Inline prose links | Do not apply a 44px minimum. |

Tie-breaker: a page-level control the user came to operate gets the 44px target; an incidental metadata link does not. When a new interaction does not clearly fit, record the decision in its implementation issue.

## Gated Content

- Every paid/tier-gated content block renders `templates/content/_gated_access_card.html`; do not create another gated-card dialect.
- Views must expose `required_tier_name`; a paid/tier-gated card without its tier pill is incomplete.
- Gated detail surfaces also expose the relevant shared tier badge above the fold, not only inside the paywall.
- `templates/includes/content_gated.html` is deprecated as described in [Deprecated](#deprecated).
- The shared partial itself must use `{% button_classes %}` for CTA chrome when an implementation issue next touches it. Callers must not work around its legacy `px-5 py-2.5` secondary action. The scoped partial/template migration that adopted this contract has shipped.
- Keep non-tier gates, such as email-verification guidance, distinct when they do not represent a paid/tier access block.

## Focus, Hover, and Active States

Every keyboard-reachable custom surface needs a visible focus state. The canonical ring is:

```html
focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background
```

Use these interaction patterns unless an existing local component requires otherwise:

- Cards: `hover:border-accent/50`, with child titles often changing to `group-hover:text-accent`.
- Body links: accent color with underline and `text-underline-offset`.
- Primary buttons: `hover:bg-accent/90`.
- Secondary buttons: `hover:bg-secondary` or `hover:bg-secondary/80`.
- Drawer/list rows: `hover:bg-muted/50`.
- Active/current rows: `bg-accent/10 text-accent font-medium` plus `aria-current="page"` or `aria-current="true"`.

## Iconography

Use Lucide icons with `<i data-lucide="name" class="...">`. Add `flex-shrink-0` when the icon sits beside wrapping or truncated text.

Common sizes:

- Text inline and buttons: `h-4 w-4`.
- Compact meta rows: `h-3 w-3` or `h-3.5 w-3.5`.
- Section badges or quote blocks: `h-5 w-5`.
- Large hero/empty-state icons: `h-6 w-6` and above.

Lucide brand icons are not reliable in the CDN build. Use `templates/includes/_icon_github.html` for GitHub and add brand SVG partials with `currentColor` if other brand marks are needed.

## Theme and Accessibility Expectations

Build and review in light and dark themes. Theme-aware surfaces should use the token classes above; accent opacity utilities usually adapt correctly in both themes.

Accessibility checklist:

- Apply 44px minimums according to the [Tap Targets](#tap-targets) decision table; compact metadata and inline prose links remain exempt.
- Icon-only buttons and links have `aria-label` or equivalent hidden text.
- Active navigation/list items expose `aria-current`.
- Keyboard focus is visible on anchors, buttons, rows, and custom controls.
- Long titles in flex layouts have `min-w-0`, `truncate`, `line-clamp-*`, or `break-words` as appropriate.
- Body and metadata colors use token pairs with sufficient contrast.

## When in Doubt

1. Follow [Before You Write a Class String](#before-you-write-a-class-string).
2. Use the owner named in the [Partials and Component Index](#partials-and-component-index) whenever the role is indexed.
3. Search for an existing sibling surface and copy its established class string exactly when an unindexed visual role matches.
4. Use CSS variables and Tailwind token classes, not raw hex values; state colors also obey the tone-semantics table.
5. Verify desktop and mobile screenshots in both themes for UI-heavy changes.
6. If a new pattern is truly needed, document why in the implementation issue.
