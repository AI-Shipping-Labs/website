# AI Shipping Labs -- Product Overview

## What is AI Shipping Labs

AI Shipping Labs is a paid membership community platform for action-oriented builders interested in AI engineering and AI tools. Founded by Alexey Grigorev (ML engineer, creator of DataTalks.Club and the Zoomcamp series) and Valeriia Kuka (content strategist), the platform provides structure, focus, and accountability to help members turn AI ideas into real, shipped projects. It combines tiered access to exclusive educational content (articles, tutorials, event recordings, courses), a private Slack community, live events and workshops, topic/course voting, and personalized career guidance -- all organized around a "learn by building, together" philosophy. Pricing is in euros, with monthly and annual billing via Stripe.

## User Personas

### Anonymous Visitor
Arrives from search, social, or referral. Can browse the public homepage, read open (level 0) articles, recordings, projects, tutorials, curated links, and event listings. Sees membership tiers, testimonials, FAQ, and newsletter signup CTAs. Cannot access gated content, vote, register for events, or use the dashboard.

### Free Member (Newsletter Subscriber)
Has an account (email + password or via newsletter signup with double opt-in). Can log in and see the personalized dashboard. Can access all open (level 0) content, register for open events, view notifications, and manage email preferences from the account page. Cannot access content gated at Basic or above.

### Basic Member (Level 10)
Pays 20 EUR/month or 200 EUR/year. Unlocks all content with `required_level <= 10`: exclusive articles, tutorials with code examples, AI tool breakdowns, research notes, curated social posts, and gated curated links/downloads marked Basic. Self-directed; no community or live session access.

### Main Member (Level 20)
Pays 50 EUR/month or 500 EUR/year. Unlocks everything a Basic member has plus all content at `required_level <= 20`. Additionally gets Slack community access, group coding sessions, guided project-based learning, community hackathons, career discussions, personal brand guidance, and the ability to propose and vote on topic polls. This is the "Most Popular" highlighted tier.

### Premium Member (Level 30)
Pays 100 EUR/month or 1000 EUR/year. Unlocks everything, including all content at `required_level <= 30`. Gets all mini-courses, can propose and vote on course poll topics, and receives resume/LinkedIn/GitHub teardowns (personalized career feedback).

### Staff / Admin
Accesses the Django admin at `/admin/` and the Studio interface at `/studio/`. Can create and edit articles, recordings, events, courses (with modules and units), downloads, projects, email campaigns, and manage subscribers. Can trigger content syncs from GitHub via the admin sync dashboard at `/admin/sync/`. Can review community-submitted projects.

## Membership Tiers

| Tier | Slug | Level | Monthly Price | Annual Price | What It Unlocks |
|------|------|-------|---------------|--------------|-----------------|
| Free | `free` | 0 | 0 EUR | 0 EUR | Newsletter emails, open content (articles, recordings, projects, tutorials, curated links, events with `required_level = 0`), account dashboard |
| Basic | `basic` | 10 | 20 EUR | 200 EUR | Everything in Free + exclusive articles, tutorials with code examples, AI tool breakdowns, research notes, curated social posts, gated curated links and downloads at level 10 |
| Main | `main` | 20 | 50 EUR | 500 EUR | Everything in Basic + Slack community access, group coding sessions, guided project-based learning, community hackathons, career discussions, personal brand guidance, topic poll voting, content/events/downloads at level 20 |
| Premium | `premium` | 30 | 100 EUR | 1000 EUR | Everything in Main + all mini-courses, course poll voting, resume/LinkedIn/GitHub teardowns, all content/events/downloads at level 30 |

Access control logic: a user can access any content object where `user.tier.level >= content.required_level`. Anonymous users are treated as level 0. The mapping is defined in `content/access.py` with constants `LEVEL_OPEN = 0`, `LEVEL_BASIC = 10`, `LEVEL_MAIN = 20`, `LEVEL_PREMIUM = 30`.

## Product Taxonomy Contract

This taxonomy is the source of truth for public navigation, page copy, and future issue grooming.

