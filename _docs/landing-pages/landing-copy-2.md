# Landing Page Copy Proposal — Batch 2

| Field | Value |
|-------|-------|
| Audit date | 2026-07-21 |
| Pages covered | `/projects`, `/interview`, `/workshops`, `/workshops/catalog`, `/events` (including `/events?filter=past`) |
| Status | Proposed copy only — nothing here is implemented. Templates and views are unchanged. |
| Companion document | `_docs/landing-pages/landing-copy.md` (parallel audit of `/blog`, `/resources`, `/downloads`, `/courses`, `/sprints`, `/activities`; terminology reconciled, see section 7) |

## How to read this document

Each string in the inventories is marked `hardcoded` (lives in this repo — template or view — and can be changed here) or `content-synced` (comes from the `AI-Shipping-Labs/content` or `AI-Shipping-Labs/workshops-content` repo through the sync pipeline and must be changed there). Proposed copy is written out in full, ready to paste. Grounding: `_docs/product.md`, especially the Product Taxonomy Contract and the Terminology Glossary.

---

## 1. `/projects` — Project Ideas

### 1.1 Copy inventory

| String | Current text | Location | Source |
|--------|--------------|----------|--------|
| `<title>` | `Project Showcase \| AI Shipping Labs` | `templates/content/projects_list.html:5` | hardcoded |
| Meta description | `Project ideas and real projects from people who've taken courses. End-to-end AI applications and agentic workflows you can learn from.` | `templates/content/projects_list.html:6` | hardcoded |
| Eyebrow | `Project Showcase` | `templates/content/projects_list.html:14` | hardcoded |
| Headline (h1) | `Pet & Portfolio Project Ideas` | `templates/content/projects_list.html:16` | hardcoded |
| Subhead | `Project ideas and real projects from people who've taken courses. End-to-end AI applications and agentic workflows you can learn from and build on.` | `templates/content/projects_list.html:19` | hardcoded |
| Filter label | `Filter by difficulty` | `templates/content/projects_list.html:26` | hardcoded |
| Filter clear | `Clear filter` | `templates/content/projects_list.html:32` | hardcoded |
| Difficulty values | e.g. `Beginner`, `Intermediate` | rendered at `projects_list.html:40` | content-synced |
| Empty state (tag filter) | `No projects match these tags` / `No projects match the selected tags.` | `templates/content/projects_list.html:55` | hardcoded |
| Empty state (difficulty filter) | `No projects match this difficulty` / `No projects match the selected difficulty.` | `templates/content/projects_list.html:57` | hardcoded |
| Empty state (fresh) | `No project ideas yet` / `Check back soon for pet and portfolio project ideas.` | `templates/content/projects_list.html:59` | hardcoded |
| Card titles, descriptions, difficulty, author, tags | per project | `templates/content/_project_card.html` | content-synced |
| Nav label | `Project Ideas` | `templates/includes/header.html:60` | hardcoded |

### 1.2 Diagnosis

The page calls itself three different things — nav says `Project Ideas`, the title tag says `Project Showcase`, and the h1 says `Pet & Portfolio Project Ideas` — and `Showcase` is on the glossary's do-not-call-it list. The subhead claims the projects come "from people who've taken courses", which frames the page around courses a first-time visitor hasn't seen yet, and nothing tells the visitor what to do with a project idea or that members can submit their own. There is no conversion path: a visitor who likes the page has no next step besides clicking a card.

### 1.3 Proposed rewrite

Eyebrow: `Project Ideas`

Headline option A (recommended): `AI project ideas you can build and ship`
Headline option B: `Pick your next AI project`
Recommendation note: A says what the page contains and sets the build-and-ship expectation; B is punchier but could be any listicle.

Subhead:

> A catalog of AI project ideas with full writeups: end-to-end applications, agents, and data pipelines. Each writeup tells you what to build, which tools to use, and how hard it is — filter by difficulty, pick one that fits, and start building. Members can submit their own finished projects to the catalog.

