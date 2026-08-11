# Designer audit - logged-in home page (`/`, member dashboard)

Date: 2026-08-11

Audit of the authenticated home page as currently rendering from the uncommitted working tree (`content/views/home.py` + `templates/content/dashboard.html`, single-column `max-w-3xl` variant with the new Community strip). Captured against the live dev server on port 8000; server process start (11:39:49) postdates the last `home.py` edit and runs with autoreload, and rendered pages contain the uncommitted markers (`max-w-3xl` container, `dashboard-community-strip`), so the screenshots show the current working-tree UI.

The core complaint driving this audit: the information arrangement feels random. This report treats information architecture as the spine and visual polish as secondary.

## Screenshots

| State | User / tier | Viewport | CloudFront URL | Local file |
|---|---|---|---|---|
| Paid, dark | `designer-premium@test.com` (Premium, level 30) | 1280x900 desktop | https://d31nukezbn4e3o.cloudfront.net/2026/08/11/0494dd4acb104c96ab6d1bd598618cbb-027c92ba4e2195c6.png | `.tmp/designer-audit-home-loggedin/home_paid-premium_1280x900_dark.png` |
| Paid, dark | same | 393x851 Pixel 7 | https://d31nukezbn4e3o.cloudfront.net/2026/08/11/a2549a858ba94b9ab79e02fb1eb19864-edcb29859a20aeba.png | `.tmp/designer-audit-home-loggedin/home_paid-premium_393x851_dark.png` |
| Paid, light | same | 1280x900 desktop | https://d31nukezbn4e3o.cloudfront.net/2026/08/11/c0db91233e7c42b1ac3f36d352a5814d-8668896a9c0822a5.png | `.tmp/designer-audit-home-loggedin/home_paid-premium_1280x900_light.png` |
| Free, dark | `designer-free@test.com` (Free, level 0) | 1280x900 desktop | https://d31nukezbn4e3o.cloudfront.net/2026/08/11/99071c36e8494ce2bb1cce7c671ef3e5-b1043202db43346c.png | `.tmp/designer-audit-home-loggedin/home_free_1280x900_dark.png` |
| Free, dark | same | 393x851 Pixel 7 | https://d31nukezbn4e3o.cloudfront.net/2026/08/11/163ddf9728b34c458e36e92ffefc1319-0eb0acea02ca4a01.png | `.tmp/designer-audit-home-loggedin/home_free_393x851_dark.png` |

Viewport-height (above-the-fold) companions exist next to each file with a `_fold` suffix.

Tier state was verified in `manage.py shell` before capture. Both users were blank accounts, so the following representative data was seeded in the local dev DB to make each state genuine rather than empty-state:

- `designer-premium@test.com`: active enrollment in `python` (9/52 units complete), registrations for events 55 and 49, `SprintEnrollment` plus a shared cohort-visible `Plan` on `local-active-sprint` (4 weeks, 8 checkpoints, 3 done). The level-20 current book (`inference-engineering`) surfaces automatically at this tier.
- `designer-free@test.com`: active enrollment in `aihero` (2/50 units), registration for event 52. This makes 2 of 3 free-activation checklist items complete.

The Playwright capture dismissed the analytics consent dialog (it overlays mid-page content on load) and parked the mouse at the origin; an earlier capture proved that resting the cursor on a card flips its `hover:border-accent/50` state, which is itself a finding (see finding 8).

## Summary

The page is not primarily too dense; it is unordered. Each section was appended by the issue that shipped it, so the page reads as a chronological changelog of features rather than an answer to "what should I focus on this week" - the exact promise its own subtitle makes. The fix is one explicit organizing principle (commitment-first timeline), five named zones with membership rules, and real cuts: dissolve the Community strip, cut the Explore grid, and keep one primary CTA per zone.

## A. Current arrangement, reverse-engineered

### Ordering is template append order, not a decision

`content/views/home.py` `_dashboard()` builds a flat context dict; no ordering logic exists anywhere in the view. `templates/content/dashboard.html` hardcodes the sequence. Every conditional card cites the issue that added it (`#705` starting soon, `#1161` checklist, `#442`/`#1199` plan card, `#802` onboarding prompt, `#1129` dismissals, `#365` merged learning, `#971`/`#953` Slack). Each landed at whatever slot was convenient at the time. There is no data-driven or persona-driven ordering anywhere. That accretion is the "feels random" the user is reporting.