| Term | Product Role | Current Routes / Surfaces |
|------|--------------|---------------------------|
| Community | Umbrella for membership and active participation. It includes membership tiers, activities by tier, community sprints, Slack/community access, and scheduled live events. | `/pricing`, `/activities#access-by-tier`, `/sprints`, `/events`, Slack access |
| Events | Scheduled live/community sessions with registration, calendar, and join flows. Events are not the umbrella home for all recordings, workshops, or resources. | `/events`, `/events/calendar`, `/events/<id>/<slug>` |
| Workshops | Durable hands-on learning artifacts. A workshop can originate from a live event, but after publication the canonical learning surface is the workshop landing/video/tutorial pages. | `/workshops`, `/workshops/<slug>`, `/workshops/<slug>/video`, `/workshops/<slug>/tutorial/<page_slug>` |
| Recordings | Recorded learning resources created from events. Workshop-linked recordings point to the workshop; legacy standalone event recordings stay discoverable through the past filter and event detail URLs until a future recording-library decision. | `/events?filter=past`, `/events/<id>/<slug>`, workshop video pages |
| Resources | Passive or self-serve content. The Resources navigation group contains learning/content destinations. The `/resources` route itself is the curated-links collection, not a catch-all hub. | Resources dropdown; `/resources` for Curated Links |
| Activities | Membership benefits and participation modes, not a content type. The activities page compares tier access and links out to the relevant participation surfaces. | `/activities#access-by-tier`, plus links to pricing, sprints, events, and workshops |

## Feature Inventory

### Homepage

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Public homepage | `/` (anonymous) | Marketing page with hero, philosophy, tier cards (with monthly/annual toggle and Stripe payment links), testimonials, latest recordings, blog posts, project ideas, curated links, newsletter signup, FAQ accordion, and section-dot navigation | Everyone | Shipped |
| Member dashboard | `/` (authenticated) | Personalized dashboard showing welcome banner with tier badge, continue-learning section (in-progress courses with progress bars), upcoming registered events, recent accessible content, active polls, quick actions, and unread notifications | Authenticated users | Shipped |

### Authentication & Account

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Login | `/accounts/login/` | Email + password login form | Everyone | Shipped |
| Registration | `/accounts/register/` | Email + password registration (also accessible via `/register` redirect) | Everyone | Shipped |
| Password reset | `/accounts/password/reset/` | Request password reset via email | Everyone | Shipped |
| Email verification | `/api/verify-email` | API endpoint; verification link sent on registration | Everyone | Shipped |
| Account page | `/account/` | Shows current tier and level, billing period end date, pending downgrade/cancellation notices; upgrade/downgrade/cancel modals (calls Stripe checkout/subscription APIs); newsletter toggle; change password form | Authenticated users | Shipped |
| Email preferences | `/account/api/email-preferences` | Toggle newsletter subscription on/off | Authenticated users | Shipped |
| Change password | `/account/api/change-password` | Update password from account page | Authenticated users | Shipped |
| Cancel subscription | `/account/api/cancel` | Schedule cancellation at end of billing period | Paid members | Shipped |

### Content -- Blog

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Blog listing | `/blog` | Paginated list of published articles with author, date, reading time, tags; tag filtering via chips; gated articles show lock icon and tier badge | Everyone (listing visible; gated content requires tier) | Shipped |
| Article detail | `/blog/<slug>` | Full article with cover image, author, date, reading time, tags, rendered markdown content, related articles, newsletter CTA after content; tag rule components; SEO (canonical, OG tags, structured data) | Open articles: everyone; gated articles: tier-dependent | Shipped |
| Content gating overlay | (included partial) | Blurred placeholder with teaser text, lock icon, "Upgrade to [Tier] to read this article" CTA linking to `/pricing` | Shown when user lacks access | Shipped |

### Content -- Past Event Recordings

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Past event recordings listing | `/events?filter=past` | Paginated legacy discovery surface for published past events with recordings; workshop-linked rows hand off to the workshop, standalone rows keep existing event detail URLs; tag filtering and gated tier cues remain visible | Everyone (listing visible) | Shipped |
| Standalone recording detail | `/events/<id>/<slug>` | Event detail page for a completed standalone event with inline recording, timestamps, description, tags, and materials; workshop-linked events show the workshop handoff instead | Open recordings: everyone; gated: tier-dependent | Shipped |

