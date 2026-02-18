# 01 - Membership Tiers

## Overview

Four tiers (one free, three paid) with cumulative access. Each tier includes everything from the tier below.

## Data Model

```
Tier:
  slug: string (unique)       # "free", "basic", "main", "premium"
  name: string                 # "Free", "Basic", "Main", "Premium"
  level: int                   # 0, 1, 2, 3 — used for access checks: user.tier.level >= content.required_level
  price_eur_year: int | null   # null for free, 200, 500, 1000
  stripe_price_id: string | null
  description: string
  features: string[]           # list of feature descriptions for the pricing page
  is_popular: bool             # true for Main tier, shows "Most Popular" badge
```

## Tier Definitions

### Free (level 0)

- No payment required
- User registers with email address only
- Receives newsletter emails
- Can access content where `required_level = 0`
- Cannot access community (Slack), live sessions, courses, or personalized feedback

### Basic (level 1) — 200 EUR/year

- Can access content where `required_level <= 1`
- Content included: exclusive articles, tutorials with code examples, AI tool breakdowns, research notes, curated social posts
- No community access, no live sessions, no courses, no personalized feedback

### Main (level 2) — 500 EUR/year

- Can access content where `required_level <= 2`
- Everything in Basic plus: Slack community access, group coding sessions, project-based learning, hackathons, career discussions, personal brand guidance, productivity tips, topic voting
- No courses, no personalized feedback

### Premium (level 3) — 1000 EUR/year

- Can access content where `required_level <= 3`
- Everything in Main plus: all mini-courses, mini-course topic voting, resume/LinkedIn/GitHub teardowns

## Requirements

- R-TIER-1: Store tiers in a `tiers` database table with the fields above. Seed the four tiers on first deploy.
- R-TIER-2: Every user has a `tier_slug` foreign key. Default is `"free"` on registration.
- R-TIER-3: Access check is always `user.tier.level >= content.required_level`. No exceptions, no per-user overrides in MVP.
- R-TIER-4: All paid tiers bill annually. Monthly billing is out of scope for MVP.
- R-TIER-5: The pricing page at `/pricing` renders all four tiers in a comparison grid. The Main tier shows a "Most Popular" badge. Free tier shows "Subscribe" button. Paid tiers show "Join" button linking to checkout.