### Paid page, top to bottom (as rendered for `designer-premium@test.com`)

| # | Section | Template lines | Implied reason it sits here | Assessment |
|---|---|---|---|---|
| 1 | Welcome h1 + tier pill | `dashboard.html:41-55` | Identity/status convention | Fine. Subtitle promises "what to focus on this week" - a priority framing the page then abandons |
| 2 | Starting soon card (conditional, 10-min window) | `_starting_soon_card.html` | Urgency; #705 explicitly made it the urgency surface | Clear reason; correct slot |
| 3 | Sprint plan card, "Your next step" | `dashboard.html:144-215` | Flagship paid commitment | Clear reason; correct slot |
| 4 | Continue learning (course + book club cards) | `dashboard.html:220-357` | Resume | Defensible, but it outranks time-bound commitments below it; no stated reason for that inversion |
| 5 | Up next (registered events) | `dashboard.html:360-407` | Commitments with dates | No reason found for sitting below undated resume items. Dated commitments are more perishable than self-paced learning |
| 6 | Onboarding prompt / plan preparing (conditional, only when no plan) | `dashboard.html:410-471` | Guidance | No reason found. When there is no plan, this IS the member's next step toward one - yet it renders four sections lower than the plan card renders when a plan exists. The same job changes rank depending on state |
| 7 | Community strip (poll + Slack + sprints discovery) | `dashboard.html:473-524` | Cross-links | No reason found for position or grouping. Three unrelated jobs (vote, get help, discover cohorts) share one shell because they are all "communityish" |
| 8 | Latest from the community | `dashboard.html:528-555` | Discovery | Reasonable tail placement |
| 9 | Explore (5 nav tiles) | `dashboard.html:558-573` | Legacy quick actions | Duplicates the global header nav (Community/Learning dropdowns) and the footer; no independent reason to exist |

### Free page, top to bottom (as rendered for `designer-free@test.com`)

| # | Section | Implied reason | Assessment |
|---|---|---|---|
| 1 | Welcome h1 + Free pill | Identity | Fine |
| 2 | Getting started checklist (2 of 3 complete) | Activation; #1161 | Clear reason; correct slot for a new free member |
| 3 | Sprint plan teaser ("View paid memberships") | Conversion | The only section whose position serves the business rather than the member: it outranks every piece of member content. No member-side reason found |
| 4-9 | Continue learning, Up next, Community, Latest, Explore | Same as paid | Same issues as paid |

### Density and hierarchy measurements

Desktop viewport is 1280x900 with 72px of fixed header chrome (828px usable).

| Metric | Paid | Free |
|---|---|---|
| Page height, desktop / mobile | 2345px (2.6 viewports) / 3632px (4.3 viewports) | 2398px (2.7) / 3765px (4.4) |
| Labeled sections + unlabeled callouts | 5 h2 sections + 1 callout | 5 h2 sections + 2 callouts |
| Bordered containers on the page | 17 (1 plan + 2 learning + 2 events + 1 community shell + 3 nested minis + 3 content rows + 5 explore tiles) | 18 (checklist shell + 3 rows inside, teaser, 1 learning, 1 event, community shell + 3, 3 rows, 5 tiles) |
| Clickable targets (cards, rows, buttons, view-all links) | ~19 | ~18 |
| Primary (accent-filled) buttons | 3 (Open my plan, Continue, Start reading) | 2 (View paid memberships, Continue) |
| Distinct type treatments above the fold | 7 (30px h1, 24px card h2, 20px section h2, 16px titles, 14px body, 12px meta, 11-13px uppercase eyebrows) | 6 |
| Above-the-fold content vs chrome | Good: plan card + course card + part of book club are all "content the member came for"; 3 competing primary CTAs is the problem, not emptiness | Poor: the entire first viewport is setup/upsell (checklist + teaser); the first resumable content sits at the fold line (~866px). 0 percent of the free above-the-fold is the member's own activity |

