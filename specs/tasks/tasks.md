# Task Index

## Dependency Graph

```
001-scaffold (done)
 ├── 068-membership-tiers
 │    ├── 067-user-auth
 │    │    ├── 069-stripe-payments
 │    │    │    ├── 070-account-page
 │    │    │    └── 082-community-slack ──► 089-notifications
 │    │    └── 085-email-ses
 │    │         ├── 086-newsletter
 │    │         └── 087-email-campaigns
 │    └── 071-access-control
 │         ├── 072-articles ─────────┐
 │         ├── 075-projects ─────────┤
 │         ├── 076-curated-links ────┤
 │         ├── 077-downloads ────────┤
 │         ├── 083-events ───────────┤
 │         │    └── 084-zoom ────────┤
 │         ├── 088-voting ───────────┤
 │         ├── 074-recordings ───┐   ├──► 090-seo-meta
 │         │   (also needs 073)  │   ├──► 091-seo-tags
 │         └── 078-courses ─┐   │   └──► 092-github-sync
 │              ├── 079-unit-pages (also needs 073)
 │              │    └── 081-cohorts
 │              └── 080-course-admin
 ├── 093-background-jobs
 │    (used by 082, 087, 089, 092)
 └── 073-video-player (deferred)
      (used by 074, 079)
```

## Parallel Execution Groups

After each group is complete, the next group can start. Tasks within a group can run in parallel.

| Group | Tasks | What's needed first |
|-------|-------|---------------------|
| **A** | 068-membership-tiers, 093-background-jobs | 001 (done) |
| **B** | 067-user-auth, 071-access-control | 068 |
| **C** | 069-stripe-payments, 085-email-ses, 086-newsletter, 087-email-campaigns | 067 |
| **D** | 070-account-page, 082-community-slack, 072-articles, 075-projects, 076-curated-links, 077-downloads, 078-course-models-catalog, 080-course-admin, 083-events, 088-voting | 069, 071 |
| **E** | 074-recordings, 079-course-unit-pages, 081-course-cohorts, 084-zoom-integration, 089-notifications, 090-seo-meta, 091-seo-tags, 092-github-content-sync | group D + 073 (when undeferred) |

## All Tasks

