# 071 - Access Control and Content Gating

**Status:** pending
**Tags:** `auth`
**GitHub Issue:** [#71](https://github.com/AI-Shipping-Labs/website/issues/71)
**Specs:** 03
**Depends on:** [068-membership-tiers](068-membership-tiers.md)
**Blocks:** [072-articles](072-articles.md), [074-recordings](074-recordings.md), [075-projects](075-projects.md), [076-curated-links](076-curated-links.md), [077-downloads](077-downloads.md), [078-course-models-catalog](078-course-models-catalog.md), [083-events](083-events.md), [088-voting](088-voting.md)

## Scope

- Add `required_level` integer field (default 0) to all content tables
- Server-side access check on every content-serving view: user.tier.level >= content.required_level
- Gated responses by content type:
  - Articles: excerpt teaser + blurred placeholder + upgrade CTA
  - Courses: syllabus visible, unit content gated
  - Recordings/resources: title+description visible, media gated
  - Events: detail visible, registration gated
- Admin visibility dropdown on all content forms (Open / Basic+ / Main+ / Premium)
- Anonymous visitors treated as level 0
- Never return 404 for gated content — always show teaser + CTA

## Acceptance Criteria

- [ ] Every content table (articles, courses, recordings, projects, curated_links, downloads, events) has a `required_level` integer column defaulting to 0
- [ ] A reusable access check (mixin, decorator, or utility) compares user.tier.level >= content.required_level
- [ ] Anonymous user (not logged in) viewing a level-10 article sees: excerpt (first 200 chars) + blurred placeholder + CTA "Upgrade to Basic to read this article" linking to /pricing
- [ ] Free user (level 0) viewing a level-20 recording sees: title + description + CTA "Upgrade to Main to watch this recording" (video not rendered)
- [ ] Main user (level 20) viewing a level-20 article sees the full content
- [ ] Gated content never returns 404 — always returns 200 with teaser
- [ ] Admin content forms include a "Visibility" dropdown with options: "Open (everyone)" (0), "Basic and above" (10), "Main and above" (20), "Premium only" (30)
- [ ] Default visibility is "Open (everyone)" for new content
