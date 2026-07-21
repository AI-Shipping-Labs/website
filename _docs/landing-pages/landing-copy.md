# Landing-page copy proposal: six nav entry points

| Field | Value |
|-------|-------|
| Audit date | 2026-07-21 |
| Pages covered | `/blog`, `/resources` (Curated Links), `/downloads`, `/courses`, `/sprints`, `/activities` |
| Status | Proposed copy only. Nothing on this page is implemented. |
| Source of truth | `_docs/product.md` (personas, tiers, taxonomy, glossary) |

These six pages are top-nav entry points. Visitors reach them from search or a
shared link without knowing what AI Shipping Labs is. Today each page opens
with a thin heading and a list. The proposed copy makes every page answer four
questions in order: what is this, who is it for, why is it worth my time, and
what do I do next.

Repo boundaries: page headers, subheads, empty states, CTA labels, and the
activities/category definitions are hardcoded in this repo (templates,
`content/views/pages.py`, `content/tier_config.py`,
`content/models/curated_link.py`). Individual article, course, download, and
curated-link titles and descriptions sync from `AI-Shipping-Labs/content` and
cannot be changed here; they are flagged as content-repo items where relevant.
Sprint names come from Studio.

## 1. `/blog`

### 1.1 Copy inventory

| String | Text | Location | Source |
|--------|------|----------|--------|
| `<title>` | `Blog \| AI Shipping Labs` | `templates/content/blog_list.html:8` | hardcoded |
| Meta description | `Articles on AI engineering, MLOps, production systems, and building with data.` | `templates/content/blog_list.html:9` | hardcoded |
| Eyebrow | `Blog` | `templates/content/blog_list.html:17` | hardcoded |
| Headline (h1) | `Insights & Updates` | `templates/content/blog_list.html:19` | hardcoded |
| Subhead | `Articles on AI engineering, production ML, and building real systems.` | `templates/content/blog_list.html:22` | hardcoded |
| Empty state (filtered) | `No articles found` / `No articles found with the selected tags.` / CTA `View all articles` | `templates/content/blog_list.html:98` | hardcoded |
| Empty state (fresh) | `No articles yet` / `Browse all articles as the archive grows.` | `templates/content/blog_list.html:100` | hardcoded |
| Article titles, descriptions, authors | per article | listing cards | content-synced |

There is no page-level CTA anywhere on the listing.

### 1.2 Diagnosis

`Insights & Updates` is generic corporate filler that could head any company
blog; it tells a first-time visitor nothing about who writes here or why the
articles are credible. The page never says what AI Shipping Labs is, and it
offers no next step: a visitor who likes an article has no path to the
newsletter, a free account, or membership without hunting through the footer.
As the most common organic search entry point, this page wastes its traffic.

### 1.3 Proposed rewrite

Headline options:

- Option A: `What we learn by shipping AI projects`
- Option B: `Practical articles on AI engineering`

Recommendation: Option A. It states the product's build-and-ship identity;
Option B is safe but interchangeable with any tech blog.

Eyebrow: keep `Blog`.

Subhead:

> The AI Shipping Labs blog: articles on AI engineering, LLM apps, and
> production ML, written by the people behind the community. Most articles are
> free to read; some are for members.

Supporting copy (short band after the article list, before the footer):

> AI Shipping Labs is a membership community where builders turn AI ideas into
> shipped projects. If these articles are useful, get the next ones by email
> or create a free account to see what membership includes.

### 1.4 CTAs

| CTA | Label | Destination |
|-----|-------|-------------|
| Primary | `Get new articles by email` | `/subscribe` |
| Secondary | `Create a free account` | `/accounts/register/` |

Place both in the supporting band; optionally repeat the primary under the
subhead.

### 1.5 Empty and gated states

Filtered empty state:

> Heading: `Nothing matches these tags`
> Body: `No articles match all the selected tags. Clear a tag or browse the full list.`
> CTA: `View all articles` -> `/blog`

Fresh empty state (no articles at all):

