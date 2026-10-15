# AI Shipping Labs Platform Specs

Product decision records for the paid community platform at aishippinglabs.com.

## How to read these specs

- Each spec records the original requirements and product decisions for its area, written before or while the platform was built.
- They are not live implementation docs. Current behavior is owned by the code, its tests, and [`_docs/product.md`](../_docs/product.md); the [documentation map](../_docs/README.md) names the owner for each topic.
- When a spec and the code disagree, treat the code as current behavior and the spec as the original intent. Unshipped requirements remain open product intent until an issue implements or rejects them.
- Issues may cite requirement IDs (for example `R-ART-1`); keep those IDs stable.

## Specs

| Spec | Description |
|---|---|
| [01-membership-tiers](01-membership-tiers.md) | Tier definitions, pricing, what each tier includes |
| [02-payments](02-payments.md) | Stripe integration, billing, VAT, merchant of record |
| [03-access-control](03-access-control.md) | Tier-based gating, per-item permissions |
| [04-content-articles](04-content-articles.md) | Blog posts and articles |
| [05-content-courses](05-content-courses.md) | Course catalog, modules, cohorts, progress tracking |
| [06-content-resources](06-content-resources.md) | Project showcase, curated links, downloads, legacy past-event-recording discovery |
| [07-events](07-events.md) | Scheduled live community events, calendar, registration, Zoom integration |
| [08-video](08-video.md) | Video embedding, timestamps |
| [09-community](09-community.md) | Slack integration, member lifecycle |
| [10-email](10-email.md) | Newsletter, tier-granular sending, lead magnets |
| [11-voting](11-voting.md) | Topic and course voting |
| [12-notifications](12-notifications.md) | Slack, Telegram, on-platform notifications |
| [13-seo](13-seo.md) | Tags, structured data, content organization |
| [14-github-content](14-github-content.md) | Content stored in GitHub repos, synced to platform via webhooks |

## Background records

| Record | Description |
|---|---|
| [paid-community-platform](paid-community-platform.md) | 2026-02-18 requirements draft and platform evaluation (Ghost, Maven, custom Django) that led to the custom build |
| [issues](issues.md) | Original milestone backlog scaffold; GitHub issues now own all work tracking |
| [course-home/mockups](course-home/mockups/README.md) | Illustrative concept mockups for issue #1782 |

## Context

- Community tagline: "Turn AI ideas into real projects"
- Philosophy: "Learn by building, together"
- Starting point (2026-02): static site + Stripe payment links + manual Slack onboarding
- Decision: custom-built platform (no single existing platform covered all requirements); see `paid-community-platform.md`

The technology stack is owned by [`AGENTS.md`](../AGENTS.md).