### Content -- Workshops

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Workshops listing | `/workshops` | Catalog of published hands-on workshop learning artifacts with access filters, skill/tool/tag filters, instructor/date metadata, and tier badges | Everyone (listing visible) | Shipped |
| Workshop detail | `/workshops/<slug>` | Durable workshop landing page with description, tools, tutorial pages, materials, code repository link, and recording action | Landing may be open or tier-dependent; pages/recording are separately gated | Shipped |
| Workshop recording | `/workshops/<slug>/video` | Canonical recording page for workshop-linked events, with gated playback, timestamps, transcript, and materials when available | Open recordings: everyone; gated: tier-dependent | Shipped |
| Workshop tutorial page | `/workshops/<slug>/tutorial/<page_slug>` | Step-by-step tutorial page in the workshop reader with breadcrumbs, navigation, progress controls, and paywall teaser when gated | Open pages: everyone; gated pages: tier-dependent | Shipped |

### Content -- Tutorials

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Tutorials listing | `/tutorials` | List of published tutorials with date, reading time, tags; gated items show lock icon | Everyone (listing visible) | Shipped |
| Tutorial detail | `/tutorials/<slug>` | Full tutorial content | Open: everyone; gated: tier-dependent | Shipped |

### Content -- Project Ideas

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Projects listing | `/projects` | Grid of project ideas with cover images, difficulty badges, author, tags; filter by difficulty and tags | Everyone (listing visible) | Shipped |
| Project detail | `/projects/<slug>` | Full project writeup | Open: everyone; gated: tier-dependent | Shipped |
| Submit project | `/api/projects/submit` | API endpoint for members to submit their own projects | Authenticated users | Shipped |

### Content -- Curated Links (Collection)

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Curated links listing | `/resources` | Links grouped by category (tools, models, courses) with icons, descriptions, source attribution, tags; gated links show lock icon and "View Plans" CTA on click | Everyone (listing visible; gated links hidden behind paywall) | Shipped |

### Content -- Downloads

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Downloads listing | `/downloads` | Grid of downloadable resources (PDFs, slides, notebooks) with file type badges, sizes, cover images, tags | Everyone (listing visible) | Shipped |
| Download file | `/api/downloads/<slug>/file` | Serves the file; lead magnet downloads (level 0) require authentication but not a paid tier; gated downloads require the appropriate tier | Lead magnets: authenticated users; gated: tier-dependent | Shipped |

### Content -- Courses

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Courses listing | `/courses` | Grid of courses with cover images, instructor, tags, tier badges; tag filtering | Everyone (listing visible) | Shipped |
| Course detail | `/courses/<slug>` | Syllabus with modules and units; progress bar (for authorized users); active cohort enrollment/unenrollment; CTA for unauthorized users; discussion link; SEO metadata | Open courses: everyone; gated: tier-dependent | Shipped |
| Course unit detail | `/courses/<slug>/<module_sort>/<unit_sort>` | Lesson page with sidebar navigation, video player with timestamps, lesson text (rendered HTML), homework section, mark-as-completed toggle, next-unit navigation; breadcrumbs | Authorized users (with drip-lock support for cohort schedules) | Shipped |
| Unit completion toggle | `/api/courses/<slug>/units/<id>/complete` | POST toggles a unit as completed/not-completed for the current user | Authenticated users with access | Shipped |
| Cohort enrollment | `/api/courses/<slug>/cohorts/<id>/enroll` | Enroll in a course cohort | Authenticated users with access | Shipped |
| Cohort unenrollment | `/api/courses/<slug>/cohorts/<id>/unenroll` | Unenroll from a cohort | Authenticated users with access | Shipped |
| Course API | `/api/courses`, `/api/courses/<slug>`, `/api/courses/<slug>/units/<id>` | JSON API for course data | Varies | Shipped |

