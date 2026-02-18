# 03 - Access Control

## Overview

Every content item has a `required_level` (0-3). A user can view it if `user.tier.level >= content.required_level`.

## Visibility Levels

| Level | Name | Who can see |
|---|---|---|
| 0 | Open | Everyone, including anonymous visitors |
| 1 | Basic+ | Basic, Main, Premium |
| 2 | Main+ | Main, Premium |
| 3 | Premium | Premium only |

Note: there is no separate "Free registered only" level. Level 0 (Open) is visible to anonymous visitors. Free registered users have level 0 same as anonymous — the difference is they are known (have email, can receive emails, can upgrade). If a "registered only" gate is needed in the future, add level 0.5 or a boolean flag.

## Data Model

Every content table (articles, courses, recordings, resources, events, downloads) includes:

```
  required_level: int  # 0, 1, 2, or 3. Default: 0 (open)
```

## Server-Side Check

Every content-serving endpoint and page render must check:

```python
if content.required_level > 0 and (not user or user.tier.level < content.required_level):
    return gated_response(content, user)
```

Where `gated_response` returns:
- For articles: the first 200 characters of the body as a teaser + a CTA banner with "Upgrade to {required_tier} to read this article" + link to `/pricing`
- For courses: the course detail page (title, description, syllabus) is always visible. Individual unit content is gated. Show "Unlock this course with {required_tier}" + link to `/pricing`
- For recordings/resources: the title and description are visible. The video/file is gated. Show "Upgrade to {required_tier} to watch/download"
- For events: the event detail is always visible. The "Register" button is gated. Show "Upgrade to {required_tier} to join this event"

## Admin UI

When creating or editing any content item, the admin sees a dropdown:
- "Open (everyone)" — `required_level = 0`
- "Basic and above" — `required_level = 1`
- "Main and above" — `required_level = 2`
- "Premium only" — `required_level = 3`

Default is "Open (everyone)".

## Requirements

- R-ACL-1: Add `required_level` integer column (default 0) to every content table: `articles`, `courses`, `recordings`, `resources`, `curated_links`, `downloads`, `events`.
- R-ACL-2: On every content-serving endpoint, check `user.tier.level >= content.required_level` server-side before returning full content.
- R-ACL-3: If the check fails, return a gated response with a teaser and a CTA linking to `/pricing`. Never return a 404 for gated content.
- R-ACL-4: The admin form for every content type includes a "Visibility" dropdown with the four options above.
- R-ACL-5: Anonymous visitors (`user = null`) are treated as level 0. They see all Open content and CTAs for everything else.
