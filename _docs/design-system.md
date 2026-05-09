# AI Shipping Labs Design System

This is a working reference for the Django template UI as it exists today. It is not a redesign brief. Before adding a new component or class string, search for an existing surface with the same role and reuse its partial or class pattern.

## Stack and Sources of Truth

- Templates: Django templates under `templates/`, with public pages extending `templates/base.html`.
- Tailwind: loaded via CDN in `templates/base.html`; there is no Tailwind build step.
- Tokens: HSL CSS variables live in `templates/base.html` on `:root` and `.dark`.
- Theme mapping: the inline Tailwind config maps variables to `bg-*`, `text-*`, `border-*`, `ring-*`, and related utilities.
- Fonts: Inter for UI text and JetBrains Mono for code, loaded from Google Fonts.
- Icons: Lucide via CDN with `<i data-lucide="...">`; brand marks such as GitHub use inline SVG partials.
- Theme: a blocking head script reads `localStorage['theme']` or `prefers-color-scheme` and toggles `<html class="dark">`.

## Color Tokens

All theme-aware color should come from the variables in `templates/base.html`. Use opacity slash syntax such as `bg-accent/10`, `border-accent/30`, and `text-muted-foreground/40` instead of raw hex values.

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

Use raw Tailwind palette colors only when the state is intentionally theme-stable, such as success (`text-green-400`, `bg-green-500/15`) or explicit status/error badges already present in the codebase.

## Typography Scale

Inter uses weights 300 through 700, but public templates mostly use `font-medium` and `font-semibold`. Prefer `font-semibold` over `font-bold` for hierarchy unless matching an existing page.

| Role | Common class pattern |
|---|---|
| Homepage hero h1 | `text-4xl font-semibold tracking-tight sm:text-5xl lg:text-6xl` |
| Page h1 | `text-3xl font-semibold tracking-tight sm:text-4xl` |
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

## Spacing and Layout

Tailwind's default 4px scale is the baseline. Bare classes are mobile values; `sm:`, `md:`, and `lg:` progressively enhance wider screens.

Standard horizontal frame:

```html
mx-auto max-w-7xl px-4 sm:px-6 lg:px-8
mx-auto max-w-5xl px-4 sm:px-6 lg:px-8
mx-auto max-w-3xl px-4 sm:px-6 lg:px-8
```

Use `max-w-7xl` for marketing and listing pages, `max-w-5xl` for detail pages with richer layout, and `max-w-3xl` for reader or long-form prose pages. Studio pages use their own admin layout.

Common vertical rhythm:

- Marketing/listing sections: `py-12 sm:py-20 lg:py-28`.
- Reader/detail sections: `py-8 sm:py-16 lg:py-24`.
- Hero/detail blocks often use `py-16 sm:py-20 lg:py-24`.
- Default grid gap: `gap-6`; tighter operational rows use `gap-4`.
- Common stack jumps: `mt-1`, `mt-2`, `mt-4`, `mt-6`, `mt-10`, `mt-16`.

Common card padding:

- Tier/hero cards: `p-5 sm:p-8`.
- Default content/testimonial cards: `p-6`.
- Compact catalog cards: `p-4 sm:p-5`.
- List rows: `px-3 py-2` with `min-h-[44px]`.
- Studio table cells: `px-4 py-3`.

Variable-height detail cards:

- Do not force related detail content into competing multi-column layouts when sections can have very different content height. A single vertical flow is often clearer than side-by-side blocks with awkward empty space.
- When a section combines explanatory copy with one primary detail card, stack them as two rows: intro/description first, primary card second.
- Inside detail cards, stack variable facts such as dates, duration, status, requirements, and next-step guidance as rows instead of splitting them into equal columns.
- Use compact grouped rows (`gap-3` or `gap-4`) for facts; reserve grids for repeated cards of the same visual weight and predictable height.

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

## Existing Reusable Patterns

Reach for these before writing a new inline component.

- `templates/content/_clickable_card_classes.html`: shared outer anchor classes for clickable catalog/preview cards. Owns `group block` and the canonical focus-visible ring.
- `templates/includes/_accordion.html`: shared `<details>` accordion with sentence-case `text-base font-medium text-foreground`, chevron rotation, card chrome, and optional template/body content.
- `templates/includes/_list_row.html`: canonical row for drawer, reader, and numbered-list surfaces. Provides `min-h-[44px]`, active state, marker variants, trailing icons, truncation, and focus rings.
- `templates/content/_gated_access_card.html`: standardized gated/paywall access card with accent border, lock/badge iconography, required-tier copy, and upgrade/sign-up CTAs.
- `templates/content/reader/_mobile_progress_bar.html`: mobile-only reader progress and drawer trigger, hidden on `lg+` and not meant for gated pages.
- `templates/includes/testimonial_cards.html`: testimonial grid/mobile carousel with quote icon, `line-clamp-10`, author block, and responsive card width.
- `templates/content/_content_preview.html`: reusable course/workshop preview media with cover image or fallback, label pill, access label, title, and meta row.
- `templates/includes/_icon_github.html`: inline GitHub SVG replacement for missing Lucide brand icons; inherits color through `currentColor`.

## Buttons

Primary filled action:

```html
inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md bg-accent px-6 py-3 text-sm font-medium text-accent-foreground transition-colors hover:bg-accent/90
```

Secondary/outlined action:

```html
inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border border-border bg-transparent px-6 py-3 text-sm font-medium text-foreground transition-colors hover:bg-secondary
```

Top-level CTAs commonly add `w-full sm:w-auto` on mobile-to-desktop transitions. Real `<button>` elements should include disabled styling such as `disabled:cursor-not-allowed disabled:opacity-50` when the state exists.

## Pills, Badges, and Chips

Canonical shape: `inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium`.

Common variants:

- Subtle accent: `bg-accent/10 text-accent`.
- Strong accent: `bg-accent px-3 py-1 text-accent-foreground`.
- Bordered accent: `border border-accent/30 bg-accent/10 text-accent`.
- Neutral: `bg-secondary text-muted-foreground`.
- Closed/disabled: `border border-border text-muted-foreground/40`.
- Success/completed: `bg-green-500/15 text-green-400`.

Pill icons are usually `h-3 w-3` or `h-3.5 w-3.5`.

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

- Interactive rows and important CTAs have at least a 44px tap target.
- Icon-only buttons and links have `aria-label` or equivalent hidden text.
- Active navigation/list items expose `aria-current`.
- Keyboard focus is visible on anchors, buttons, rows, and custom controls.
- Long titles in flex layouts have `min-w-0`, `truncate`, `line-clamp-*`, or `break-words` as appropriate.
- Body and metadata colors use token pairs with sufficient contrast.

## When in Doubt

1. Search for an existing page with the same UI role.
2. Reuse the closest partial before adding a new component.
3. Copy established class strings exactly when the visual role matches.
4. Use CSS variables and Tailwind token classes, not raw hex values.
5. Verify desktop and mobile screenshots in both themes for UI-heavy changes.
6. If a new pattern is truly needed, document why in the issue or implementation notes.
