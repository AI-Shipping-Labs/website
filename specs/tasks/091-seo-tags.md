# 091 - SEO: Tags, Filtering, and Conditional Components

**Status:** pending
**Tags:** `seo`, `frontend`, `admin`
**GitHub Issue:** [#91](https://github.com/AI-Shipping-Labs/website/issues/91)
**Specs:** 13 (tags, tag pages, conditional components sections)
**Depends on:** [072-articles](072-articles.md), [074-recordings](074-recordings.md), [075-projects](075-projects.md), [076-curated-links](076-curated-links.md), [077-downloads](077-downloads.md), [078-course-models-catalog](078-course-models-catalog.md), [083-events](083-events.md)
**Blocks:** —

## Scope

- Tags stored as string[] on all content tables, normalized on save (lowercase, hyphenated, no special chars)
- `/tags` page: all tags with content counts, sorted by count
- `/tags/{tag}` page: all content with that tag across all types, sorted by date, each item shows content type badge
- Tag filter chips on all listing pages (/blog, /courses, /recordings, /projects, /resources): clickable chips, multiple tag selection (AND logic via ?tag=X&tag=Y), active filters shown as removable chips
- TagRule model: tag, component_type, component_config (JSON), position (after_content/sidebar)
- When rendering content detail pages, check tag_rules and inject configured components
- Admin CRUD for tag rules

## Acceptance Criteria

- [ ] Tags normalized on save: lowercase, hyphenated, no special characters (e.g., "Machine Learning" → "machine-learning")
- [ ] `GET /tags` shows all tags with content count per tag, sorted by count descending
- [ ] `GET /tags/{tag}` shows all content with that tag across all content types, sorted by date descending; each item shows content type badge
- [ ] Tag filter chips on listing pages: /blog, /courses, /recordings, /projects, /resources, /downloads
- [ ] Multiple tag selection with AND logic via ?tag=X&tag=Y query params
- [ ] Active tag filters shown as removable chips (click X to remove)
- [ ] TagRule model with fields: tag, component_type, component_config (JSON), position (after_content/sidebar)
- [ ] When rendering content detail page, matching TagRules inject configured components at specified position
- [ ] Admin CRUD for TagRules
