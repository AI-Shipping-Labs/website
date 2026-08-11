# Workshop catalog card-density research

Date: 2026-08-11
Surface: `/workshops/catalog`
Decision: approved for implementation

## Problem

The curated topic controls are useful and are now at an appropriate level of
granularity. The remaining usability problem is the result presentation: a
three-column wall of narrow, equally weighted bordered cards makes titles,
summaries, access, skill, topic, instructor, date, media, and arrows compete.
The chrome-to-content ratio is high and descriptions become difficult to scan.

## Evidence

- Nielsen Norman Group's [Anatomy of a List Entry](https://www.nngroup.com/articles/list-entries/)
  recommends selecting the few attributes most users need, placing them
  consistently, and using typography and whitespace to establish hierarchy.
- Baymard's [list-item design research](https://baymard.com/blog/list-item-design-ecommerce)
  similarly emphasizes consistent attribute placement and visually distinct
  information without giving every datum its own heavy row or treatment.
- Baymard's [listing-information research](https://baymard.com/blog/product-listing-information)
  supports a high signal-to-noise ratio: primary identifiers should dominate
  while secondary information is subdued.
- Material Design's [canonical layouts](https://m3.material.io/foundations/layout/canonical-examples/overview)
  provide the feed/list vocabulary used here. The exact one-column choice is a
  product judgment based on this archive being text-led rather than image-led.

These are general listing and comparison findings, not workshop-specific A/B
tests. They strongly support the hierarchy and information-reduction choices;
the rendered mockup supplied the product validation for the exact layout.

## Approved direction

Use a calm, single-column editorial feed at `max-w-3xl`:

1. Keep `All` plus the curated topic pills above the results.
2. Keep one access badge and one primary-topic eyebrow in the lead signal row.
3. Make the title the strongest element (`text-lg` / `sm:text-xl`).
4. Clamp the description to two readable lines.
5. Put date, instructor, and optional skill level in one muted metadata row.
6. Keep one right-aligned navigation arrow and make the entire row clickable.
7. Separate entries with low-contrast dividers and generous vertical padding,
   not rounded borders around every item.
8. Render workshop media only for an authored cover or custom banner. Never
   reserve a blank slot or show generated/placeholder media in the public row.

The `/workshops` landing keeps the existing `max-w-5xl` frame and shows four
items total: the newest workshop as one prominent full-width card, then the
next three workshops in a left-aligned three-cell row, followed by a `See all
workshops` button. The preview limit must therefore remain four; a limit of
three creates an awkward two-card remainder after extracting the featured
item. Only the full `/workshops/catalog` archive uses the `max-w-3xl`
editorial feed.

## Alternatives considered

### Two-column roomy cards

This reduces compression but preserves the repeated box wall and narrow summary
lines. It was rejected because it treats the symptom rather than the hierarchy.

### Featured workshop plus grid

This creates a strong promotional entry point but makes one archive item
structurally exceptional and weakens consistent comparison. The landing page
already owns the featured-workshop pattern, so repeating it in the archive was
rejected.

## Tradeoff and follow-up

The one-column archive is longer. That is accepted in exchange for calmer
scanning. Pagination or progressive loading should be considered only when
catalog growth makes page length a measured problem; it is outside this change.

## Follow-on: blog archive

After reviewing the implemented workshop archive, the same direction was
approved for `/blog`. The blog keeps its `max-w-3xl` topic-filtered feed but
removes rounded card boxes and all thumbnail/fallback slots. Articles use the
same divider-led `_content_card.html` editorial mode and the same hierarchy:
access plus curated topic, title, two-line summary, muted metadata, and one
navigation arrow.

Article detail pages are text-first as well. The large visible cover above an
article header is omitted; `cover_image_url` remains available to Open Graph,
Twitter, structured-data, and other sharing metadata.

The shared related-content rail follows the same hierarchy everywhere: compact
divider-led rows rather than a secondary card grid, a muted content-type
eyebrow, optional access tier, title, two-line description, date, and one
arrow. Event detail and event-series pages use `max-w-3xl` so their single
column and related rows retain the same readable line length. Blog, tutorial,
project, workshop, and event detail pages all include the same partial with no
page-specific variant flags; each uses `max-w-3xl`.