Supporting line above the filter controls (replaces nothing, sits with `Filter by difficulty`):

> Not sure where to start? Filter for a difficulty that matches your experience.

### 1.4 CTA copy

| CTA | Label | Target | Notes |
|-----|-------|--------|-------|
| Primary | the project card grid itself | `/projects/<slug>` | The content is the product here; no button should compete with the grid. |
| Secondary (new, under subhead) | `Create a free account` | `/accounts/register/` | Supporting line: `Free members can read every open project and submit their own.` |
| Tertiary (new, under subhead) | `View membership tiers` | `/pricing` | Text link, not a button. Label matches the canonical pricing CTA agreed in `landing-copy.md`. |

### 1.5 Empty-state and gated-state copy

Fresh (no projects at all):

> Title: `Project ideas are on the way`
> Body: `We haven't published the first project writeups yet. Subscribe and we'll email you when they're ready.`
> Primary CTA: `Subscribe for updates` → `/subscribe`
> Secondary CTA: `Browse workshops` → `/workshops`

Filter empty (tags or difficulty — the current body just repeats the title):

> Title: `No projects match this filter`
> Body: `Try removing a filter, or browse the full list.`
> Primary CTA: `View all project ideas` → `/projects`

Gated state: the listing is fully visible to anonymous visitors; gated writeups show a tier badge on the card and the upgrade path lives on the detail page. No listing-level gate copy needed beyond the tier badges already rendered.

### 1.6 Title and meta description

`<title>` (33 chars): `AI Project Ideas | AI Shipping Labs`

Meta description (139 chars): `AI project ideas with full writeups: end-to-end apps, agents, and pipelines. Filter by difficulty, pick one, and build it with the community.`

---

## 2. `/interview` — Interview Prep

### 2.1 Copy inventory

| String | Current text | Location | Source |
|--------|--------------|----------|--------|
| `<title>` | `AI Engineer Interview Questions \| AI Shipping Labs` | `templates/content/interview_hub.html:3` | hardcoded |
| Meta description | `Prepare for AI engineer interviews with theory, coding, system design, behavioral, and project deep dive questions.` | `templates/content/interview_hub.html:4` | hardcoded |
| Back link | `Back to home` | `templates/content/interview_hub.html:14` | hardcoded |
| Eyebrow | `Interview Prep` | `templates/content/interview_hub.html:16` | hardcoded |
| Headline (h1) | `AI Engineer Interview Questions` | `templates/content/interview_hub.html:18` | hardcoded |
| Subhead | `Prepare for AI engineer interviews with questions based on real candidate reports and job descriptions.` | `templates/content/interview_hub.html:21` | hardcoded |
| Category titles and descriptions | e.g. `Theory Interview Questions`, `17 take-home assignments and 5 paid work trials analyzed in detail.` | `InterviewCategory` rows, rendered at `interview_hub.html:31-32, 46-47` | content-synced |
| Coming-soon badge | `Coming soon` | `templates/content/interview_hub.html:37-38` | hardcoded |
| Section count | `N section(s)` | `templates/content/interview_hub.html:53` | hardcoded |
| Empty behavior | 404 when no categories synced | `content/views/interview.py:83-84` | hardcoded |

### 2.2 Diagnosis

The headline and subhead are the closest to landing-ready of the five pages, but the page never says the most surprising and convertible fact about itself: everything is free to read, with no account and no tier gate (verified — `content/views/interview.py` and `interview_detail.html` contain no access checks). The `Back to home` link is unique among the five pages and reads as an exit sign at the top of an entry point. There is no guidance on where to start among six categories and no next step for a visitor who finishes reading.

### 2.3 Proposed rewrite

Eyebrow: `Interview Prep`

Headline option A (recommended): `AI Engineer Interview Questions`
Headline option B: `Get ready for your AI engineer interview`
Recommendation note: A matches what people search for and what the nav promises; keep it.