Above the fold the paid page actually has a defensible top (plan card first), but three accent-filled primaries within ~700 vertical pixels (Open my plan, Continue, Start reading) means no single dominant action; all three shout at the same volume. Below the fold, rank stops meaning anything: every section gets the same `space-y-8` gap, the same `text-xl` icon+h2 header, and the same card chrome, so "Up next" (a commitment) and "Explore" (nav duplication) read as equally important.

### Design-system violations (numbered findings)

1. Mobile overflow, Community strip (paid Pixel 7 screenshot). The three mini-cards overflow the viewport right edge and are clipped by `overflow-x-hidden` on `<main>`: arrow icons and right padding are cut, the poll title runs to the screen edge. Cause: the grid items (`<a>` at `dashboard.html:484` etc.) lack `min-w-0`, so the long poll title's nowrap min-content widens all three tracks past the container. The free page does not overflow (short labels), which confirms the content-length dependency. Breaks the mobile-behavior rule (no clipped badges/controls) and the 44px tap-target intent for the clipped arrows.
2. Container width contradicts the contract. `dashboard.html:26` uses `mx-auto max-w-3xl`, but `_docs/design-system.md` (including its uncommitted edits, lines 141 and 292) still assigns the member dashboard to the `max-w-7xl` Frame, and `content/tests/test_container_widths.py` has no `dashboard.html` entry. Resolved: `max-w-3xl` is adopted (see Decisions). The remaining work is to amend the doc and add the route-table/test entry, not to change the template.
3. Section h2 scale is off-ramp. All five section headers use `text-xl font-semibold tracking-tight`; the type ramp's smallest section h2 is `text-2xl ... sm:text-3xl`. `text-xl` may be the right size for a 3xl-wide dashboard, but it is currently an undocumented seventh heading size. Open PM question 2.
4. Decorative icons inside h2s. Every section h2 embeds an accent Lucide icon (`dashboard.html:222-223, 362-363, 475-476, 530-531, 559-560`). The listing contract explicitly bans decorative icons inside group H2s; the dashboard replicates the banned pattern five times, and the icons add a sixth accent element per viewport competing with CTAs.
5. Hand-rolled button chrome. The starting-soon CTA (`_starting_soon_card.html:41`) hand-rolls `rounded-lg bg-accent px-5 py-3 ...` instead of `{% button_classes %}`; `px-5 py-3` is not one of the three sanctioned size paddings and `rounded-lg` differs from the button `rounded-md`.
6. Eyebrow tracking. `tracking-wider` is used on the starting-soon eyebrow (`_starting_soon_card.html:23`) and the Community mini-card labels (`dashboard.html:486, 493, 501` etc.); the contract says eyebrows use `tracking-widest`, never `tracking-wider`.
7. Card radius/padding drift. The plan card (`rounded-xl ... p-5 sm:p-6`, `dashboard.html:145`), the Community strip shell (`rounded-xl ... p-5 sm:p-6`, line 473), and the free checklist (`rounded-xl`, line 63) all use spotlight radius; the callout/action contract is `rounded-lg` + `p-6` (spotlight `rounded-xl p-5 sm:p-8` is reserved for tier cards and the home featured-sprint). `p-5 sm:p-6` is not a sanctioned card padding pair anywhere.
8. False hover affordance on static cards. Continue-learning, book-club, and event cards are non-clickable `<div>`s that carry `hover:border-accent/50` (`dashboard.html:231, 261, 297, 371`); only their inner buttons navigate. The card contract reserves hover affordance for clickable cards. Screenshot evidence: an early capture where the cursor happened to rest on the book-club card shows it lit up with an accent border, promising a navigation that does not exist.
9. Card titles off-contract. Row/card titles use bare `font-medium` (16px) (`dashboard.html:234, 264, 304, 375, 544`); the compact card title role is `text-base font-semibold leading-snug text-foreground`. Combined with `font-medium` mini-card labels and `font-semibold` h2s, the page carries two competing title weights for the same role.
10. View-all links use a third link dialect. "View all events" / "View all" (`dashboard.html:366, 534`) render as bare accent text with `hover:bg-secondary`, matching neither the narrative discovery-link recipe (accent + arrow icon + underline hover) nor the header/action-row pattern (`sm:items-end sm:justify-between`); they sit on their own stray line under the h2.
11. Repeated-grid gaps. Community minis, recent-content rows, and Explore tiles use `gap-3`/`space-y-3`; repeated card grids are `gap-6` (tight operational rows `gap-4`). Minor, but it contributes to "everything is a slightly different card".