> Heading: `The first articles are on the way`
> Body: `We publish articles on AI engineering, LLM apps, and production ML. Subscribe and we'll send them to you as they go live.`
> CTA: `Subscribe to the newsletter` -> `/subscribe`

Gated articles already show a lock icon and tier badge on the card; no change.

### 1.6 Metadata

| Field | Proposed | Length |
|-------|----------|--------|
| `<title>` | `AI Engineering Blog \| AI Shipping Labs` | 39 |
| Meta description | `Practical articles on AI engineering, LLM apps, and production ML from the community where builders turn AI ideas into shipped projects.` | 136 |

## 2. `/resources` (Curated Links)

### 2.1 Copy inventory

| String | Text | Location | Source |
|--------|------|----------|--------|
| `<title>` | `Curated Links \| AI Shipping Labs` | `templates/content/collection_list.html:7` | hardcoded |
| Meta description | `Curated links to workshops, courses, articles, tools, and references for AI builders.` | `templates/content/collection_list.html:8` | hardcoded |
| Eyebrow | `Curated Links` | `templates/content/collection_list.html:18` | hardcoded |
| Headline (h1) | `Curated links for AI builders` | `templates/content/collection_list.html:21` | hardcoded |
| Subhead | `External links and references selected for builders shipping AI projects. Browse workshops, courses, articles, and focused references without treating this page as the home for every community activity or recording.` | `templates/content/collection_list.html:24` | hardcoded |
| Category labels | `Workshops`, `Courses`, `Articles`, `Tools`, `Models`, `Other` | `content/models/curated_link.py:18-25` | hardcoded |
| Category descriptions | e.g. `Courses and learning tracks`, `Datasets, APIs, and more` | `content/models/curated_link.py:27-34` | hardcoded |
| Gated CTA message | `Upgrade to {Tier} to access this resource` | `content/views/pages.py:494` | hardcoded |
| Gated CTA button | `View Plans` | `templates/content/collection_list.html:109` | hardcoded |
| Empty state (filtered) | `No links found` / `No curated links found with the selected tags.` / `View all links` | `templates/content/collection_list.html:122` | hardcoded |
| Empty state (fresh) | `No curated links yet` / `Check back soon for workshops, courses, and references.` | `templates/content/collection_list.html:124` | hardcoded |
| Link titles, descriptions, sources | per link | listing cards | content-synced |

### 2.2 Diagnosis