Subhead:

> Questions collected from real candidate reports and job descriptions, organized the way interviews actually run: theory, coding, system design, project deep dives, behavioral rounds, and home assignments. Everything is free to read — no account needed.

Supporting line above the category grid:

> New to interview prep? Start with the theory questions, then work through the round you're least ready for.

Remove the `Back to home` link (`interview_hub.html:12-15`); the header logo already does this job and no other landing page has one.

### 2.4 CTA copy

| CTA | Label | Target | Notes |
|-----|-------|--------|-------|
| Primary | `Start with theory questions` | `/interview/theory` | Button under the subhead; the category cards remain the browse path. |
| Secondary | `Build portfolio projects to talk about` | `/projects` | Text link; project deep dives are a listed category, so the cross-link is earned. |

### 2.5 Empty-state and gated-state copy

Empty: the view raises 404 when no categories are synced, so the page is invisible rather than empty. Keep that behavior — an empty interview hub reached from the nav would be worse than the nav link 404ing, and the nav should not ship without the content. No empty-state copy needed.

Gated state: none — all interview content is open. The proposed subhead makes that explicit, which is the strongest gate-related copy this page can carry.

Coming-soon cards: keep the `Coming soon` badge; add one body line to the card so it is a promise rather than a dead tile:

> `We're writing this one now.`

### 2.6 Title and meta description

`<title>` (50 chars, keep): `AI Engineer Interview Questions | AI Shipping Labs`

Meta description (145 chars): `Free AI engineer interview questions from real candidate reports: theory, coding, system design, project deep dives, behavioral, home assignments.`

---

## 3. `/workshops` — Workshops landing

### 3.1 Copy inventory

| String | Current text | Location | Source |
|--------|--------------|----------|--------|
| `<title>` | `Hands-on AI Workshops \| AI Shipping Labs` | `templates/content/workshops_list.html:3` | hardcoded |
| Meta description | `Hands-on AI workshops with recordings, step-by-step writeups, tutorial pages, code, and materials for builders shipping real projects.` | `templates/content/workshops_list.html:4` | hardcoded |
| Eyebrow | `Workshops` | `templates/content/workshops_list.html:13` | hardcoded |
| Headline (h1) | `Hands-on AI workshops` | `templates/content/workshops_list.html:15` | hardcoded |
| Subhead | `Practical AI engineering sessions for builders who want to learn by building with AI Shipping Labs. Each workshop turns a concrete project into a guided path with the recording, step-by-step writeups or tutorial pages, and runnable code or materials when they are provided.` | `templates/content/workshops_list.html:18` | hardcoded |
| Second paragraph | `Event pages announce live sessions. Workshop pages are durable hands-on learning artifacts: the home for the writeup, recording, materials, and project notes you can return to when it is time to ship.` | `templates/content/workshops_list.html:21` | hardcoded |
| Primary CTA | `Browse all workshops` | `templates/content/workshops_list.html:25` | hardcoded |
| Secondary CTA | `View membership options` | `templates/content/workshops_list.html:29` | hardcoded |
| Value card 1 | `Guided build flow` / `Follow a project from setup through implementation decisions and shipping checkpoints.` | `templates/content/workshops_list.html:38-39` | hardcoded |
| Value card 2 | `Replay and writeups` / `Use recordings alongside step-by-step pages when you want to revisit the work.` | `templates/content/workshops_list.html:45-46` | hardcoded |
| Value card 3 | `Project outcomes` / `Leave with code, materials, or project direction you can adapt to real AI systems.` | `templates/content/workshops_list.html:52-53` | hardcoded |
| Preview eyebrow | `Latest workshops` | `content/views/workshops.py:654` | hardcoded (view) |
| Preview heading | `Start with recent workshop writeups` | `content/views/workshops.py:655` | hardcoded (view) |
| Preview intro | `Preview the newest published workshops, then open the full archive when you want to filter by topic or access level.` | `content/views/workshops.py:656-659` | hardcoded (view) |
| Preview CTA | `View all workshops` | `templates/content/_workshops_catalog.html:54` | hardcoded |
| Workshop card titles, descriptions, instructors, tools, tags | per workshop | `templates/content/_workshops_catalog.html:152-266` | content-synced (`workshops-content`) |
| Empty state (fresh) | `No workshops published yet` / `Check back soon.` | `templates/content/_workshops_catalog.html:270,274` | hardcoded |