Light/dark parity is good: the light capture shows correct token usage throughout (no hardcoded darks), and contrast of `text-muted-foreground` metadata is acceptable in both themes.

## B. Jobs-to-be-done

Grounded in `_docs/product.md` personas (Free member, Basic, Main, Premium) and the feature inventory.

### Paid member (Main/Premium)

| Job | Current page | Verdict | Evidence |
|---|---|---|---|
| What am I committed to next, and when do I show up | Up next (position 5), starting-soon card (only within 10 min of start) | Buried | Registered events render below self-paced learning; a Thursday event is invisible above the fold on Monday. The 10-minute urgency card is great but there is no "this week" view between "starting in 8 min" and "position 5" |
| What do I owe my sprint cohort this week | Plan card, position 3 | Served | Checkpoints 3/8 + Open my plan is the strongest block on the page; correct top placement |
| Resume what I was doing | Continue learning, position 4 | Served | Course + book club with progress bars and next-item labels; good. Weakened only by three same-weight primary CTAs |
| What is new since I last visited | Latest from the community, position 8 | Partially served | Static latest-3 list, not "since last visit"; acceptable for now, position is right |
| Get help / talk to people | Slack card (only when not yet a member, dismissible), Request a call (only post-onboarding, as an unlabeled Explore tile) | Buried to missing | A connected Main/Premium member has no "ask the community" or "book a call" entry on the page at all in common states |
| Influence what gets built (polls) | Community strip mini-card | Buried and broken | On desktop the poll title truncates to "Which mini-..." (unreadable); on mobile it overflows the viewport. The one member-actionable, expiring item on the strip is its least legible |
| Check my status (tier, billing) | Tier pill in header | Served | Adequate; deep status lives on `/account` |

### Free member

| Job | Current page | Verdict | Evidence |
|---|---|---|---|
| Get value now (open course, free events) | Checklist (position 2), Continue learning (4), Up next (5) | Buried | Everything the member can actually do today sits below the paid teaser; first resumable item is at the fold |
| Set up my account / learn the ropes | Getting started checklist, position 2 | Served | Good pattern: progress bar, done states, one action per row |
| Understand what I am missing and whether it is worth paying | Sprint plan teaser, position 3 | Missed - it adds noise instead | The teaser is one abstract paragraph + a pricing link. It shows nothing concrete: no locked event, no gated course, no cohort activity evidence. Meanwhile the page pitches sprints three separate times (checklist item 3, teaser, "Build together" community card) and the Community strip links a Free member to a level-20 book and Main+ polls with no lock badges - upsell by dead-end rather than by demonstration |
| See what is happening in the community | Latest (7), community strip (6) | Partially served | Latest rows are open-content filtered, good; the strip's Book Club / polls links are gated surprises |

## The missing organizing principle

Named principle: commitment-first timeline. Order everything by when it needs the member, then by how bound the member already is to it:

Now -> This week -> In progress (no date) -> New for you -> Browse and connect.

Justification: the page header already promises exactly this ("Here's what to focus on this week"); the strongest member jobs above are all time-bound (event start times, sprint checkpoints, book chapter deadlines); and the rule assigns every current and future card an unambiguous slot with a simple test - Does it have a date within ~7 days and did the member commit to it? Zone 2. Is it started but undated? Zone 3. Is it new/rotating and uncommitted? Zone 4. Is it static navigation or a one-time nudge? Zone 5. This is deliberately one principle, not a menu: urgency-based and job-based orderings collapse into it because member commitments with dates are both the most urgent and the highest-value jobs.

## Zones

