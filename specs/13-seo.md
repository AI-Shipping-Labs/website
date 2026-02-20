# 13 - SEO and Content Organization

## Overview

Tags for filtering and organization. Structured data (JSON-LD, OpenGraph) on every public page for search engine and social media discoverability.

## Tags

### Data Model

Tags are stored as a `string[]` on each content item (articles, courses, recordings, projects, curated links, downloads, events). No separate tags table — tags are free-form strings, lowercased, hyphenated (e.g. `"ai-engineering"`, `"python"`, `"tutorial"`).

### Tag Pages

- `/tags` — page listing all tags with content counts, sorted by count descending
- `/tags/{tag}` — page listing all published content items with that tag, across all content types, sorted by date
- Each item shows: content type badge (Article, Course, Recording, etc.), title, excerpt/description, date

### Tag Filtering

On listing pages (`/blog`, `/courses`, `/recordings`, `/projects`, `/resources`), tags appear as clickable filter chips. Clicking a tag filters the list to items with that tag. Multiple tags can be selected (AND logic). Active filters shown as removable chips above the list.

### Conditional Components

Admin can configure tag-based rules for showing inline CTAs:

```
TagRule:
  id: uuid
  tag: string                     # e.g. "ai-engineer"
  component_type: string          # e.g. "roadmap_signup", "course_promo", "download_cta"
  component_config: jsonb         # e.g. {"course_slug": "python-data-ai", "cta_text": "Start learning"}
  position: enum                  # "after_content", "sidebar"
```

When rendering an article/resource with a matching tag, inject the specified component at the specified position. Example: any article tagged `"ai-engineer"` gets a "Python for Data & AI Engineering" course promo card after the article body.

## Structured Data

### JSON-LD

Every public page includes a `<script type="application/ld+json">` tag. Schema by page type:

| Page | Schema.org type | Key fields |
|---|---|---|
| Article | `Article` | `headline`, `author.name`, `datePublished`, `dateModified`, `description`, `image`, `publisher` |
| Course | `Course` | `name`, `description`, `provider.name`, `offers.price`, `offers.priceCurrency` |
| Course unit | `LearningResource` | `name`, `description`, `video.embedUrl` (if has video) |
| Event | `Event` | `name`, `startDate`, `endDate`, `location`, `offers`, `organizer` |
| Recording | `VideoObject` | `name`, `description`, `embedUrl`, `duration`, `uploadDate` |
| Home | `Organization` | `name`, `url`, `logo`, `description`, `sameAs` (social links) |

### OpenGraph Tags

Every page includes in `<head>`:

```html
<meta property="og:title" content="{page title}">
<meta property="og:description" content="{page description or excerpt}">
<meta property="og:image" content="{cover image URL or site default}">
<meta property="og:url" content="{canonical URL}">
<meta property="og:type" content="article|website|event">
<meta property="og:site_name" content="AI Shipping Labs">
```

Plus Twitter Card tags:

```html
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{page title}">
<meta name="twitter:description" content="{page description}">
<meta name="twitter:image" content="{cover image URL}">
```

### Meta Tags

Every page includes:

```html
<title>{Page Title} | AI Shipping Labs</title>
<meta name="description" content="{excerpt or description, max 160 chars}">
<link rel="canonical" href="{canonical URL}">
```

### Sitemap

Generate `/sitemap.xml` that includes all public (open) pages:
- All published articles with `required_level = 0`
- All course catalog and detail pages (always public)
- All events
- Tag pages
- Static pages (home, pricing, about)

Gated content (required_level > 0) detail pages are NOT included in the sitemap (they show a gated response to crawlers). Course detail pages ARE included because they always show public content.

## Requirements

- R-SEO-1: Store tags as `string[]` on all content tables. Normalize on save: lowercase, replace spaces with hyphens, strip special characters.
- R-SEO-2: Implement `/tags` and `/tags/{tag}` pages. `/tags/{tag}` queries all content tables for items with the matching tag and merges results sorted by date.
- R-SEO-3: On all listing pages, render tag filter chips. Clicking a chip adds `?tag=X` to the URL. Support multiple: `?tag=ai-engineering&tag=python`. Filter server-side.
- R-SEO-4: Create `tag_rules` table. When rendering a content detail page, query `tag_rules` for any matching tags and inject the configured component.
- R-SEO-5: Implement a `structured_data(content)` helper that returns the JSON-LD object for a given content item. Include it in the `<head>` of every public content page.
- R-SEO-6: Implement an `og_tags(content)` helper that returns OpenGraph and Twitter Card meta tags. Include in `<head>` of every page.
- R-SEO-7: Every page has a `<title>` (format: `{Page Title} | AI Shipping Labs`), `<meta name="description">` (max 160 chars from excerpt/description), and `<link rel="canonical">`.
- R-SEO-8: Generate `/sitemap.xml` dynamically. Include all public pages. Regenerate on content publish (or serve dynamically with caching).