### Content -- Tags

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Tags index | `/tags` | Cloud of all tags across content types with counts | Everyone | Shipped |
| Tag detail | `/tags/<tag>` | All content items matching a specific tag | Everyone | Shipped |

### Events

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Events listing | `/events` | Scheduled live/community event discovery with upcoming sessions, default past-event history, status/tier badges, registration state, and a past-event-recordings filter | Everyone (listing visible) | Shipped |
| Events calendar | `/events/calendar` | Monthly calendar and mobile agenda for scheduled live/community events with links to event details | Everyone | Shipped |
| Event detail | `/events/<id>/<slug>` | Announcement page for a scheduled session: status, dates, location, timezone, description, registration button, and join link near start time. Workshop-linked past events show a "View workshop writeup" handoff because recording, materials, learning objectives, and core tools live on the workshop. Legacy past standalone events still render inline recording resources. | Open events: everyone; gated: tier-dependent | Shipped |
| Event registration | `/api/events/<slug>/register` | POST to register for an event | Authenticated users with access | Shipped |
| Event unregistration | `/api/events/<slug>/unregister` | POST to unregister from an event | Authenticated users | Shipped |

### Payments & Pricing

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Pricing page | `/pricing` | Dedicated page with all 4 tiers in a grid (including Free), monthly/annual toggle, Stripe payment links for paid tiers | Everyone | Shipped |
| Stripe webhook | `/api/webhooks/payments` | Receives Stripe events (checkout.session.completed, invoice.paid, customer.subscription.updated/deleted) to update user tiers | System | Shipped |

### Voting

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Poll listing | `/vote` | List of active polls with type badge (Topic Poll or Course Poll), proposals-open indicator, option/vote counts, closing date | Authenticated users (filtered by tier level) | Shipped |
| Poll detail | `/vote/<uuid>` | Full poll with options, vote counts, vote buttons, votes-remaining counter; proposal form (if open); gating for insufficient tier | Main+ for topic polls; Premium for course polls | Shipped |
| Vote toggle | `/api/vote/<uuid>/vote` | POST to vote/unvote on an option | Tier-dependent (Main+ or Premium) | Shipped |
| Propose option | `/api/vote/<uuid>/propose` | POST to submit a new option for a poll | Tier-dependent, if proposals are open | Shipped |

### Notifications

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Notification bell | (header) | Bell icon with unread count badge in header; dropdown with recent notifications; polls every 60 seconds | Authenticated users | Shipped |
| Notifications page | `/notifications` | Full paginated list of all notifications (read/unread); mark individual or all as read | Authenticated users | Shipped |
| Notification APIs | `/api/notifications`, `/api/notifications/unread-count`, `/api/notifications/<id>/read`, `/api/notifications/read-all` | JSON APIs for listing, counting, and marking notifications | Authenticated users | Shipped |

### Newsletter & Email

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Subscribe page | `/subscribe` | Standalone newsletter signup page | Everyone | Shipped |
| Subscribe API | `/api/subscribe` | POST with email; double opt-in flow via verification email | Everyone | Shipped |
| Unsubscribe API | `/api/unsubscribe` | Unsubscribe from newsletters via token link | Everyone | Shipped |
| Email verification result | (template) | Confirmation page after clicking verify link | Everyone | Shipped |
| Unsubscribe result | (template) | Confirmation page after unsubscribing | Everyone | Shipped |

