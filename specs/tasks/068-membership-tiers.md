# 068 - Membership Tiers

**Status:** pending
**Tags:** `payments`, `admin`, `frontend`
**GitHub Issue:** [#68](https://github.com/AI-Shipping-Labs/website/issues/68)
**Specs:** 01
**Depends on:** [001-scaffold](001-scaffold.md)
**Blocks:** [067-user-auth](067-user-auth.md), [069-stripe-payments](069-stripe-payments.md), [071-access-control](071-access-control.md)

## Scope

- Tier model: slug, name, level, price_eur_month, price_eur_year, stripe_price_id_monthly, stripe_price_id_yearly, description, features list
- The "Most Popular" badge on Main is just a frontend flag in the template, not a DB field
- Both monthly and yearly billing options for each paid tier
- Seed the four tiers on first deploy via data migration:

| Slug | Name | Level | Monthly | Yearly |
|------|------|-------|---------|--------|
| `free` | Free | 0 | — | — |
| `basic` | Basic | 10 | €20/month | €200/year |
| `main` | Main | 20 | €50/month | €500/year |
| `premium` | Premium | 30 | €100/month | €1000/year |

- Levels use multiples of 10 (0, 10, 20, 30) to allow inserting tiers in between later
- Tiers are cumulative: each tier includes everything from the tier below
- **Free (level 0):** newsletter emails, access to open content (required_level = 0). No community, no live sessions, no courses, no personalized feedback.
- **Basic (level 10):** exclusive articles, tutorials with code examples, AI tool breakdowns, research notes, curated social posts
- **Main (level 20):** everything in Basic + Slack community, group coding sessions, project-based learning, hackathons, career discussions, personal brand guidance, topic voting
- **Premium (level 30):** everything in Main + all mini-courses, mini-course topic voting, resume/LinkedIn/GitHub teardowns
- Pricing page at `/pricing` showing all four tiers in a comparison grid
- Main tier shows "Most Popular" badge
- Pricing page has monthly/yearly toggle; yearly shows savings
- Free tier shows "Subscribe" button, paid tiers show "Join" button
- Access check pattern: user.tier.level >= content.required_level (enforcement in [071](071-access-control.md))

## Acceptance Criteria

- [ ] Running `manage.py migrate` creates Tier table and seeds exactly 4 rows: free (level 0), basic (level 10), main (level 20), premium (level 30)
- [ ] Each tier has: slug, name, level, price_eur_month, price_eur_year, stripe_price_id_monthly, stripe_price_id_yearly, description, features (list)
- [ ] `GET /pricing` renders a 4-column comparison grid with tier names, prices, and feature lists
- [ ] Monthly/yearly toggle switches displayed prices between €20/€50/€100 per month and €200/€500/€1000 per year
- [ ] Main tier card shows "Most Popular" badge (hardcoded in template)
- [ ] Free tier shows "Subscribe" button; paid tiers show "Join" button
- [ ] "Join" buttons are present and clickable (can be placeholder hrefs until [069](069-stripe-payments.md))
- [ ] Pricing grid is responsive: stacks vertically on mobile, horizontal on desktop
- [ ] Tier data is editable in Django admin