| ID | Issue | Task | Tags | Depends on | Status |
|----|-------|------|------|------------|--------|
| 001 | [#1](https://github.com/AI-Shipping-Labs/website/issues/1) | [Scaffold Django project](001-scaffold.md) | `infra` `frontend` | — | done |
| 067 | [#67](https://github.com/AI-Shipping-Labs/website/issues/67) | [User auth (OAuth login)](067-user-auth.md) | `auth` `frontend` | 068 | pending |
| 068 | [#68](https://github.com/AI-Shipping-Labs/website/issues/68) | [Membership tiers (model, pricing page)](068-membership-tiers.md) | `payments` `admin` `frontend` | 001 | pending |
| 069 | [#69](https://github.com/AI-Shipping-Labs/website/issues/69) | [Stripe payments (checkout, webhooks, lifecycle)](069-stripe-payments.md) | `payments` `integration` | 067 | pending |
| 070 | [#70](https://github.com/AI-Shipping-Labs/website/issues/70) | [Account page (tier info, upgrade/downgrade)](070-account-page.md) | `payments` `auth` `frontend` | 069 | pending |
| 071 | [#71](https://github.com/AI-Shipping-Labs/website/issues/71) | [Access control (gating, teasers, CTAs)](071-access-control.md) | `auth` | 068 | pending |
|  |  |  |  |  |  |
| 072 | [#72](https://github.com/AI-Shipping-Labs/website/issues/72) | [Articles / blog](072-articles.md) | `content` `admin` `frontend` | 071 | pending |
| 073 | [#73](https://github.com/AI-Shipping-Labs/website/issues/73) | [Video player component](073-video-player.md) | `frontend` | 001 | deferred |
| 074 | [#74](https://github.com/AI-Shipping-Labs/website/issues/74) | [Event recordings](074-recordings.md) | `content` `admin` `frontend` | 071, 073 | pending |
| 075 | [#75](https://github.com/AI-Shipping-Labs/website/issues/75) | [Project showcase](075-projects.md) | `content` `admin` `frontend` | 071 | pending |
| 076 | [#76](https://github.com/AI-Shipping-Labs/website/issues/76) | [Curated links](076-curated-links.md) | `content` `admin` `frontend` | 071 | pending |
| 077 | [#77](https://github.com/AI-Shipping-Labs/website/issues/77) | [Downloadable resources](077-downloads.md) | `content` `admin` `frontend` | 071 | pending |
| 078 | [#78](https://github.com/AI-Shipping-Labs/website/issues/78) | [Course models and catalog](078-course-models-catalog.md) | `courses` `frontend` | 071 | pending |
| 079 | [#79](https://github.com/AI-Shipping-Labs/website/issues/79) | [Course unit pages and progress](079-course-unit-pages.md) | `courses` `frontend` | 078, 073 | pending |
| 080 | [#80](https://github.com/AI-Shipping-Labs/website/issues/80) | [Course admin CRUD](080-course-admin.md) | `courses` `admin` | 078 | pending |
| 081 | [#81](https://github.com/AI-Shipping-Labs/website/issues/81) | [Course cohorts](081-course-cohorts.md) | `courses` `admin` | 079 | pending |
| 082 | [#82](https://github.com/AI-Shipping-Labs/website/issues/82) | [Community / Slack integration](082-community-slack.md) | `community` `integration` | 069, 093 | pending |
| 083 | [#83](https://github.com/AI-Shipping-Labs/website/issues/83) | [Events and calendar](083-events.md) | `events` `admin` `frontend` | 071 | pending |
| 084 | [#84](https://github.com/AI-Shipping-Labs/website/issues/84) | [Zoom integration](084-zoom-integration.md) | `events` `integration` | 083 | pending |
| 085 | [#85](https://github.com/AI-Shipping-Labs/website/issues/85) | [Email service (SES)](085-email-ses.md) | `email` `integration` | 067 | pending |
| 086 | [#86](https://github.com/AI-Shipping-Labs/website/issues/86) | [Newsletter signup + lead magnets](086-newsletter.md) | `email` `frontend` | 085 | pending |
| 087 | [#87](https://github.com/AI-Shipping-Labs/website/issues/87) | [Email campaigns](087-email-campaigns.md) | `email` `admin` | 085, 093 | pending |
| 088 | [#88](https://github.com/AI-Shipping-Labs/website/issues/88) | [Voting / polls](088-voting.md) | `community` `frontend` | 071 | pending |
| 089 | [#89](https://github.com/AI-Shipping-Labs/website/issues/89) | [Notifications (bell, Slack announcements)](089-notifications.md) | `community` `integration` `frontend` | 082, 093 + content | pending |
| 090 | [#90](https://github.com/AI-Shipping-Labs/website/issues/90) | [SEO: structured data, meta tags, sitemap](090-seo-meta.md) | `seo` `frontend` | content tasks | pending |
| 091 | [#91](https://github.com/AI-Shipping-Labs/website/issues/91) | [SEO: tags, filtering, conditional components](091-seo-tags.md) | `seo` `frontend` `admin` | content tasks | pending |
| 092 | [#92](https://github.com/AI-Shipping-Labs/website/issues/92) | [GitHub content sync](092-github-content-sync.md) | `content` `integration` `admin` `infra` | 093 + content | pending |
| 093 | [#93](https://github.com/AI-Shipping-Labs/website/issues/93) | [Background job infrastructure](093-background-jobs.md) | `infra` | 001 | pending |
| 094 | [#94](https://github.com/AI-Shipping-Labs/website/issues/94) | [Email + password auth](094-email-password-auth.md) | `auth` `email` `frontend` | 067, 085 | pending |

Tag definitions: [tags.md](tags.md)