### 3.2 Diagnosis

This is the only page of the five with real landing structure, but the copy leaks internal taxonomy at the visitor: "durable hands-on learning artifacts" is a phrase from `_docs/product.md`, not something a first-time visitor should have to parse, and "when they are provided" hedges away the value proposition mid-sentence. The subhead also calls workshops "sessions", which is the exact confusion the second paragraph then has to untangle. The preview heading "Start with recent workshop writeups" undersells the cards below it, which contain recordings and code, not just writeups.

### 3.3 Proposed rewrite

Eyebrow: `Workshops`

Headline option A (recommended): `Hands-on AI workshops`
Headline option B: `Learn AI engineering by building it`
Recommendation note: A is concrete, matches the nav and the title tag, and already ranks the page's core noun; B trades that clarity for attitude.

Subhead:

> Each workshop takes one real project — an agent framework, an evaluation pipeline, a deployed app — and walks you through building it. You get the recording, step-by-step tutorial pages, and code you can run yourself.

Second paragraph:

> Workshops start as live sessions on the events calendar. After the session, everything moves here — recording, tutorial, materials — so you can build at your own pace and come back when it's time to ship your own version.

Value cards:

| Card | Heading | Body |
|------|---------|------|
| 1 | `Follow the build` | `Go from setup through the decisions that matter to a working result, in order.` |
| 2 | `Watch or read` | `Follow the recording, the tutorial pages, or both — whichever fits how you learn.` |
| 3 | `Leave with code` | `Take the repo and materials and adapt them to your own project.` |

Access line (new, one sentence under the value cards):

> Free workshops are open to everyone. Paid-tier workshops are labeled on each card.

Preview section (strings live in `content/views/workshops.py:654-659`):

> Eyebrow: `Latest workshops`
> Heading: `Start with the latest workshops`
> Intro: `The newest published workshops. Open the full catalog to filter by topic, technology, skill level, or access.`

### 3.4 CTA copy

| CTA | Label | Target | Notes |
|-----|-------|--------|-------|
| Primary | `Browse all workshops` | `/workshops/catalog` | Keep as is. |
| Secondary | `View membership tiers` | `/pricing` | Relabel from `View membership options` to match the canonical pricing CTA agreed in `landing-copy.md`. |
| Preview section | `View all workshops` | `/workshops/catalog` | Keep as is. |

### 3.5 Empty-state and gated-state copy

Fresh (shared partial, also used by the catalog — see 4.5 for the filter case):

> Title: `Workshops are coming`
> Body: `We're preparing the first workshop writeups and recordings. Subscribe and we'll email you when they're published.`
> Primary CTA: `Subscribe for updates` → `/subscribe`
> Secondary CTA: `See upcoming events` → `/events`

Gated state: listing visible to everyone; cards carry tier badges and a `Free` badge for open workshops. The new access line in 3.3 is the page-level gate copy. No further gate copy needed on the listing.

### 3.6 Title and meta description

`<title>` (40 chars, keep): `Hands-on AI Workshops | AI Shipping Labs`

Meta description (147 chars): `Hands-on AI workshops: build an agent, an eval pipeline, or a deployed app with the recording, step-by-step tutorial pages, and code to run yourself.`

---

## 4. `/workshops/catalog` — Workshop catalog

### 4.1 Copy inventory