| Zone | Label (user-visible) | Membership rule | What does NOT belong |
|---|---|---|---|
| 1 | (no header; the card is the label) | Live or imminent items requiring action in minutes: starting-soon card | Anything without a countdown |
| 2 | This week | Dated commitments the member opted into, within ~7-14 days: sprint plan card (primary CTA), registered event rows, book-chapter deadline row; when the member has no plan, the onboarding prompt / plan-preparing card takes the plan slot | Un-registered events, promos, browse links |
| 3 | Pick up where you left off | Started, self-paced, undated: in-progress courses/workshops, book-club resume when no imminent deadline | Anything not yet started; anything with a due date this week (that surfaces in Zone 2) |
| 4 | New for you | Rotating/fresh items not yet committed to: latest content rows, active poll row (full title, closing date) | Static links; things already in Zones 2-3 |
| 5 | More (deprioritized tail) | Static navigation and one-time nudges: compact text-link row to Courses/Workshops/Events/Resources/Projects, Slack join callout (until dismissed), sprint discovery for members without a plan; for Free members, the single consolidated Unlock block | Anything with a date or progress state |

### Section -> zone mapping

| Current section | Zone | Why | Verdict |
|---|---|---|---|
| Starting soon card | 1 | Countdown urgency | Keep |
| Sprint plan card | 2 | Dated commitment, the week's anchor | Keep; sole primary CTA of the page |
| Up next events | 2 | Dated commitments | Merge into Zone 2 as compact list rows under the plan card (drop per-row "View event" buttons; whole-row anchors) |
| Onboarding prompt / plan preparing | 2 | It is the plan-slot substitute when no plan exists | Move up; renders in the plan card's position |
| Continue learning (courses/workshops) | 3 | Started, undated | Keep; demote CTAs to secondary |
| Book club card | 3 (resume) + a deadline row in 2 when a chapter is due within 7 days | Split personality: it is both resume and commitment | Collapse to one compact row in 3; emit a Zone 2 row only when a deadline is imminent |
| Latest from the community | 4 | Fresh, uncommitted | Keep |
| Active poll (from Community strip) | 4 | Rotating, expiring, actionable | Merge as a full-width list row with untruncated title |
| Slack join card | 5 | One-time nudge, dismissible | Collapse to Zone 5 callout; hide entirely once `slack_member` |
| "Build together" sprints card | 5 | Discovery | Cut for members who already have a plan (they are in a sprint); Zone 5 link otherwise |
| Community strip shell | - | Grab-bag with no shared rule | Cut (dissolved into Zones 4-5) |
| Explore tile grid | 5 | Duplicates header nav dropdowns and footer | Cut as tiles; replace with one compact text-link row |
| Free checklist | 2 (free) | It is the new free member's dated-ish commitment: finish setup | Keep at top until complete/dismissed |
| Free sprint teaser | 5 (free) | Conversion, not a member commitment | Move below Zone 4 and merge with the gated-content evidence into one Unlock block; cut the duplicate sprint pitches |

## Making the grouping visible without reading everything

- Zone headers, not section headers: at most four plain-language h2s per page ("This week", "Pick up where you left off", "New for you", "More"). Zone 1's card is its own label. No icons in h2s.
- Whitespace rhythm carries the grouping: rows inside a zone sit at `space-y-4`; zones separate at `mt-16` (desktop) / `mt-12` (mobile). Today's uniform `space-y-8` is exactly why the page reads as one undifferentiated pile.
- At most two card treatments: (a) the accent-bordered callout card - plan card and starting-soon only; (b) plain list rows (`border-b` or quiet `bg-card` rows) for everything else. Events, poll, latest content, book club, and courses all become rows, not five bespoke card designs.
- One accent-filled primary button per page state ("Open my plan"; for free members "Continue" on AI Hero, or the checklist action while incomplete). Everything else is secondary chrome or a whole-row anchor. Rank is communicated by position and the single primary, not by decoration.

## C. Recommended structure

### Paid, desktop (max-w tier per open question; single column shown)