### Studio (Staff Content Management)

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| Studio dashboard | `/studio/` | Overview for staff with quick stats and links to manage content | Staff only | Shipped |
| Article management | `/studio/articles/`, `/studio/articles/new`, `/studio/articles/<id>/edit` | List, create, edit articles | Staff only | Shipped |
| Recording management | `/studio/recordings/`, `/studio/recordings/new`, `/studio/recordings/<id>/edit` | List, create, edit event recordings | Staff only | Shipped |
| Course management | `/studio/courses/`, `/studio/courses/new`, `/studio/courses/<id>/edit` | List, create, edit courses with modules and units; reorder modules/units | Staff only | Shipped |
| Event management | `/studio/events/`, `/studio/events/new`, `/studio/events/<id>/edit` | List, create, edit events | Staff only | Shipped |
| Download management | `/studio/downloads/`, `/studio/downloads/new`, `/studio/downloads/<id>/edit` | List, create, edit downloadable resources | Staff only | Shipped |
| Project review | `/studio/projects/`, `/studio/projects/<id>/review` | List submitted projects; approve/reject | Staff only | Shipped |
| Campaign management | `/studio/campaigns/`, `/studio/campaigns/new`, `/studio/campaigns/<id>/` | List, create, view email campaigns | Staff only | Shipped |
| Subscriber management | `/studio/users/?filter=subscribers`, `/studio/users/export?filter=subscribers` | List subscribers; export to CSV | Staff only | Shipped |

### Integrations & Webhooks

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| GitHub content sync | `/api/webhooks/github` | Webhook receives push events; syncs markdown/YAML content from configured GitHub repos into articles, recordings, projects, etc. | System (webhook secret) | Shipped |
| Admin sync dashboard | `/admin/sync/` | View configured content sources, sync history, trigger manual sync | Staff only | Shipped |
| Zoom webhook | `/api/webhooks/zoom` | Receives Zoom events (for live event integration) | System | Shipped |
| SES webhook | `/api/ses-events` | Receives Amazon SES bounce/complaint notifications | System | Shipped |

### Other Pages

| Feature | URL | Description | Access | State |
|---------|-----|-------------|--------|-------|
| About page | `/about` | Community introduction, founders (Alexey Grigorev and Valeriia Kuka) with bios and LinkedIn links, "Why AI Shipping Labs?" CTA | Everyone | Shipped |
| Activities page | `/activities#access-by-tier` | Membership benefits and participation modes organized by tier, with filter buttons (Basic/Main/Premium), quick comparison, and links to pricing, sprints, events, and workshops | Everyone | Shipped |
| Community sprints index | `/sprints` | Public discovery page for current, future, and past community sprint cohorts with tier requirements and next-step CTAs | Everyone (joining requires authentication/tier access) | Shipped |
| Sitemap | `/sitemap.xml` | XML sitemap for search engines | Everyone | Shipped |
| Django admin | `/admin/` | Full Django admin with custom admin views for all models, including email campaign change form with timestamp editor widget | Superusers/staff | Shipped |

## Navigation & Information Architecture

### Header (Global)
The fixed header appears on every page and contains:
- Logo + site name linking to `/` (home)
- Primary nav groups (desktop): About, Community, Resources
- About dropdown: About, Team, FAQ
- Community dropdown: Overview (`/community`) when the launch recap exists, Membership (`/pricing`), Activities (`/activities#access-by-tier`), Community Sprints (`/sprints`), Events (`/events`)
- Resources dropdown: Blog, Courses, Workshops, Learning Paths, Project Ideas, Interview Prep, Curated Links (`/resources`), and Downloads when published downloads exist
- Auth area (desktop): "Sign in" for anonymous users; for authenticated users: notification bell with unread badge dropdown, email link to account page, "Log out" button
- Mobile menu: Hamburger toggle with About, Community, and Resources accordions plus account/notifications/logout controls

### Footer (Global)
Appears on every page:
- Newsletter signup form (email input + subscribe button, calls `/api/subscribe`)
- Site logo + tagline: "Where action-oriented builders turn AI ideas into real projects"
- Community links: About, Membership Tiers, Activities, Community Sprints, Events, FAQ, Manage Subscription (Stripe customer portal)
- Copyright notice

### Homepage CTAs (Anonymous)
The homepage serves as the primary conversion funnel for anonymous visitors:
1. Hero: "Subscribe for updates" (scrolls to newsletter section) and "View Membership Tiers" (scrolls to tiers section)
2. Tiers section: Payment links for each tier (monthly/annual toggle)
3. Past event recordings section: "View past event recordings" links to `/events?filter=past`
4. Blog section: "View all posts" links to `/blog`
5. Projects section: "View all project ideas" links to `/projects`
6. Collection section: "View all curated links" links to `/resources`
7. Newsletter section: Email signup form
8. Footer: Secondary newsletter signup