| String | Current text | Location | Source |
|--------|--------------|----------|--------|
| `<title>` | `All Workshops \| AI Shipping Labs` | `content/views/workshops.py:684` | hardcoded (view) |
| Meta description | `Browse the full AI Shipping Labs workshop catalog and archive, including recordings, writeups, tutorial pages, materials, and access labels.` | `content/views/workshops.py:685-689` | hardcoded (view) |
| Eyebrow | `Archive` | `content/views/workshops.py:673` | hardcoded (view) |
| Headline (h2, page has no h1) | `All workshops` | `content/views/workshops.py:674` | hardcoded (view) |
| Intro | `Browse the full AI Shipping Labs workshop archive, newest first, with recordings, writeups, tutorial pages, materials, and membership access labels.` | `content/views/workshops.py:675-679` | hardcoded (view) |
| Access filter labels | `All`, `Free`, `Paid` | `content/views/workshops.py:106-110` | hardcoded (view) |
| Facet headings | `Topics`, `Technologies` | `templates/content/_workshops_catalog.html:63,90` | hardcoded |
| Clear-filters CTA | `View all workshops` | `templates/content/_workshops_catalog.html:48` | hardcoded |
| Empty state (filter) | `No workshops found` / `No workshops match the selected filters.` | `templates/content/_workshops_catalog.html:272` | hardcoded |
| Empty state (fresh) | `No workshops published yet` / `Check back soon.` | `templates/content/_workshops_catalog.html:270,274` | hardcoded |
| Workshop cards | per workshop | `templates/content/_workshops_catalog.html` | content-synced (`workshops-content`) |

### 4.2 Diagnosis

`Archive` is the wrong frame: it tells a visitor the page is where old things go, when it is actually the live catalog of everything the platform teaches. A visitor deep-linked here from search gets no sentence saying what a workshop is or that some are free — the intro describes the page's mechanics ("with recordings, writeups, tutorial pages, materials, and membership access labels") rather than the value. The meta description hedges between "catalog and archive", repeating the same identity confusion.

### 4.3 Proposed rewrite