```
+================ ZONE 1 - NOW (conditional) =================+
| [accent callout] Starting soon - Office hours   [Join now]  |
+=============================================================+

+================ ZONE 2 - THIS WEEK =========================+
| [accent callout] Your sprint plan                           |
|   Local Active Sprint - 3/8 checkpoints - progress bar      |
|   [ Open my plan ]  <- the ONLY primary button on the page  |
|                                                             |
|  Thu Aug 14  19:00  Office hours - Session 1            >   |
|  Mon Aug 17  20:00  Building Evals for LLM Apps         >   |
|  Sun Aug 17  due    Book club - Chapter 0 (Inference)   >   |
|  View all events                                            |
+=============================================================+
              (mt-16 whitespace = zone boundary)
+========= ZONE 3 - PICK UP WHERE YOU LEFT OFF ===============+
|  Python for AI Engineering   9/52  [====      ] (Continue)  |
|  Inference Engineering (book) 0/8  [          ] (Resume)    |
+=============================================================+

+================ ZONE 4 - NEW FOR YOU =======================+
|  POLL  Which mini-course should we create next?  closes...  |
|  ARTICLE  Hiring Manager Interview                  Jul 22  |
|  ARTICLE  AI Engineering Tradeoffs                  Jul 21  |
|  WORKSHOP  Tailor Your CV                           Jul 8   |
|  View all                                                   |
+=============================================================+

+================ ZONE 5 - MORE (collapsed tail) =============+
|  Courses - Workshops - Events - Resources - Projects        |
|  [Slack callout, only until joined/dismissed]               |
+=============================================================+
```

### Paid, mobile (393px)

Same zone order, full-width rows; Zone 2 event/book rows stack date-over-title; Zone 5 link row wraps to two lines. Nothing about the zone model changes on mobile - which is the point: today's mobile page is 4.3 screens of same-weight cards, the restructure makes screens 1-2 = Zones 1-2, screen 3 = Zone 3, screen 4 = Zones 4-5.

### Free, desktop

```
+================ ZONE 2 - THIS WEEK (free variant) ==========+
| [card] Getting started - 2 of 3 - progress bar              |
|   (v) Start AI Hero                    Done                 |
|   (v) Register for a free event        Done                 |
|   (3) Learn how sprints work           [View sprints]       |
|                                                             |
|  Sat Aug 15  20:00  Monthly Meetup                      >   |
+=============================================================+

+========= ZONE 3 - PICK UP WHERE YOU LEFT OFF ===============+
|  AI Hero: 7-Day Crash-Course  2/50  [=  ]  [ Continue ]     |
|                       ^ the free page's single primary CTA  |
+=============================================================+

+================ ZONE 4 - NEW FOR YOU =======================+
|  3 open-content rows + View all                             |
+=============================================================+

+============ ZONE 5 - UNLOCK (one block, concrete) ==========+
|  What paid members are doing this month:                    |
|  - Sprint cohort: Local Active Sprint (Aug 4 - Sep 1)       |
|  - Book club: Inference Engineering (Main)         [lock]   |
|  - 2 member-only events this month                 [lock]   |
|  [View paid memberships]  (secondary or single primary-lg)  |
|                                                             |
|  Courses - Workshops - Events - Resources - Projects        |
+=============================================================+
```

### Free, mobile

Same order; checklist collapses completed rows to single lines to keep Zone 2 under one screen.

### Explicit cuts and collapses (the reduction, not just addition)

1. Cut the Explore tile grid (5 bordered tiles, ~200px + heading): replace with one text-link row in Zone 5. It duplicates the header dropdowns and footer.
2. Cut the Community strip shell and its "Stay connected without losing your place" framing: poll becomes a Zone 4 row, Slack a Zone 5 callout, sprints discovery a Zone 5 link (and nothing at all for members already in a sprint).
3. Cut two of the three sprint pitches on the free page: keep checklist item 3, delete the "Build together" card, fold the teaser into the single Zone 5 Unlock block.
4. Cut per-row "View event" secondary buttons: whole-row anchors with the shared row affordance.
5. Cut the five h2 icons and two of the three primary buttons (paid).
6. Collapse the book club card (currently the tallest learning card: eyebrow + title + author + 4-item meta + progress + button) into one row plus an optional Zone 2 deadline row.

Net effect on the paid page: 17 bordered containers -> ~6 (2 callouts + ~4 zone shells/row groups); 3 primary CTAs -> 1; 6 section headers -> 4 zone headers.

## Concrete Tailwind class diffs (highest value, implementable now)