### Dashboard CTAs (Authenticated)
The dashboard surfaces personalized actions:
1. Continue Learning: Resume in-progress courses
2. Upcoming Events: View registered events
3. Recent Content: Click through to accessible articles/recordings
4. Active Polls: Vote on polls
5. Quick Actions: Browse Courses, View Recordings (`/events?filter=past`), Community (Main+ only), Submit Project
6. Notifications: Click through to notification targets

### Content-Level Cross-Links
- Gated content: When a user cannot access content, a CTA banner appears with "Upgrade to [Tier] to [action]" linking to `/pricing`
- Article detail: Related articles section; newsletter CTA after content; tag links
- Course detail: "Sign Up Free" CTA for free courses (unauthenticated users); "View Pricing" CTA for paid courses
- Event listing: Past event recordings link to the workshop when one is linked; legacy standalone recordings keep the event detail URL
- Tag system: Tag chips on listings link to filtered views; global tag index at `/tags`

## Key User Journeys

### 1. Discovery to Free Member
Visitor lands on homepage (from search, social, or referral) -> reads hero and philosophy sections -> scrolls through testimonials -> browses open blog articles and recordings -> enters email in newsletter form (homepage, footer, or `/subscribe`) -> receives verification email -> clicks verification link -> becomes a confirmed newsletter subscriber. If they also create an account via `/accounts/register/`, they become a Free member with dashboard access.

### 2. Free Member to Paid Upgrade
Free member logs in -> sees dashboard with limited content -> browses blog and encounters a gated article (lock icon, blurred content overlay, "Upgrade to Basic to read this article") -> clicks "View Pricing" -> lands on `/pricing` -> compares tiers (monthly/annual toggle) -> selects a tier -> redirected to Stripe Checkout -> completes payment -> Stripe webhook updates their tier -> returns to site with full access to content at their new level.

### 3. Member Takes a Course
Member navigates to `/courses` -> browses course catalog with tag filters -> clicks into a course -> reads syllabus and description -> enrolls in a cohort (if available) -> starts first unit -> watches embedded video, reads lesson text, completes homework -> clicks "Mark as completed" -> proceeds to next unit via "Next" button -> progress bar updates on course detail page -> returns to dashboard and sees course in "Continue Learning" section -> eventually completes all units.

### 4. Member Registers for an Event
Member navigates to `/events` -> sees scheduled live/community events -> clicks into an event detail page -> reads description and schedule -> clicks "Register" button -> event appears in their dashboard under "Upcoming Events" -> receives notification before event -> attends via the join link near start time -> after the event, a workshop-linked session points to `/workshops/<slug>` / `/workshops/<slug>/video`, while a legacy standalone recording remains available from the event detail URL and `/events?filter=past`.

### 5. Visitor Uses Workshops and Past Event Recordings
Visitor opens `/workshops` from the Resources dropdown -> scans durable hands-on learning artifacts with writeups, recordings, tutorial pages, tools, and materials -> opens a workshop landing page -> watches the recording or reads tutorial pages if their tier allows it. If the visitor starts from `/events?filter=past`, workshop-linked recordings hand off to the workshop and standalone recordings keep the existing event detail URL.

### 6. Visitor to Paid Member via Pricing
Visitor clicks "View Membership Tiers" on homepage or navigates to `/pricing` -> reviews all 4 tiers in the grid -> toggles between monthly and annual pricing (annual saves approximately 17%) -> clicks "Join" on their chosen tier -> redirected to Stripe Checkout -> creates account during checkout (or logs in) -> completes payment -> gains access at the purchased tier level.

### 7. Staff Manages Content via Studio
Staff member logs in -> navigates to `/studio/` -> sees dashboard with content counts -> clicks into Articles section -> creates a new article (title, slug, content in markdown, tags, required_level, published flag) -> article appears on the blog listing and homepage. Alternatively, staff configures a GitHub content source at `/admin/sync/` -> content auto-syncs from a GitHub repo on push (via webhook) or manual trigger -> articles, recordings, projects are created/updated from markdown + YAML frontmatter files in the repo.

