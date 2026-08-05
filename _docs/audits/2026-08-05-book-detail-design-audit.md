# Book Club book detail page — design system audit

Page under review: `templates/bookclub/book_detail.html`, rendered at `http://localhost:8009/books/inference-engineering` (`?view=member` / `?view=guest`).
Authority: `_docs/design-system.md`. Siblings for context: `templates/bookclub/index.html`, `templates/bookclub/book_secondary.html`.
Screenshots: `audit-book-member.png`, `audit-book-guest.png` in this scratchpad (dark theme, 1440px).

## 1. Critique

### 1.1 The core violation: the two-column main+sidebar body

`templates/bookclub/book_detail.html:64` opens `<div class="mt-10 grid gap-8 lg:grid-cols-3">`, with the chapter roadmap in `lg:col-span-2` (line 67) and an `<aside>` stacking four cards (line 138): progress/join, leaderboard, summaries, meetings.

The design system restricts this in three places:

Variable-height detail cards (`_docs/design-system.md:179`):

> Do not force related detail content into competing multi-column layouts when sections can have very different content height. A single vertical flow is often clearer than side-by-side blocks with awkward empty space.

This is exactly the failure mode visible in the screenshot: the sidebar exhausts its content around the fourth chapter card, leaving roughly a third of the page with a dead right column while eight chapter cards keep scrolling on the left.

Hero Layout (`_docs/design-system.md:193-195`):

> Page heroes are single-column ... A side column is allowed only for a functional artifact the visitor can act on immediately, such as a registration form, video embed, or event-registration card.

The sanctioned side-column exception is one immediately actionable artifact. This sidebar is four stacked cards — a stats widget, a ranking, an index of summaries, and a meetings list — none of which is the single functional artifact the exception describes. It is a dashboard rail, not a registration card.

Width tiers (`_docs/design-system.md:130-137`): the tier table assigns sidebar-plus-content layouts to the Frame tier:

> | Frame | `max-w-7xl` | Index, grid, and listing pages; marketing pages; the member dashboard; sidebar-plus-content layouts. |

The page uses `max-w-5xl` (`book_detail.html:14`), the Detail tier, defined as "media embed plus metadata plus cards or CTAs". So the current page is doubly inconsistent: a Detail-tier width carrying a Frame-tier sidebar layout. Squeezing a 3-column grid into 64rem also produces a cramped ~20rem rail, which is why every sidebar card had to invent compact `p-5` padding (see 1.3). The correct resolution is not to widen to 7xl — it is to drop the sidebar and keep the Detail tier, which fits this page's shape ("media embed plus metadata plus cards or CTAs") precisely.

Comparison and progress lists (`_docs/design-system.md:186-188`) makes the leaderboard specifically wrong as a sidebar widget:

> Use tables or table-like rows for member progress, status comparisons ... Put the current user's own record first when the page is a signed-in member surface. Keep table columns predictable and scannable: identity first, then primary metric/progress, then status ...

The leaderboard (`book_detail.html:182-197`) is a member-progress comparison. It should be table-like rows in the main flow; in the prototype the signed-in viewer sits at rank 4 mid-list (highlighted, but not surfaced first — a "your position" treatment above or pinned atop the rows would satisfy the rule without breaking rank order).

### 1.2 Radius drift

`_docs/design-system.md:203`:

> Radius drift is role drift. `rounded-lg` is anything repeated in a grid or list; `rounded-xl` is a singular spotlight surface only ...; `rounded-2xl` is full-page focus panels (out of scope here).

`rounded-2xl` appears six times, all wrong:

| Line | Element | Correct radius |
|---|---|---|
| `book_detail.html:75` | Chapter card, repeated in a list of 8 | `rounded-lg` |
| `book_detail.html:142` | Your-progress card | `rounded-lg` |
| `book_detail.html:173` | Guest join-CTA card | `rounded-lg` (or removed, see 1.4) |
| `book_detail.html:182` | Leaderboard card | `rounded-lg` |
| `book_detail.html:200` | Summaries card | `rounded-lg` |
| `book_detail.html:229` | Meetings card | `rounded-lg` |

The spotlight exception (`design-system.md:217`) covers only tier/pricing cards and the home featured-sprint card, and even those are `rounded-xl`, not `2xl`. The drift was likely copied from the sibling `index.html:30` featured-book card (`rounded-2xl` — itself non-compliant), which is exactly the trap `design-system.md:42` warns about: "Existing usage is not precedent by itself."

### 1.3 Card role and padding drift

Card padding is bound to role (`design-system.md:149-159`, role table at 209-215). Every card on this page uses `p-5`, which is not a sanctioned card padding for any of these roles:

- Chapter cards (line 75, `p-5`): these are a repeated content list. Closest sanctioned roles: content-catalog (`p-4 sm:p-5`) if they stay cards, or table-like/list rows per the progress-list rule.
- Sidebar info cards — leaderboard, summaries, meetings (lines 182, 200, 229, all `p-5`): info/static role is `p-6` via `templates/content/_info_card_classes.html` (`design-system.md:302`), which is the mandated owner for static-card class strings; none of them use it.
- Progress card (line 142, `p-5`): same — info/static, `p-6`.

