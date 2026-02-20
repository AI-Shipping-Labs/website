# AI Shipping Labs Platform Specs

Technical requirements for the paid community platform at aishippinglabs.com.

## Specs

| Spec | Description |
|---|---|
| [01-membership-tiers](01-membership-tiers.md) | Tier definitions, pricing, what each tier includes |
| [02-payments](02-payments.md) | Stripe integration, billing, VAT, merchant of record |
| [03-access-control](03-access-control.md) | Tier-based gating, per-item permissions |
| [04-content-articles](04-content-articles.md) | Blog posts and articles |
| [05-content-courses](05-content-courses.md) | Course catalog, modules, cohorts, progress tracking |
| [06-content-resources](06-content-resources.md) | Event recordings, project showcase, curated links, downloads |
| [07-events](07-events.md) | Live/async events, calendar, Zoom integration |
| [08-video](08-video.md) | Video embedding, timestamps |
| [09-community](09-community.md) | Slack integration, member lifecycle |
| [10-email](10-email.md) | Newsletter, tier-granular sending, lead magnets |
| [11-voting](11-voting.md) | Topic and course voting |
| [12-notifications](12-notifications.md) | Slack, Telegram, on-platform notifications |
| [13-seo](13-seo.md) | Tags, structured data, content organization |
| [14-github-content](14-github-content.md) | Content stored in GitHub repos, synced to platform via webhooks |

## Technology Stack

- **Backend:** Django (Python)
- **Payments:** Stripe
- **Community:** Slack
- **Email:** Amazon SES
- **Video:** YouTube / Loom embeds + self-hosted
- **Live events:** Zoom API

## Context

- Community tagline: "Turn AI ideas into real projects"
- Philosophy: "Learn by building, together"
- Current state: static site + Stripe payment links + manual Slack onboarding
- Target: custom-built platform (no single existing platform covers all requirements)