### 8. Member Votes on Topics
Main or Premium member navigates to `/vote` -> sees active polls filtered to their tier level (topic polls for Main+, course polls for Premium) -> clicks into a poll -> reads options -> votes on up to N options (toggle on/off) -> optionally proposes a new option (if proposals are open) -> poll results influence what the community builds next.

### 9. Member Manages Subscription
Member goes to `/account/` -> sees current tier, billing period end date -> wants to upgrade: clicks "Upgrade" -> modal shows higher tiers with prices -> selects one -> redirected to Stripe Checkout. Or wants to downgrade: clicks "Downgrade" -> modal shows lower tiers -> selects one -> change scheduled for end of billing period. Or wants to cancel: clicks "Cancel Subscription" -> confirmation modal -> confirms -> cancellation scheduled for end of billing period, access retained until then.

## Terminology Glossary

| Term | Meaning | Do NOT Call It |
|------|---------|----------------|
| Community | The membership and active-participation umbrella: tiers, activities, Slack access, sprints, and scheduled live events | Resources, content library |
| Tier | A membership level (Free, Basic, Main, Premium) | Plan, package, subscription level |
| Level | The numeric access level associated with a tier (0, 10, 20, 30) | Rank, grade |
| Article | A blog post on the site | Post, blog entry |
| Workshop | A durable hands-on learning artifact with writeup/tutorial pages, recording, materials, tools, and optional code repository | Past event, webinar |
| Recording | A recorded learning resource created from an event. Workshop-linked recordings live on the workshop landing/video pages (`/workshops/<slug>` and `/workshops/<slug>/video`); the linked Event page announces the session and links out to the workshop. Legacy past events that have not been promoted to a Workshop still host their recording inline on the event detail page and list at `/events?filter=past`. | Generic event, workshop |
| Tutorial | A focused step-by-step guide on a narrow topic | How-to, guide |
| Course | A structured multi-module learning path with units | Class, program |
| Module | A grouping of units within a course | Section, chapter |
| Unit | A single lesson within a module (video + text + homework) | Lesson, lecture |
| Cohort | A time-bound group taking a course together (with drip scheduling) | Batch, class, group |
| Project | A project idea or portfolio project writeup | Showcase, portfolio item |
| Resource | Passive or self-serve content surfaced through the Resources navigation group. The `/resources` route itself is Curated Links. | Activity, live event |
| Curated Link | An external link categorized by type (workshop, course, article, other) on `/resources` | Resource hub, activity, recording library |
| Download | A downloadable file (PDF, slides, notebook) | Asset, attachment |
| Activity | A membership benefit or participation mode shown on `/activities#access-by-tier` | Resource, content type |
| Event | A scheduled live/community session with registration and join flow | Resource, recording library |
| Instructor | A person who teaches courses, workshops, or speaks at events; identified by a stable `instructor_id` slug and referenced from yaml | Speaker, presenter, author |
| Poll | A vote on a topic or course idea | Survey, questionnaire |
| Option | A choice within a poll that members can vote on | Answer, item |
| Proposal | A member-submitted option for a poll | Suggestion |
| Notification | An in-app alert shown in the bell dropdown and notifications page | Alert, message |
| Newsletter | The email subscription powered by the email_app | Mailing list |
| Subscriber | Someone who has signed up for the newsletter (may or may not have an account) | Lead, contact |
| Campaign | A single email send to subscribers | Blast, email |
| Studio | The staff-facing content management interface at `/studio/` | CMS, admin panel, back-office |
| Content Source | A GitHub repo configured to sync content into the platform | Integration, feed |
| Gated | Content that requires a specific tier level to access | Locked, restricted, premium |
| Lead Magnet | A download with `required_level = 0` that requires an account but not payment | Freebie, opt-in |
| Account | The user-facing page at `/account/` for managing membership and preferences | Profile, settings |
| Billing Period | The current monthly or annual subscription cycle | Cycle, term |