Other role violations:

- Hand-rolled status pills (lines 90-94): "Read" / "Reading now" / "Upcoming" are inlined `rounded-full border ... px-2 py-0.5` spans. `design-system.md:520`: "Never inline a pill whose meaning an owning tag covers, even if the classes would be identical." `{% member_status_badge %}` covers success/active/upcoming tones — and `book_secondary.html:28-30` uses it correctly, proving the owner was known and available.
- Raw emerald palette (lines 79, 90, 118, and `index.html:91`): the tone table says success is green, and the compact-badge recipe (`design-system.md:45-52`) is `bg-<color>-500/15 text-<color>-800 dark:text-<color>-400`, owned by `member_badges`. `text-emerald-400` with no light-theme split fails the 4.5:1 requirement in light mode.
- Hand-rolled button: line 117's "Chapter summary" anchor is styled as a button (`rounded-md border border-border px-2 py-1`) without `{% button_classes %}`. `design-system.md:323`: "New or edited templates must not hand-roll equivalent button class strings." The RSVP actions (lines 241, 248) are bare text links doing explicit-action duty on a callout-ish row; they should be `{% button_classes 'secondary' size='sm' %}`.
- Fake toggle (lines 164-169): a `<label>` wrapping decorative spans — no `<input>`, no keyboard reachability, no focus ring. Violates `design-system.md:587` ("Every keyboard-reachable custom surface needs a visible focus state") by not even being reachable. Prototype or not, this is a hand-rolled control with no owner.
- Eyebrow (line 27, also `index.html:17`): `text-sm uppercase tracking-wide text-muted-foreground` vs the spec `text-sm font-medium uppercase tracking-widest text-accent` (`design-system.md:72`), and `design-system.md:56`: "Eyebrows use `tracking-widest`, never `tracking-wider`" — `tracking-wide` is equally off-scale.
- Section heading (line 69): "Reading roadmap" is `text-lg font-semibold` — that is the large card title pattern. A page section h2 is `text-2xl font-semibold tracking-tight sm:text-3xl` (`design-system.md:65`). Its "3/8 chapters read" counter is pinned opposite the title (`justify-between`), which the header contract reserves for one clear destination CTA rendered with `{% button_classes %}` (`design-system.md:161-166`); metadata belongs under the title.
- Link chips in the header (line 36): hand-rolled `rounded-full border border-border bg-card` chips with a hover state; the canonical clickable tag chip is `bg-secondary` based (`design-system.md:542`) — and these carry no focus-visible ring.

### 1.4 Gating

Lines 44-61 hand-roll a guest gate aside. `design-system.md:578`: "Every paid/tier-gated content block renders `templates/content/_gated_access_card.html`; do not create another gated-card dialect." The Partials index (`design-system.md:294`) makes that owner mandatory. Additional problems:

- Vocabulary: line 52 says "This book club is for Main members"; the sanctioned vocabulary is "Main or above required" (`design-system.md:537`).
- Duplication: the guest also gets a second, competing "Become a member" CTA card in the sidebar (lines 173-178) — two hand-rolled gate dialects on one page, where the system prescribes exactly one owned card.

### 1.5 What the page does right

For fairness: it loads `{% button_classes %}` and uses it for most buttons, uses `{% member_access_badge %}` in the header (line 28), uses token colors nearly everywhere, and keeps `gap-8` on a page-layout grid (allowed by `design-system.md:146`, though the grid itself is not). The component-level vocabulary was largely followed; the layout-level rules were not.

## 2. Compliant reorganization

Keep the Detail tier (`max-w-5xl`) and make the body one vertical flow. Section order matches the member's task priority: what to do now, then the roadmap, then time-sensitive meetings, then reference material, then social comparison.

