# 090 - SEO: Structured Data, Meta Tags, Sitemap

**Status:** pending
**Tags:** `seo`, `frontend`
**GitHub Issue:** [#90](https://github.com/AI-Shipping-Labs/website/issues/90)
**Specs:** 13 (structured data, OG, meta, sitemap sections)
**Depends on:** [072-articles](072-articles.md), [074-recordings](074-recordings.md), [075-projects](075-projects.md), [078-course-models-catalog](078-course-models-catalog.md), [083-events](083-events.md)
**Blocks:** —

## Scope

- JSON-LD structured data on every public page: Article, Course, LearningResource, Event, VideoObject, Organization (homepage)
- structured_data(content) helper returning JSON-LD for any content type
- OpenGraph tags on every page: og:title, og:description, og:image, og:url, og:type, og:site_name
- Twitter Card tags: twitter:card, twitter:title, twitter:description, twitter:image
- og_tags(content) helper
- Every page: `<title>` in "{Page Title} | AI Shipping Labs" format, `<meta description>` (max 160 chars), `<link rel="canonical">`
- `/sitemap.xml`: dynamically generated, includes all public pages (open articles, course catalog/detail, events, tag pages, static pages); excludes gated content detail pages

## Acceptance Criteria

- [ ] Every public content page includes JSON-LD structured data in `<script type="application/ld+json">` in `<head>`
- [ ] JSON-LD types used: Article (blog posts), Course (courses), LearningResource (recordings), Event (events), VideoObject (recordings with video), Organization (homepage)
- [ ] `structured_data(content)` template tag/helper returns correct JSON-LD for any content type
- [ ] Every page includes OpenGraph meta tags: og:title, og:description, og:image, og:url, og:type, og:site_name
- [ ] Every page includes Twitter Card meta tags: twitter:card (summary_large_image), twitter:title, twitter:description, twitter:image
- [ ] `og_tags(content)` template tag/helper generates correct OG tags
- [ ] Every page has: `<title>` in "{Page Title} | AI Shipping Labs" format, `<meta name="description">` (max 160 chars), `<link rel="canonical">`
- [ ] `GET /sitemap.xml` dynamically generated; includes all public pages: open articles, course catalog, course detail, events, tag pages, static pages
- [ ] Sitemap excludes gated content detail pages (where required_level > 0)
- [ ] `[HUMAN]` Validate structured data with Google Rich Results Test