Fix the mobile overflow (Community strip grid items, `dashboard.html:484, 491, 499, 505, 511, 518` - applies wherever the mini-cards survive):

```diff
- <a href="{{ poll.get_absolute_url }}" class="group flex min-h-[92px] items-center gap-3 rounded-lg border border-border bg-background p-4 ...">
+ <a href="{{ poll.get_absolute_url }}" class="group flex min-w-0 min-h-[92px] items-center gap-3 rounded-lg border border-border bg-background p-4 ...">
```

Reasoning: grid items default to `min-width: auto`; the nowrap truncated title widens all tracks past the container and `overflow-x-hidden` on `<main>` clips the result. `min-w-0` on the grid item restores truncation. (Design system: mobile behavior - no clipped overflow.)

One primary per page - demote resume CTAs (`dashboard.html:248, 253, 282, 287, 326`):

```diff
- <a href="{{ item.next_unit.get_absolute_url }}" ... class="{% button_classes 'primary' 'shrink-0' %}">
+ <a href="{{ item.next_unit.get_absolute_url }}" ... class="{% button_classes 'secondary' 'shrink-0' %}">
```

Reasoning: the callout contract gives the explicit `{% button_classes %}` CTA to the callout card; only the plan card is the page's next-step callout. Three same-size accent-filled buttons in the first 700px is why nothing reads as the next action.

Remove false hover affordance from static cards (`dashboard.html:231, 261, 297, 371`):

```diff
- <div class="rounded-lg border border-border bg-card p-5 transition-colors hover:border-accent/50" data-testid="continue-learning-course">
+ <div class="rounded-lg border border-border bg-card p-5" data-testid="continue-learning-course">
```

Reasoning: cards section, principle 3 - "A static card with `hover:border-accent/50` promises navigation that does not exist."

Starting-soon CTA through the owner tag (`_starting_soon_card.html:40-45`):

```diff
- <a href="{{ starting_soon.event.get_absolute_url }}"
-    class="inline-flex min-h-[44px] shrink-0 items-center justify-center gap-2 rounded-lg bg-accent px-5 py-3 text-sm font-medium text-accent-foreground transition-colors hover:bg-accent/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background"
+ <a href="{{ starting_soon.event.get_absolute_url }}"
+    class="{% button_classes 'primary' 'shrink-0' %}"
```

Reasoning: buttons section - every non-Studio CTA uses `{% button_classes %}`; `px-5 py-3` is not a sanctioned size. (Template must `{% load accounts_extras %}`.)

Eyebrow tracking (`_starting_soon_card.html:23`; `dashboard.html` mini-card labels):

```diff
- <p class="text-xs font-semibold uppercase tracking-wider text-accent">
+ <p class="text-xs font-medium uppercase tracking-widest text-accent">
```

Reasoning: typography - "Eyebrows use `tracking-widest`, never `tracking-wider`."

Callout radius/padding to contract (`dashboard.html:145, 473, 63`):

```diff
- <section class="mb-8 rounded-xl border border-accent/30 bg-card p-5 sm:p-6" id="sprint-plan-section" ...>
+ <section class="mb-8 rounded-lg border border-accent/30 bg-card p-6" id="sprint-plan-section" ...>
```

Reasoning: callout/action card role is `rounded-lg` + `p-6`; `rounded-xl` spotlight is reserved for tier cards and the home featured-sprint (unless PM extends the exception - open question 3).

Card titles to the compact-title role (`dashboard.html:234, 264, 304, 375, 544`):

```diff
- <h3 class="truncate font-medium text-foreground">{{ item.course.title }}</h3>
+ <h3 class="truncate text-base font-semibold leading-snug text-foreground">{{ item.course.title }}</h3>
```

Zone rhythm (structure of the restructured page):

```diff
- <div class="space-y-8">
+ <div class="space-y-12 sm:space-y-16">
    <!-- each zone -->
-   <section class="min-w-0">
-     <h2 class="flex min-w-0 items-center gap-2 text-xl font-semibold tracking-tight text-foreground">
-       <i data-lucide="calendar" class="h-5 w-5 shrink-0 text-accent"></i>
-       Up next
-     </h2>
+   <section class="min-w-0 space-y-4">
+     <h2 class="text-xl font-semibold tracking-tight text-foreground">This week</h2>
```