| # | Section | Role / owner | Notes |
|---|---|---|---|
| 1 | Back link + book header | Existing detail-hero pattern | Cover + title + meta is fine: the Detail tier explicitly covers "media embed plus metadata". Fix the eyebrow to spec; convert link chips to canonical clickable tag chips. |
| 2 | Guest gate (guest only) | `templates/content/_gated_access_card.html` | Directly under the header, full width. The only gate CTA on the page; delete the sidebar join card entirely. View supplies `required_tier_name`; copy uses "Main or above required". |
| 3 | Your progress (member only) | Callout/action, inline action row (`design-system.md:237`, reference `_starting_soon_card.html`) | One row: left = "3 of 8 chapters read · 2-week streak"; right = the single next action as `{% button_classes 'primary' %}` ("Mark Ch. 3 as read" or "RSVP for Thursday"). Drop the hand-rolled SVG donut — the number plus the roadmap's per-chapter markers carry the same information. The reading-profile toggle becomes a real form control here or moves to the reader-profile/account page. |
| 4 | Reading roadmap | Section h2 (`text-2xl font-semibold tracking-tight sm:text-3xl`) on its own row, counter as `text-sm text-muted-foreground` beneath it; chapters as `rounded-lg` rows | Full width. Chapter entries stay a vertical list (they are variable-height with notes), `rounded-lg border border-border bg-card p-4 sm:p-5`. Status pills via `{% member_status_badge %}` ("Read" success/green, "Reading now" accent-active, "Upcoming" blue/muted). All actions via `{% button_classes %}`; per-chapter primary only on the current chapter. Full width gives the note excerpts and action rows the room the 2/3 column denied them. |
| 5 | Weekly meetings | Section with `templates/includes/_list_row.html`-style rows or compact-rail cards | Two upcoming discussion rows, date via the event datetime helpers when this leaves prototype, RSVP as `{% button_classes 'secondary' size='sm' %}`. |
| 6 | Summaries | Info/static card via `templates/content/_info_card_classes.html` + `bg-card`, or plain list rows | Per-chapter summary links as list rows. The "Compile Ch. 3 summary" affordance is an organizer action — gate it to organizers or move it to Studio; a member-visible button that most members cannot meaningfully use is a broken affordance. |
| 7 | Leaderboard | Table-like rows per Comparison and progress lists (`design-system.md:186-189`) | Top rows with identity first, then chapters-read, wrapped in `overflow-x-auto` if columns grow. Signed-in viewer's own row surfaced first (a "Your position" row above the top 5 preserves rank order while satisfying the rule). "View all" as a narrative discovery link below the table (`mt-2 inline-flex items-center gap-2 text-sm font-medium text-accent hover:underline` + `arrow-right`, `design-system.md:168-170`), not pinned opposite the heading. |

CTA inventory after the reorg: guest sees exactly one gate card (section 2); member sees exactly one primary action (section 3) plus per-row secondaries. No content is lost relative to the sidebar version — it is re-ranked instead of parallelized.

## 3. Root cause: why a two-column page got built

Candidly:

1. Pattern-matching a generic dashboard archetype. "Progress ring + leaderboard + upcoming events in a right rail" is the stock layout of every fitness/learning app, and the builder reached for it as the default shape for "page with a main list and supporting widgets". The design system anticipates and forbids exactly this shape (variable-height multi-column, progress-as-widget instead of progress-as-table), but the archetype was applied before the rules were consulted.
2. Component-level reading, layout-level skipping. The template loads `accounts_extras` and `member_badges`, uses `{% button_classes %}` and `{% member_access_badge %}` — clear evidence the builder read the Buttons and Badges sections. But the violated rules (Variable-height detail cards, Hero Layout side-column exception, the width-tier table's "sidebar-plus-content" cell, Comparison and progress lists) all live in Spacing and Layout / Hero Layout prose. The builder consumed the design system as a parts catalog ("which button class do I use?") and never as a layout contract ("am I allowed a second column at all?"). Skimming for lookups instead of reading the layout sections in full is the single behavior that produced the violation.
3. Sibling drift treated as precedent. `rounded-2xl` and the off-spec eyebrow were most plausibly copied from `bookclub/index.html` (featured card, line 30), which drifted first. `design-system.md:42` warns "Existing usage is not precedent by itself", but nothing in the flow forced that check — copying a sibling felt like compliance.
4. No design gate in the prototype flow. The `ui-prototype` path deliberately skips the PROCESS.md pipeline (PM/SWE/tester), so nothing between "builder writes template" and "user looks at it" reads the design system. The `designer` agent exists precisely for screenshot-backed design-system audits and was never in the loop. Automated enforcement also has a hole: `content/tests/test_container_widths.py` polices outer width tiers (which the page technically passes at `max-w-5xl`) but nothing polices column structure, radius roles, or hand-rolled gate cards — the two-column body sails through every existing check.

What would have caught it:

- A layout declaration step in the `ui-prototype` skill checklist, before any markup: "State the width tier and the column plan in one sentence. A side column requires citing the sanctioned exception (functional artifact per Hero Layout) by name; four stacked widget cards is not one." Writing "5xl + 3-column grid with a widget rail" next to the tier table makes the contradiction self-evident in a way that writing Tailwind classes never does.
- A mandatory `designer`-agent pass on any new public page, prototype or not — prototypes especially, since prototype layouts get ratified into specs. One screenshot-backed audit against `_docs/design-system.md` flags `lg:grid-cols-3` + `aside` and six `rounded-2xl`s in minutes.
- A cheap grep-lint extension of the existing ratchet: flag `rounded-2xl` in `templates/` outside an allowlist, flag `lg:grid-cols-[23]` with a sibling `<aside>` on non-Frame-tier pages, and flag gate-like copy ("Become a member" + lock icon) outside `_gated_access_card.html`. Each is a five-line check that turns a prose rule into a failing test.

The top fix is the first one: make declaring the layout — tier, column count, and the exception being invoked if any — a required, explicit step before building. Every other violation on this page (radius, padding, duplicate CTAs) descends from the moment the sidebar was assumed.
