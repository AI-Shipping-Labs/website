# Guest UI / Design Audit

Date: 2026-07-09
Scope: visual/design audit of the public guest-facing pages on production, reviewed against `_docs/design-system.md`. Companion to the same-day conversion audit (`2026-07-09-guest-ux-conversion-audit.md`); funnel/copy findings live there and are not repeated here.

Evidence: full-page desktop screenshots (1440px) of home, pricing, blog, events, courses, resources, downloads, projects, tutorials, about, activities, register, login, subscribe, article, course, workshop, and event detail; mobile (390px) home and pricing; dark-mode captures of home, pricing, blog.

Summary: dark mode and token usage are broadly healthy — no contrast or legibility failures found. The dominant problems are (a) the pricing page, where the Free tier's embedded registration form distorts card heights on desktop and the whole carousel height on mobile, and (b) the same concepts (tier requirement badges, gated cards, empty states, content cards) rendered several different ways across pages.

## Findings (ordered by severity)

1. Pricing cards: whitespace deserts from the embedded register form. `templates/payments/pricing.html` includes `accounts/includes/_inline_register_card.html` (compact) inside the Free card. All four tiers share one grid row, so the tall Free card forces Basic/Main/Premium to its height; their Join buttons pin to the bottom with a large empty gap above (Premium: 4 bullets, then roughly half a card of dead space). Most visible issue on the highest-intent page. Fix: stop stretching non-Free cards to the Free card height, or move the inline register form out of the grid into a row below the tiers.
2. Mobile pricing carousel inherits the Free-card height and has no swipe affordance. Visible Main card ends around 950px but the carousel reserves height to roughly 2700px, leaving a huge empty band before the footer. No dots/arrows/scrollbar indicator, so only the edge-peek signals that four tiers exist. Fix: intrinsic per-card height plus a dot indicator row (design-system: Breakpoints and Mobile Carousels).
3. Tier requirement badges are inconsistent in copy and color: "Membership: Main" accent pill (`templates/content/activities.html`), "Main or above" green+lock pill (events list/detail), "Basic or above" green pill (workshop, projects), plain tier chips (courses). One concept, three wordings, two colors. Pick one canonical requirement pill (design-system: Pills, Badges, and Chips).
4. Downloads empty state is hand-rolled (`templates/content/downloads_list.html` L130-140: bare icon + muted sentence, no `member_empty_state` partial), while tutorials uses the canonical `{% member_empty_state %}`. Replace with the partial (`title='No downloads yet'`, `icon='download'`, `kind='fresh'`; `kind='filter'` variant for tag-filtered empties).
5. Blog list: first row has no thumbnail while every other row does (`templates/content/blog_list.html`), so the list reads misaligned. Give the first card the same thumbnail slot or a consistent fallback cover.
6. Workshop detail inline flow diagram overflows the reading column and clips at the right edge (`templates/content/workshop_detail.html`). Wrap in `overflow-x-auto` (with `-mx-4 px-4` bleed) or allow wrapping.
7. Resources page: per-card type pill repeats the section heading ("Courses" pill inside the Courses section), and cards without descriptions stretch to the tallest neighbor leaving large empty cards (`templates/content/collection_list.html`). Drop redundant pills; let cards size to content.
8. Same content type rendered differently on home vs its own page: project cards are compact text cards on home but large cover-image cards on `/projects`; home shows 3 tiers while `/pricing` shows 4. Align treatments or document the intent.
9. Events list uses two treatments for one entity: Upcoming as large bordered cards (gated ones near-empty and tall), Past as compact rows (`templates/events/events_list.html`). Use one row/card system with state modifiers.
10. Courses page: 2 cards left-aligned in a 3-column grid leaves the right half empty (`templates/content/courses_list.html`). Center or cap columns when item count is below column count.
11. Event gated card does not reuse the standardized gated access card: plain `bg-secondary` box on events vs accent-bordered `_gated_access_card.html` on courses/workshops (`templates/events/_event_registration_card.html`). Reuse the partial.
12. Event description markdown leaks as raw dashes: the Mock Interviews event body renders "- 5 minutes: introduction - project deep dive -" as literal hyphens inside a paragraph (`templates/events/_event_description.html`). Matches the standing rule that event descriptions carry no markdown; fix the content and consider a renderer guard.
13. Accessibility: LinkedIn icon links on `/about` are roughly 28-32px squares, below the 44px tap-target minimum (design-system: Theme and Accessibility Expectations). Bump to `min-h-[44px] min-w-[44px]`.
14. The pre-footer "Build AI in public" newsletter block repeats on auth pages — on register it sits directly under a signup form; on `/subscribe` it duplicates the page form. Suppress on login/register/subscribe.

## Open product questions (need PM judgment, not design-system rules)

- Home ordering and length: tiers/pricing is the third block, before testimonials and most content, and the page is roughly 9,300px on desktop / 10,900px on mobile with four content catalogs plus pricing duplicated from `/pricing`. Is price-before-proof intended? Should home trim or collapse catalogs for first-time visitors?
- Should the Free tier keep a full inline registration form inside the pricing grid (findings 1-2), or become a single "Create free account" CTA that expands a form below the tier row? Changes conversion behavior; needs PM sign-off before the layout fix.
- Project generated cover images are near-identical dark templates across every card, producing monotony. Intentional uniformity or should covers vary?

## Out of scope

Content copy/data accuracy, the OG-image generation pipeline, authenticated member surfaces, and backend gating logic. Funnel and conversion findings are in `2026-07-09-guest-ux-conversion-audit.md`.