Should `/workshops` and `/workshops/catalog` read differently? Yes, and deliberately: `/workshops` is the pitch (what workshops are, why they're worth your time, three newest as proof) and `/workshops/catalog` is the tool (find the right workshop fast). The catalog should not repeat the landing pitch; it needs exactly one orientation sentence for deep-linked visitors, then get out of the way of the filters.

Eyebrow: `Workshop catalog`

Headline option A (recommended): `All workshops`
Headline option B: `Find your next workshop`
Recommendation note: A matches the title tag and the "View all workshops" CTAs that lead here; B reads well but breaks the label chain from the buttons that promise "all workshops".

Intro:

> Every published workshop, newest first — each one a guided build with a recording, tutorial pages, and code. Filter by topic, technology, skill level, or access. Free workshops are open to everyone.

Orientation link (new, text link after the intro):

> `New here? See how workshops work` → `/workshops`

### 4.4 CTA copy

| CTA | Label | Target | Notes |
|-----|-------|--------|-------|
| Primary | the workshop card grid | `/workshops/<slug>` | Filters plus grid are the page's job. |
| Secondary | `New here? See how workshops work` | `/workshops` | Orientation for deep-linked visitors. |
| Clear filters | `View all workshops` | `/workshops/catalog` | Keep as is. |

### 4.5 Empty-state and gated-state copy

Filter empty:

> Title: `No workshops match these filters`
> Body: `Try removing a filter, or browse the full catalog.`
> Primary CTA: `View all workshops` → `/workshops/catalog`

Fresh: same copy as 3.5 (shared partial).

Gated state: `Free` / tier badges on cards plus the `Free` access filter already do this work; the intro's closing sentence (`Free workshops are open to everyone.`) is the page-level reassurance.

### 4.6 Title and meta description

`<title>` (32 chars, keep): `All Workshops | AI Shipping Labs`

Meta description (146 chars): `Every AI Shipping Labs workshop, newest first. Filter by topic, technology, skill level, and access. Recordings, tutorial pages, and code included.`

---

## 5. `/events` — Events (including `/events?filter=past`)

### 5.1 Copy inventory

| String | Current text | Location | Source |
|--------|--------------|----------|--------|
| `<title>` | `Events \| AI Shipping Labs` | `templates/events/events_list.html:5` | hardcoded |
| Meta description | `Scheduled live community sessions, registration, calendar view, and recordings from past AI Shipping Labs events.` | `templates/events/events_list.html:6` | hardcoded |
| Eyebrow | `Events` | `templates/events/events_list.html:14` | hardcoded |
| Headline (h1) | `Live community events` | `templates/events/events_list.html:16` | hardcoded |
| Subhead | `Join scheduled live sessions, coding sessions, and community moments. Register for upcoming events, add them to your calendar, or browse recordings from past events.` | `templates/events/events_list.html:18-20` | hardcoded |
| View toggle | `List` / `Calendar` | `templates/events/events_list.html:25-26` | hardcoded |
| Filter tabs | `All` / `Upcoming` / `Past event recordings` | `templates/events/events_list.html:39,47,55` | hardcoded |
| Section heading | `Upcoming` | `templates/events/events_list.html:77` | hardcoded |
| Empty upcoming | `No upcoming events scheduled. Check back soon!` | `templates/events/events_list.html:96` | hardcoded |
| Section heading (past filter) | `Past event recordings` | `templates/events/events_list.html:108` | hardcoded |
| Section heading (all view) | `Past events` | `templates/events/events_list.html:111` | hardcoded |
| Past-filter intro | `Recordings from past events stay here for legacy discovery. When a recording has a linked workshop, the workshop is the canonical learning artifact.` | `templates/events/events_list.html:117-119` | hardcoded |
| Recording CTA | `Watch recording` | `templates/events/events_list.html:177,183` | hardcoded |
| Empty state (past, tag filter) | `No past event recordings match this filter` (title and body identical) | `templates/events/events_list.html:291` | hardcoded |
| Empty state (past, fresh) | `No past event recordings yet` / `Check back soon!` | `templates/events/events_list.html:293` | hardcoded |
| Empty state (all view, fresh) | `No past events yet` / `Past events will appear here after they finish.` | `templates/events/events_list.html:295` | hardcoded |
| Event titles, descriptions, dates | per event | Studio-managed event records | Studio data (not this repo, not content repo) |
| Nav label | `Past Recordings` | `templates/includes/header.html:42` | hardcoded |

### 5.2 Diagnosis

The subhead assumes the visitor already belongs: it lists activities ("community moments") without saying who runs them, who can join, or that joining an open event costs nothing beyond a free account. The empty upcoming state — the state this page is in between cohorts — is a hard dead end: "Check back soon!" with no recording, workshop, or notification path, on a page the nav sends cold visitors to. The past-recordings intro is written for the team, not the visitor: "legacy discovery" and "canonical learning artifact" are taxonomy-contract language pasted into public copy.

### 5.3 Proposed rewrite

Eyebrow: `Events`

Headline option A (recommended): `Live events for AI builders`
Headline option B: `Build with the community, live`
Recommendation note: A names the audience and keeps "live events" as the page's key phrase; B assumes the visitor already knows what "the community" is.

Subhead:

> AI Shipping Labs runs live workshops, group coding sessions, and community calls. Open events are free to join with a free account, and member events show the tier they need. Recorded sessions show up under Past recordings, so you can catch up on anything you missed.

Past-filter view (`/events?filter=past`, linked from the nav as `Past Recordings`) — assessment: the current view keeps the generic live-events h1 and subhead, then adds an intro paragraph written in internal taxonomy language. For someone who clicked "Past Recordings" in the nav, the page reads as a leftover list bolted onto the events page. Proposed: when `filter=past` is active, swap the header copy.

> Headline: `Past event recordings`
> Subhead: `Catch up on live sessions you missed. Recordings from workshop sessions live on the workshop page together with the tutorial and code — those cards take you there. Standalone recordings play right here.`

This replaces the intro paragraph at `events_list.html:117-119` entirely.

Section heading `Upcoming`: keep. Section heading on the all view: keep `Past events`.

### 5.4 CTA copy

| CTA | Label | Target | Notes |
|-----|-------|--------|-------|
| Primary (anonymous, new, under subhead) | `Create a free account to register` | `/accounts/register/` | Registration requires an account; say so before the visitor hits the wall on a detail page. |
| Secondary | `Watch past recordings` | `/events?filter=past` | Text link under subhead. |
| Per past card | `Watch recording` | workshop video page or event detail | Keep as is. |
| View toggle | `List` / `Calendar` | keep | Keep as is. |

### 5.5 Empty-state and gated-state copy

Empty upcoming (the state that matters most — `events_list.html:94-97`):

> Title: `No live events on the calendar right now`
> Body: `Events run around sprints and cohorts, so the calendar has quiet stretches — the recordings don't. Watch a past session, or work through a workshop at your own pace, and check the calendar again soon. TODO(product): confirm events are announced in the newsletter; if so, add "Join the newsletter and we'll announce the next one." with a subscribe CTA.`
> Primary CTA: `Watch past recordings` → `/events?filter=past`
> Secondary CTA: `Browse workshops` → `/workshops`

Past filter, fresh (no recordings yet):

> Title: `No recordings yet`
> Body: `Recordings appear here after live sessions finish.`
> Primary CTA: `See upcoming events` → `/events?filter=upcoming`

Past filter, tag filter empty (current title and body are identical — replace the body):

> Title: `No recordings match this filter`
> Body: `Try clearing the tag, or browse every recording.`
> Primary CTA: `View all past recordings` → `/events?filter=past`

Gated state: event cards already show tier badges; the subhead's `Open events are free to join with a free account; member events show the tier they need.` is the page-level gate copy for both anonymous and free-tier visitors. Free-tier visitors hitting a Main-gated event get the upgrade path on the detail page, which is out of scope here.

### 5.6 Title and meta description

`<title>` (43 chars): `Live AI Community Events | AI Shipping Labs`

Meta description (146 chars): `Live workshops, group coding sessions, and community calls for AI builders. Register with a free account, or catch up with past event recordings.`

---

## 6. `/workshops` vs `/workshops/catalog` — verdict

Keep both pages, with deliberately different copy jobs:

| Page | Job | Copy consequence |
|------|-----|------------------|
| `/workshops` | Pitch. Explain what a workshop is, why it's worth time, prove it with the three newest. | Full hero, value cards, access line, two CTAs. This is where all persuasion lives. |
| `/workshops/catalog` | Tool. Help a visitor find the right workshop fast. | One orientation sentence, filters, grid, and a `New here?` link back to `/workshops`. No repeated pitch. |

The current split already has this shape; the copy just needs to stop calling the catalog an `Archive` (section 4) and stop hedging on the landing (section 3). Do not merge the pages and do not duplicate the hero onto the catalog.

---

## 7. Terminology drift

| # | Drift | Where | Canonical recommendation |
|---|-------|-------|--------------------------|
| 1 | `Project Showcase` (title, eyebrow) vs `Pet & Portfolio Project Ideas` (h1) vs `Project Ideas` (nav) | `projects_list.html:5,14,16`, `header.html:60` | `Project Ideas`. The glossary explicitly forbids `Showcase` for projects. Drop `Pet &` — it undersells and the glossary term is `Project`. |
| 2 | Workshops called `sessions` (`Practical AI engineering sessions`) | `workshops_list.html:18` | Per the taxonomy contract: a `workshop` is the durable page; a `session` (or `live session`) is the scheduled event occurrence. Never call the workshop itself a session. |
| 3 | `Archive` vs `catalog` for `/workshops/catalog` | `workshops.py:673` (eyebrow), `workshops.py:685` (meta says both) | `catalog`. `Archive` implies stale; every CTA leading here says `all workshops`. |
| 4 | `Past Recordings` (nav) vs `Past event recordings` (tab, heading) vs `Past events` (all-view heading) vs `Past Recording` (product.md glossary) | `header.html:42`, `events_list.html:55,108,111` | `Past recordings` for visitor-facing tab and heading (nav keeps title case `Past Recordings`); `Past events` stays only on the all-view section, where events without recordings also appear. |
| 5 | Internal taxonomy leaking into public copy: `durable hands-on learning artifacts`, `legacy discovery`, `canonical learning artifact` | `workshops_list.html:21`, `events_list.html:117-119` | Keep taxonomy language in `_docs/product.md`; public copy describes the same split in plain words (see 3.3 and 5.3). |
| 6 | `writeups` vs `tutorial pages` vs `step-by-step pages` for workshop step content | `workshops_list.html:4,18,46`, `workshops.py:655,675` | `tutorial pages` (matches routes and product.md's workshop feature table). Reserve `writeup` for the workshop landing description, and `Tutorial` alone for the separate `/tutorials` content type. |
| 7 | `community moments` | `events_list.html:19` | Name the real things: `community calls`. `Moments` is filler. |

Alignment note: `_docs/landing-pages/landing-copy.md` (the parallel audit of `/blog`, `/resources`, `/downloads`, `/courses`, `/sprints`, `/activities`) records five drifts of its own — `cohort` vs `sprint`, `plans` vs `membership tiers`, `Learning Paths` vs `courses`, `resources` vs `downloads`/`links`, and inconsistent pricing CTA labels. None conflict with the table above. This document adopts its canonical pricing CTA label, `View membership tiers`, for every button or link pointing at `/pricing` (sections 1.4 and 3.4).

## 8. Claims needing product confirmation

| Claim | Where used | Status |
|-------|-----------|--------|
| Events are announced in the newsletter | Proposed events empty state (5.5) | `TODO(product): confirm N/A or wording` — the CTA is written as conditional; do not ship the newsletter line without confirmation. |
| Questions come from `real candidate reports and job descriptions` | Kept in proposed `/interview` subhead | Pre-existing claim in the current template; `TODO(product): confirm sourcing description still holds` before reusing it more widely (for example in the meta description, where I already use it). |
| Workshop or question counts (`N workshops`, `N questions`) | Not used | Deliberately omitted; add only with real numbers. The one synced count on `/interview` (`17 take-home assignments and 5 paid work trials`) lives in the content repo and was left untouched. |

Content-repo changes (cannot be made in this repo): none of the proposed copy requires content-repo edits. Interview category titles/descriptions, project writeups, and workshop card content are content-synced and were deliberately left as data; if the team later wants to retitle a category (for example shortening `AI System Design Interview Questions` on the card), that is a change in `AI-Shipping-Labs/content`.

## 9. Stylint notes

I ran `stylint` over this document. Fixed in the proposed copy: a semicolon and a `land` (verb) in the events subhead, and a `land` in the projects empty state. Deliberate keeps:

- Table and heading-depth findings: stylint forbids markdown tables and `###` headings, but CLAUDE.md and this task's spec require tables and per-page sections. The CLAUDE.md formatting rules win for this document.
- Em dashes in proposed subheads. Landing copy needs the appositive rhythm, and each sentence still parses without the aside.
- Second-person imperative fragments in headlines and CTA labels (`Pick your next AI project`, `Watch or read`). These are display copy, not prose.
- `Check back soon` survives only inside quoted current-copy inventory, never in proposed copy.
- Long sentences flagged in the diagnosis paragraphs. Those are internal audit prose, not publishable copy, and splitting them further would fragment the argument.