Reasoning: inter-zone whitespace (12/16) vs intra-zone rows (4) is what makes the grouping perceivable without reading; icons removed per the no-decorative-icons-in-h2 rule. (Exact h2 size pending open question 2.)

## Ranked recommendations

| Rank | Recommendation | Impact | Effort |
|---|---|---|---|
| 1 | Reorder into the five commitment-first zones: move onboarding prompt into the plan slot, merge events + book deadline into "This week" under the plan card, one primary CTA per page | High - directly answers "feels random"; fixes the buried-events job | Medium (template restructure, no data-model change) |
| 2 | Cut Explore tiles and dissolve the Community strip into Zones 4-5 (poll as full-width row, Slack as tail callout, sprints link only when planless) | High - removes 9 of 17 bordered containers and the least legible card | Low-medium |
| 3 | Fix the Community strip mobile overflow (`min-w-0` on grid-item anchors) | High on mobile - currently clipped/broken | Trivial |
| 4 | Free page: single Unlock block below "New for you" with concrete gated items; delete duplicate sprint pitches; checklist stays on top | Medium-high - turns upsell from noise into demonstration | Medium |
| 5 | Demote resume CTAs to secondary; remove hover affordance from static cards; card titles to `text-base font-semibold` | Medium - hierarchy legibility | Low |
| 6 | Zone whitespace rhythm (`space-y-12/16` between, `space-y-4` within) + h2s without icons | Medium | Low |
| 7 | `{% button_classes %}` on starting-soon CTA; eyebrow `tracking-widest`; callout `rounded-lg p-6`; grid gaps to `gap-4`/`gap-6` | Low - contract hygiene | Trivial |
| 8 | Record the adopted `max-w-3xl` dashboard width in `_docs/design-system.md` and add the `dashboard.html` entry to `content/tests/test_container_widths.py` (decision already made - see Decisions) | Low visually, high for contract integrity | Low (doc + test) |

Top three: 1, 2, 3.

## Decisions

1. Dashboard width - DECIDED 2026-08-11: keep `max-w-3xl` for the authenticated home dashboard. Single-column is the adopted direction; do not revert to `max-w-7xl`. Follow-up work required: amend `_docs/design-system.md` so the member dashboard is assigned to the 3xl Frame instead of 7xl, and add a `dashboard.html` entry to `content/tests/test_container_widths.py` so the contract is enforced. Finding 2 and ranked recommendation 8 are resolved by this decision and reduce to the doc/test update. If the plan card's internal two-column grid is cramped at 3xl, fix it inside the card (stack it) rather than widening the page.

## Open PM questions

1. Dashboard section-header scale: sanction a dashboard h2 role at `text-xl font-semibold tracking-tight` (new ramp entry) or move to the documented `text-2xl` smaller-section h2?
2. Does the sprint plan card earn the `rounded-xl` spotlight exception (alongside tier cards and the home featured-sprint), or does it follow the `rounded-lg p-6` callout contract?
3. Free-member gating on community links: the strip currently sends Free users to a level-20 book and Main+ polls with no lock badges. In the Unlock block, should these render with `{% member_access_badge %}` locks (demonstration) or be hidden entirely?
4. Where does "get help" live for connected paid members? Slack deep link (`slack_profile_url` exists in context but is unused by the template) and Request a call currently have no stable home; proposal: Zone 5 row, but that is a product decision.
5. Should "New for you" become genuinely since-last-visit (requires a last-seen timestamp) or stay latest-3?

## Out of scope

- The anonymous marketing homepage (`home.html`), header/nav, and footer.
- The analytics consent dialog: it overlays mid-page dashboard content on load (observed covering the book-club card). Component placement is global, not dashboard-owned; worth its own small issue.
- Event detail, plan detail, and `/account` surfaces linked from the dashboard.
- Backend/eventing work implied by "since last visit" or deadline-aware Zone 2 rows (view changes needed, but data largely exists: `next_chapter.deadline`, `dashboard_formatted_start`).
- Test-data note: the seeded rows listed in the Screenshots section remain in the local dev DB (`db.sqlite3`) for reproduction; nothing was committed.
