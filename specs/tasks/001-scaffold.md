# 001 - Scaffold Django Project and Reproduce aishippinglabs.com

**Status:** done
**Tags:** `infra`, `frontend`
**Specs:** All (foundational)
**GitHub Issue:** #1
**Depends on:** —
**Blocks:** [067-user-auth](067-user-auth.md), [068-membership-tiers](068-membership-tiers.md), [071-access-control](071-access-control.md), [072-articles](072-articles.md), [073-video-player](073-video-player.md), [074-recordings](074-recordings.md), [075-projects](075-projects.md), [076-curated-links](076-curated-links.md), [077-downloads](077-downloads.md)

## Scope

- Initialize Django project with `uv`
- Create all apps: `website`, `accounts`, `payments`, `content`, `integrations`, `email`
- Folder-based `models/`, `tests/`, `admin/`, `views/` structure in each app
- Base template with Tailwind CDN, header, footer
- Reproduce all pages from aishippinglabs.com: homepage (all sections), about, activities, blog listing/detail, recordings listing/detail, projects listing/detail, collection, tutorials listing/detail
- Models for existing content (articles, recordings, projects, curated links)
- Initial content populated via fixtures or data migration
- Responsive design (mobile + desktop)

## Acceptance Criteria

- `uv run python manage.py runserver` starts without errors
- All pages from aishippinglabs.com reproduced with matching content and styling
- Homepage renders all sections: hero, about, tiers, testimonials, recordings, blog, projects, collection, newsletter, FAQ
- All listing and detail pages work
- Navigation works between all pages
- Tailwind CSS loaded via CDN
- All tests pass with 85%+ coverage
- Playwright visual regression tests pass
- All apps use `models/` and `tests/` folder patterns