The second half of the subhead ("without treating this page as the home for
every community activity or recording") is an internal information-architecture
note leaking to visitors; no first-time reader knows what dispute it settles.
The page never says who curates the links or why the selection is worth
trusting over a search. `View Plans` breaks the glossary (tiers, not plans)
and collides with member sprint plans.

### 2.3 Proposed rewrite

Headline options:

- Option A: `Curated links for AI builders` (keep current)
- Option B: `What we recommend reading, watching, and using`

Recommendation: Option A. The current headline is already the strongest string
on the page; the problem is the subhead, not the h1.

Eyebrow: keep `Curated Links`.

Subhead:

> External courses, workshops, articles, and tools, hand-picked by the AI
> Shipping Labs team for people building AI projects. Every card links out to
> the source. Most links are open; some are reserved for members.

Supporting copy (band after the categories):

> These are links to other people's work that we recommend. Our own material
> lives on the blog and in downloads.

### 2.4 CTAs

| CTA | Label | Destination |
|-----|-------|-------------|
| Primary | `Get new links in the newsletter` | `/subscribe` |
| Secondary | `View membership tiers` | `/pricing` |
| Gated card button | `View membership tiers` (replaces `View Plans`) | `/pricing` |
| Gated card message | `Upgrade to {Tier} to open this link` (replaces `...to access this resource`) | `content/views/pages.py:494` |

### 2.5 Empty and gated states

Filtered empty state:

> Heading: `Nothing matches these tags`
> Body: `No links match all the selected tags. Clear a tag or browse the full list.`
> CTA: `View all links` -> `/resources`

Fresh empty state:

> Heading: `The first links are being curated`
> Body: `We collect the courses, workshops, articles, and tools we'd recommend to a friend building with AI. Subscribe to get them as we add them.`
> CTA: `Subscribe to the newsletter` -> `/subscribe`

Gated links (anonymous or under-tier): keep the tap-to-reveal card, with the
message and button labels above.

### 2.6 Metadata

| Field | Proposed | Length |
|-------|----------|--------|
| `<title>` | `Curated AI Links \| AI Shipping Labs` | 35 |
| Meta description | `Hand-picked external courses, workshops, articles, and tools for AI builders, curated by the AI Shipping Labs team.` | 114 |

## 3. `/downloads`

### 3.1 Copy inventory

| String | Text | Location | Source |
|--------|------|----------|--------|
| `<title>` | `Downloads \| AI Shipping Labs` | `templates/content/downloads_list.html:8` | hardcoded |
| Meta description | `Downloadable resources for building AI agents and practical systems.` | `templates/content/downloads_list.html:9` | hardcoded |
| Eyebrow | `Downloads` | `templates/content/downloads_list.html:17` | hardcoded |
| Headline (h1) | `Downloadable resources` | `templates/content/downloads_list.html:18` | hardcoded |
| Subhead | `PDFs, slides, notebooks, and practical resources to help you ship.` | `templates/content/downloads_list.html:19` | hardcoded |
| Card CTA | `View download` | `templates/content/downloads_list.html:45` | hardcoded |
| Empty state (filtered) | `No downloads found` / `No downloads match the selected topics.` / `View all downloads` | `templates/content/downloads_list.html:60` | hardcoded |
| Empty state (fresh) | `No downloads yet` / `No downloadable resources yet. Check back soon.` | `templates/content/downloads_list.html:62` | hardcoded |
| Detail-page gate | `This download is for members` / `{Tier} access is required...` / `View pricing` / `Already a member? Sign in` | `content/views/pages.py:660-672` | hardcoded |
| Download titles, descriptions, file metadata | per download | listing cards | content-synced |

### 3.2 Diagnosis

`Downloadable resources` restates the nav label and the word `resources`
collides with the Curated Links route one menu item away. The page hides its
best conversion fact: free downloads are delivered by email with no account,
which makes every level-0 download a lead magnet — the copy never says so.
`View download` undersells the action; visitors don't want to view a download,
they want the file.

### 3.3 Proposed rewrite

Headline options:

- Option A: `Guides, slides, and notebooks to keep`
- Option B: `Downloads that help you ship`

Recommendation: Option A. It names the concrete things on the page; Option B
leans on the tagline without saying what's here.

Eyebrow: keep `Downloads`.

Subhead:

> PDFs, slides, and notebooks from AI Shipping Labs, made for building AI
> systems. Free downloads arrive by email — no account needed. Member
> downloads unlock with your membership tier.

### 3.4 CTAs

| CTA | Label | Destination |
|-----|-------|-------------|
| Card CTA | `Get this download` (replaces `View download`) | `/downloads/<slug>?surface=catalog` |
| Primary (page) | none needed; the cards are the CTA | — |
| Secondary (detail-page gate) | `View membership tiers` (replaces `View pricing`) | `/pricing` |

### 3.5 Empty and gated states

Filtered empty state:

> Heading: `Nothing matches these topics`
> Body: `No downloads match all the selected topics. Clear a topic or browse the full list.`
> CTA: `View all downloads` -> `/downloads`

Fresh empty state:

> Heading: `Downloads are being prepared`
> Body: `Guides, slides, and notebooks will appear here. Until then, the blog and workshops are open — and subscribers hear first when new downloads are published.`
> Primary CTA: `Subscribe to the newsletter` -> `/subscribe`
> Secondary CTA: `Read the blog` -> `/blog`

Gated state (detail page, under-tier member or anonymous on a paid download):

> Heading: `This download is for members` (keep)
> Body: `{Tier} membership unlocks this download, along with every other download at that tier.`
> Primary CTA: `View membership tiers` -> `/pricing`
> Secondary CTA: `Already a member? Sign in` -> `/accounts/login/?next=...` (keep)

### 3.6 Metadata

| Field | Proposed | Length |
|-------|----------|--------|
| `<title>` | `Downloads: Guides, Slides, Notebooks \| AI Shipping Labs` | 55 |
| Meta description | `PDFs, slides, and notebooks for building AI systems. Free downloads arrive by email; member downloads unlock with your tier.` | 123 |

## 4. `/courses`

### 4.1 Copy inventory

| String | Text | Location | Source |
|--------|------|----------|--------|
| `<title>` | `Courses \| AI Shipping Labs` | `templates/content/courses_list.html:7` | hardcoded |
| Meta description | `Structured courses on AI engineering, machine learning, and data engineering. Learn from hands-on projects and guided lessons.` | `templates/content/courses_list.html:8` | hardcoded |
| Eyebrow | `Courses` | `templates/content/courses_list.html:16` | hardcoded |
| Headline (h1) | `Structured Learning Paths` | `templates/content/courses_list.html:18` | hardcoded |
| Subhead | `Dive deep into AI engineering with guided courses. Each course includes video lessons, hands-on exercises, and homework assignments.` | `templates/content/courses_list.html:21` | hardcoded |
| Empty state (filtered) | `No courses found` / `No courses found with the selected tags.` / `View all courses` | `templates/content/courses_list.html:72` | hardcoded |
| Empty state (fresh) | `No courses available yet` / `Check back soon for structured learning paths.` | `templates/content/courses_list.html:74` | hardcoded |
| Course titles, descriptions, instructors | per course | listing cards | content-synced |

### 4.2 Diagnosis

The headline calls courses `Learning Paths`, which is a different content type
with its own nav entry — a visitor who opens both pages sees two surfaces
claiming the same name. The subhead describes course mechanics but not who
they're for or what it costs to start, so a first-time visitor can't tell
whether anything here is available to them without clicking into a course.

### 4.3 Proposed rewrite

Headline options:

- Option A: `Learn AI engineering by building`
- Option B: `Courses built around real projects`

Recommendation: Option A. It states the teaching philosophy from
`_docs/product.md` directly; Option B depends on synced course descriptions
staying project-based.

Eyebrow: keep `Courses`.

Subhead:

> Structured courses with video lessons, hands-on exercises, and homework,
> organized into modules you can track as you complete them. Some courses are
> free with an account; mini-courses are part of Premium membership.

### 4.4 CTAs

| CTA | Label | Destination |
|-----|-------|-------------|
| Primary | `Create a free account` | `/accounts/register/` |
| Secondary | `View membership tiers` | `/pricing` |

Place under the subhead. The primary works because free courses and progress
tracking require an account; the secondary covers Premium mini-courses.

### 4.5 Empty and gated states

Filtered empty state:

> Heading: `Nothing matches these tags`
> Body: `No courses match all the selected tags. Clear a tag or browse the full catalog.`
> CTA: `View all courses` -> `/courses`

Fresh empty state:

> Heading: `Courses are being prepared`
> Body: `Structured courses with video lessons and homework are on the way. Subscribe to hear when the first one opens, or start with the blog and workshops now.`
> Primary CTA: `Subscribe to the newsletter` -> `/subscribe`
> Secondary CTA: `Browse workshops` -> `/workshops`

Gated courses already show tier badges on cards; the detail page owns the
upgrade flow. No listing change.

### 4.6 Metadata

| Field | Proposed | Length |
|-------|----------|--------|
| `<title>` | `AI Engineering Courses \| AI Shipping Labs` | 41 |
| Meta description | `Structured AI engineering courses with video lessons, hands-on exercises, and homework. Free courses plus Premium mini-courses.` | 126 |

## 5. `/sprints` (Community Sprints)

### 5.1 Copy inventory

| String | Text | Location | Source |
|--------|------|----------|--------|
| `<title>` | `Community Sprints \| AI Shipping Labs` | `templates/content/sprints_index.html:5` | hardcoded |
| Meta description | `Discover active AI Shipping Labs community sprints, cohort windows, membership requirements, and next steps for joining.` | `templates/content/sprints_index.html:6` | hardcoded |
| Eyebrow | `Community` | `templates/content/sprints_index.html:17` | hardcoded |
| Headline (h1) | `Community Sprints` | `templates/content/sprints_index.html:20` | hardcoded |
| Subhead | `Join time-bound cohorts for shipping projects with structure, accountability, and a clear window for making visible progress with the community.` | `templates/content/sprints_index.html:23` | hardcoded |
| Section titles | `Current/Future/Past sprint(s)` | `content/views/pages.py:214-216, 250-253` | hardcoded |
| Per-card boilerplate | `A sprint is a time-bound shipping cohort with project structure, accountability check-ins, and community progress.` | `templates/content/sprints_index.html:61` | hardcoded |
| Per-card tier line | `Joining requires {Tier} membership.` | `templates/content/sprints_index.html:64` | hardcoded |
| Card CTA labels | `Log in to join` / `View sprint` / `Open my plan` / `Open cohort board` / `Upgrade to {Tier}` | `content/views/pages.py:150-179` | hardcoded |
| Section empty messages | `No sprint is running right now.` / `No future sprints are scheduled yet.` / `No past sprints yet.` | `content/views/pages.py:251-253` | hardcoded |
| Whole-page empty state | `Next sprint coming soon` / `There are no active community sprints open right now. You can still join live events and workshops while the next cohort window is prepared.` / buttons `Events`, `Workshops` | `templates/content/sprints_index.html:88-100` | hardcoded |
| Sprint names and dates | per sprint | cards | Studio data |

### 5.2 Diagnosis

The subhead is one overloaded sentence that leads with the jargon `time-bound
cohorts` before the visitor knows what a sprint produces, and the same
definition sentence then repeats verbatim on every card. Nothing on the page
explains the sequence — pick a project, work from a plan, check in, ship — so
the format that differentiates the whole product stays abstract. The empty
state routes people away to events and workshops with no way to hear about the
next sprint, which is a dead end on the page's most common state between
cohort windows.

### 5.3 Proposed rewrite

Headline options:

- Option A: `Ship one project per sprint`
- Option B: `Community Sprints` (keep as h1, move the promise to the subhead)

Recommendation: Option A. The nav link already says Community Sprints, so the
h1 can spend its space on the promise; `one project per sprint` is the format
as defined in the activities copy.

Eyebrow: `Community Sprints` (replaces `Community`, matching the nav label).

Subhead:

> A community sprint is a time-boxed window where you pick one AI project,
> work from a personal plan, check in on progress with other members, and
> ship something visible by the end. Browse the current, upcoming, and past
> sprints below.

How-it-works strip (three short blocks between the subhead and the sections):

> 1. `Pick one project` — You choose what to build. A personal plan breaks it
>    into steps for the sprint window.
> 2. `Check in as you go` — Accountability check-ins with other members keep
>    the project moving through the window.
> 3. `Ship by the end` — The sprint closes with something you can show: a
>    working project, not a plan.

Membership line (once, under the strip, replacing the per-card boilerplate):

> Each sprint lists the membership it requires. Some sprints are open with a
> free account; most require Main membership. TODO(product): confirm whether
> free-tier sprints are a standing offer or a one-off.

Per-card copy: drop the repeated definition sentence
(`sprints_index.html:61`); keep the status badge, tier badge, window dates,
and the tier line `Joining requires {Tier} membership.`

### 5.4 CTAs

| Viewer | CTA | Label | Destination |
|--------|-----|-------|-------------|
| Anonymous, card primary | primary | `View sprint details` | `/sprints/<slug>` |
| Anonymous, card secondary | secondary | `Sign in to join` (replaces `Log in to join`) | `/accounts/login/?next=<detail>` |
| Free member below tier | primary | `Upgrade to {Tier}` (keep) | `/pricing` |
| Eligible member | primary | `View sprint` (keep) | `/sprints/<slug>` |
| Enrolled member | primary | `Open my plan` / `Open cohort board` (keep) | plan / board |
| Page-level secondary | secondary | `View membership tiers` | `/pricing` |

Sending anonymous visitors to the detail page first (instead of straight to
login) lets them read what the sprint is before we ask for an account.

### 5.5 Empty and gated states

This page is frequently empty between sprint windows, so the empty state is
the landing page for most first-time visitors.

Whole-page empty state (no visible sprints at all):

> Heading: `Between sprints right now`
> Body: `The next sprint window hasn't opened yet. A community sprint is a
> time-boxed push where members pick one AI project, work from a personal
> plan, check in on progress, and ship something visible by the end. Leave
> your email and we'll tell you when the next one opens — or see what
> membership includes in the meantime.`
> Primary CTA: `Get notified about the next sprint` -> `/subscribe`
> Secondary CTA: `View membership tiers` -> `/pricing`
> Tertiary link: `Browse live events` -> `/events`

Section empty messages (some sections filled, others not):

> Current: `No sprint is running today. Subscribe to the newsletter to hear when the next one opens.`
> Future: `The next sprint hasn't been scheduled yet. Subscribers hear first.`
> Past: `No past sprints yet — the first one is still ahead.`

Gated state (logged-in member below the required tier): the card already
switches its CTA to `Upgrade to {Tier}`; keep, and keep the tier line so the
requirement is visible before the click.

### 5.6 Metadata

| Field | Proposed | Length |
|-------|----------|--------|
| `<title>` | `Community Sprints \| AI Shipping Labs` (keep) | 36 |
| Meta description | `Time-boxed community sprints where members plan, build, and ship one AI project with accountability check-ins. See current and upcoming sprints.` | 143 |

## 6. `/activities`

### 6.1 Copy inventory

| String | Text | Location | Source |
|--------|------|----------|--------|
| `<title>` | `Activities and community sprints \| AI Shipping Labs` | `templates/content/activities.html:7` | hardcoded |
| Meta description | `Compare membership benefits, participation modes, community sprints, live events, and workshop access by tier at AI Shipping Labs.` | `templates/content/activities.html:8` | hardcoded |
| Eyebrow | `Access by tier` | `templates/content/activities.html:20` | hardcoded |
| Headline (h1) | `Membership benefits by tier` | `templates/content/activities.html:23` | hardcoded |
| Subhead | `Compare what Basic, Main, and Premium unlock across content, community accountability, live learning, courses, and career support.` | `templates/content/activities.html:26-27` | hardcoded |
| Taxonomy sentence | `Activities are membership benefits and participation modes: use them to understand how each tier connects to sprints, live events, workshops, and pricing.` | `templates/content/activities.html:30` | hardcoded |
| Anchor nav | `Community sprints` / `Live events` / `Workshops` | `templates/content/activities.html:33-41` | hardcoded |
| Activity card titles, descriptions, action labels | seven activities, e.g. `Community sprints`, `Explore community sprints` | `content/tier_config.py:45-152` | hardcoded |
| Quick comparison | `Quick comparison`; tier sublabels `Content only` / `Structure + accountability` / `Courses + career growth` | `templates/content/activities.html:108, 117, 140, 163` | hardcoded |
| Pricing CTA | `Compare pricing` | `templates/content/activities.html:181` | hardcoded |
| Sprints section | `Active community sprints` + subhead + `Anonymous visitors can browse active sprint windows and membership requirements before joining.` | `templates/content/activities.html:198-206` | hardcoded |
| Sprints empty state | `Next sprint coming soon` / body / `Events`, `Workshops` CTAs | `templates/content/activities.html:258` | hardcoded |
| Live events section | `Upcoming community sessions` + subhead + `View all events` | `templates/content/activities.html:268-274` | hardcoded |
| Workshops section | `Recent hands-on workshops` + subhead + `View all workshops` | `templates/content/activities.html:302-308` | hardcoded |
| Event and workshop titles | per item | cards | Studio / content-synced |

### 6.2 Diagnosis

The page starts comparing Basic, Main, and Premium before ever saying what AI
Shipping Labs is, so a first-time visitor is asked to compare tiers of a
product they haven't been introduced to. The nav says `Activities` but the
visitor lands on `Access by tier` / `Membership benefits by tier` — the label
they clicked never appears, and the taxonomy sentence on line 30 explains the
site's internal vocabulary instead of the visitor's options. The
`Anonymous visitors can browse...` line addresses the reader in the third
person.

### 6.3 The `#access-by-tier` anchor as an entry point

The anchor works mechanically: `#access-by-tier` is the id of the first
section (`activities.html:15`), so the nav link lands at the top of the page
and nothing above it is skipped. The problem is naming, not position — the
visitor clicks `Activities` and lands under two headings that don't contain
the word. Fix by making the eyebrow `Activities` and keeping the section id
unchanged so the nav link keeps working.

### 6.4 Proposed rewrite

Headline options:

- Option A: `What members do at AI Shipping Labs`
- Option B: `Membership benefits by tier` (keep current)

Recommendation: Option A. It answers the first-time visitor's actual question
and matches the nav label; the tier comparison is the mechanism, not the
headline.

Eyebrow: `Activities` (replaces `Access by tier`; keep the `#access-by-tier`
section id).

Subhead:

> AI Shipping Labs is a membership community for builders turning AI ideas
> into shipped projects. Here's what members actually do — community sprints,
> live events, workshops, courses, and career support — and which tier
> unlocks each.

Drop the taxonomy sentence at `activities.html:30` entirely.

Sprints section intro (replaces `activities.html:200-206`):

> Sprints are time-boxed windows for shipping one project with structure and
> accountability. Browse the active sprint windows and what membership each
> requires — sprint pages explain the format before you join.

Drop the `Anonymous visitors can browse...` sentence and the repeated
per-card definition at `activities.html:234` (same fix as `/sprints`).

Live events and workshops sections: current copy is serviceable; only align
CTA labels (below).

### 6.5 CTAs

| CTA | Label | Destination |
|-----|-------|-------------|
| Primary (quick comparison) | `View membership tiers` (replaces `Compare pricing`) | `/pricing` |
| Secondary (page) | `Create a free account` | `/accounts/register/` |
| Activity cards | keep the seven `action_label` values in `content/tier_config.py`, except `Compare community membership` -> `View membership tiers` | per card |

### 6.6 Empty and gated states

Sprints empty state (replaces `activities.html:258`):

> Heading: `Between sprints right now`
> Body: `The next sprint window hasn't opened yet. Subscribe to hear when it
> does, or join a live event in the meantime.`
> Primary CTA: `Get notified` -> `/subscribe`
> Secondary CTA: `Browse live events` -> `/events`

Live events empty state: keep current heading; body:

> `New community sessions are announced here first. Subscribe to hear about the next one.`
> CTA: `Subscribe to the newsletter` -> `/subscribe`

Workshops empty state: keep current copy; it already routes to `/workshops`.

The page has no gated content for anonymous visitors; tier badges on activity
cards do the gating work. No change.

### 6.7 Metadata

| Field | Proposed | Length |
|-------|----------|--------|
| `<title>` | `Member Activities by Tier \| AI Shipping Labs` | 44 |
| Meta description | `What AI Shipping Labs members do — community sprints, live events, workshops, courses, and career support — compared across Basic, Main, and Premium.` | 148 |

## 7. Terminology drift across pages

The glossary in `_docs/product.md` is the source of truth. Five drifts show up
across these six pages:

| Drift | Where it appears | Canonical term |
|-------|------------------|----------------|
| `cohort` used for sprints (`time-bound cohorts`, `shipping cohort`, `cohort window`, `cohort windows` in the meta description) | `sprints_index.html:6,23,61,90`; `activities.html:201,234`; `pages.py` CTA `Open cohort board` | `sprint` for the participation mode. The glossary reserves `cohort` for course cohorts; using it for sprints makes `/courses` cohort enrollment and `/sprints` read as the same mechanism. `Open cohort board` is a member-facing feature name and can stay until renamed product-wide. |
| `plans` used for pricing (`View Plans`) | `collection_list.html:109` | `membership tiers`. Doubly wrong: the glossary says tier, not plan, and `plan` already means a member's personal sprint plan (`Open my plan`, `plans` app). |
| `Learning Paths` used for courses (`Structured Learning Paths`) | `courses_list.html:18` | `courses`. `learning_path` is a separate content type with its own nav entry; two surfaces currently claim the name. |
| `resources` used for downloads and curated-link items (`Downloadable resources`, `access this resource`, `more resource tags`) | `downloads_list.html:9,18-19,62`; `pages.py:494`; `collection_list.html:64,98` | `downloads` on `/downloads`, `links` on `/resources`. Per the taxonomy, Resources is the nav group and `/resources` is Curated Links; calling individual items resources blurs both. |
| Pricing CTA label varies (`View Plans`, `Compare pricing`, `View pricing`, `Compare community membership`, homepage `View membership tiers`) | `collection_list.html:109`; `activities.html:181`; `pages.py:666`; `tier_config.py:104` | `View membership tiers` everywhere a button points at `/pricing`. |

Related consistency note: eyebrows should match the nav label the visitor
clicked (`Community Sprints` on `/sprints`, `Activities` on `/activities`);
both currently differ.

## 8. Content-repo changes (cannot be fixed here)

- Article, course, download, and curated-link titles and descriptions sync
  from `AI-Shipping-Labs/content`. Two synced strings currently weaken
  listings: the `Assignments for Programming assignments for Data Engineering
  Zoomcamp` curated link title (duplicated phrase) and several curated-link
  descriptions that are cut off mid-sentence (`CS336`, `LLM Fine-Tuning
  roadmap`). File these in the content repo.
- Tier feature data on the homepage comes from the synced `tiers` SiteConfig;
  any tier-wording alignment there is also a content-repo change.

## 9. Open product decisions

- TODO(product): confirm whether free-tier sprints are a standing offer; the
  proposed `/sprints` membership line depends on it.
- TODO(product): confirm the typical sprint length before adding any duration
  claim; the proposed copy avoids numbers, but `four to six weeks` would
  strengthen the subhead if confirmed.
- Decide whether `Learning Paths` stays as a separate nav item; if it does,
  the `/courses` headline change is required, not optional.
- Decide the canonical pricing CTA label (`View membership tiers` proposed)
  and apply it product-wide, not only on these six pages.

## 10. Style notes

Stylint was run over this document, and the proposed on-page strings were
adjusted where it caught real weaknesses (`carry`/`worth your time` metaphors
in empty states and supporting copy). Findings deliberately kept, because
stylint's ruleset targets first-person workshop prose and this is a copy
audit:

- Markdown tables and `###` headings: required by the task structure and by
  CLAUDE.md formatting rules, which prefer tables over key-value bullets.
- Label-colon lines and long blockquotes: the per-page copy specs
  (`Heading:` / `Body:` / `CTA:`) need labeled fields so implementers know
  exactly which string goes where.
- Second-person marketing voice instead of first-person `I` narration: these
  are landing pages, not technical walkthroughs.
- Em dashes and semicolons inside a few proposed on-page strings: they match
  the register of the site's existing copy, and splitting them would flatten
  the subheads.
- Uncontracted forms (`what is this`, `who is it for`) in the framing
  paragraph and meta descriptions: deliberate emphasis and search-snippet
  clarity.
