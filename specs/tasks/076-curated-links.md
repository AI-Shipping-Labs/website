# 076 - Curated Links

**Status:** pending
**Tags:** `content`, `admin`, `frontend`
**GitHub Issue:** [#76](https://github.com/AI-Shipping-Labs/website/issues/76)
**Specs:** 06 (curated links section)
**Depends on:** [071-access-control](071-access-control.md)
**Blocks:** [092-github-content-sync](092-github-content-sync.md)

## Scope

- CuratedLink model: title, description, url, category, tags, required_level, sort_order
- `/resources` listing page: links grouped by category, tag filtering
- Each link shows title, description, external link icon; opens URL in new tab
- Gated links show lock icon and upgrade CTA instead of URL
- Admin CRUD for curated links

## Acceptance Criteria

- [ ] CuratedLink model with fields: title, description (short text), url, category (string), tags (string[]), required_level, sort_order, created_at
- [ ] `GET /resources` renders links grouped by category with category headers
- [ ] Each link shows: title, description, external link icon
- [ ] Clicking an open link opens the external URL in a new tab
- [ ] Gated links show lock icon instead of external link icon; clicking shows upgrade CTA instead of opening URL
- [ ] The actual URL is never exposed in HTML for gated links (not in href, data attributes, or JS)
- [ ] Tag filtering via clickable chips works
- [ ] Links within each category are sorted by sort_order
- [ ] Admin can create/edit/delete curated links with all fields
