# Books (book club) — redesign direction

Date: 2026-08-05. Grounded in: the live prototype at `http://localhost:8009/books` (screenshots `cur-index-member.png`, `cur-detail-member.png`, `cur-chapter-member.png`, `cur-progress-member.png` in this scratchpad), `_docs/audits/2026-08-05-book-detail-design-audit.md`, `_docs/audits/2026-08-05-book-club-per-chapter-page-spec.md`, the surviving IA plan (`book-club-ia-plan.md`, this scratchpad), `_docs/design-system.md` in full, and web research on comparable products (sources at the end).

## 0. Diagnosis: why the current build feels clinical

The IA is already clean (single column, one gate card, chapter pages own the notes). The problem is not structure — it is that every surface leads with instrumentation:

- The book detail page opens with a metrics callout (`3 of 8 chapters read`, streak, progress link) before the book has said anything.
- The roadmap is eight identical bordered cards, each carrying a numbered disc, a status badge, and a three-icon meta strip (`Due Sep 7 · 15 read · 2 notes`). Eight rows × five signals = forty pieces of telemetry before any human voice appears.
- The chapter page opens with a button and a form (`Mark as read`, composer) — a data-entry screen — and only then shows the conversation, which is the actual reason to visit.
- `/progress` is a ranked leaderboard: rank column, progress meters, streak flames. Rank + flame is the grammar of fitness apps.
- The hub spends a third of its height on a three-card `How it works` explainer and repeats the same counters (`34 readers`, `3/8 read`) that appear again one click deeper.
- The one warm asset — the book cover — is a thumbnail; the one warm content — members' notes, which are genuinely good — is invisible until two clicks deep.

In short: the numbers are the protagonists and the book and the people are the metadata. The redesign inverts that.

## 1. Survey: what similar systems do, and the one pattern to steal or avoid

| System | What it does for reading / club UX | Steal or avoid |
|---|---|---|
| Goodreads | Shelves, ratings, groups with threaded folder discussions; spoilers handled by manual `<spoiler>` tags; per-book "what your friends thought" social proof. UI widely described as frozen-in-2013, noisy, feed-heavy. | Steal: friends' words attached to the book itself as the hook. Avoid: generic threaded forums and manual spoiler etiquette — structure should make spoilers impossible, not policed. |
| The StoryGraph | Rich stats (moods, pace, graphs) but deliberately out of the way — the home surface is the book and the review; stats live in their own tab you opt into. Calm, dark-mode-first UI. | Steal: stats exist but are a destination, never the front door. Avoid: nothing major — this separation is the model for our `/progress` page. |
| Fable | The book-club app: clubs have chapter/milestone-scoped discussion rooms that are spoiler-safe by construction; club milestones set a shared pace ("read to here by Friday") without per-person meters; reviews have a spoiler toggle. | Steal: the chapter as the spoiler boundary and the milestone as club pace, not personal score. This validates our per-chapter pages — keep them, warm them up. |
| Bookclubs.com | Organizer tooling: meetings are the spine (calendar sync, video, discussion guides), polls choose the next book. Little ambient tracking. | Steal: the meeting as the club's heartbeat — "for Thursday's call" is a warmer deadline than "Due Sep 7". Poll-the-next-book is a natural future feature for `Coming up`. Avoid: admin-tool density on member surfaces. |
| Basmo | Habit tracker: timed reading sessions, pages/percent logging, streaks, daily reminders. Reviews praise motivation but the pattern makes reading feel like logging work; note-taking is an afterthought. | Avoid: session/percent instrumentation entirely. A weekly club needs one binary per chapter (read it or not), nothing finer. |
| Notion reading trackers | DIY databases: books as rows, properties for status/rating/dates. Abandonment is the documented failure mode — upkeep friction, "makes reading feel like a chore", clumsy shared discussion in comments. | Avoid: the database-of-books feel — grids of property chips is exactly what our roadmap cards currently emulate. A club is not a personal CRUD surface. |
| Duolingo (habit apps) | Streaks, XP, leaderboards drive retention — and a documented literature of streak anxiety, guilt mechanics, and dark patterns; leaderboards demotivate everyone below the fold. | Steal only: one clear next action per visit ("continue here"). Avoid: streaks and ranks. Our audience are professionals reading a systems book, not gems collectors. Drop the flame. |
| Course-progress UIs (incl. our own reader) | Linear syllabus, checkmarks, "continue where you left off". Works because a course is a solo path with an end state. | Steal: the single resume thread (one "Open Ch. 3" action). Avoid: dashboard framing — a club is a shared calendar, not a personal completion funnel; percent-complete belongs to courses. |
| Readwise Reader / Matter (editorial reading apps) | Typography-first: generous margins, ~65ch measure, chrome recedes, the text is the interface. Matter is repeatedly cited as calm precisely because it shows fewer things. | Steal: the whole temperament. Words carry the page; numbers appear as sentences in muted text, not meters. Our Reader width tier and `.prose` already encode this — use them. |

Cross-cutting conclusions:

1. Every product that feels communal leads with people's words about the book (Fable rooms, Goodreads friends' reviews). Every product that feels like a chore leads with logging (Basmo, Notion).
2. Spoiler safety should be structural (chapter-scoped spaces), not etiquette (tags) — we already have this; it is the strongest part of the current IA.
3. Pace belongs to the club (a shared milestone tied to a meeting), progress belongs to the person (private-by-default, quiet), comparison belongs on an opt-in page (StoryGraph's stats-tab move) — never ranked.

## 2. Recommended design direction

### The feel

Editorial, not dashboard. The Books area should read like the club's weekly journal — closer to a well-set magazine page (Matter/Readwise temperament) than to a tracker. Words in `text-muted-foreground` sentences do the work that badges, meters, and icon strips do today. One accent-colored thing per screen: the single next action.

### The organizing metaphor

The weekly issue. The club runs one conversation, one book, one chapter a week, anchored to a call. So the front page of the feature is always this week: the current chapter, what people are saying about it, and when we meet. Everything else — the full chapter list, past summaries, who's reading along — is the archive and the masthead, quiet below.

This replaces the current metaphor, which is a progress dashboard (my completion state, everyone's completion state, a backlog of tracked items).

### Hero vs secondary

| Priority | Content |
|---|---|
| Hero | The book (large cover, title, author, one warm paragraph) and this week's conversation (current chapter + one or two member notes quoted, with names). |
| Secondary | The rhythm: next call, the chapter list as a quiet table of contents with your own read-marks. |
| Tertiary, opt-in | The group page (who's reading along) and compiled summaries. |
| Removed | Streaks, ranks, progress meters, per-row read/note counters, the how-it-works card trio. |

### How this fixes "too dashboardy / clinical"

- Numbers become sentences, and each number appears exactly once. `Most of the group has finished Ch. 2 — 15 of us are in Ch. 3 now.` instead of four `15 read` chips. `You're 3 chapters in; Ch. 3 is next.` instead of a callout with a counter, a streak, and a link.
- Human voices move above the fold. The prototype already has excellent fake notes ("Batch size is the lever, and we were leaving it at 1"). One of those, quoted with attribution on the book page, does more communal warmth than every badge on the site.
- Deadlines become appointments. `For Thursday's call · Sep 7` (Bookclubs pattern) instead of `Due Sep 7` (homework pattern).
- Instrumentation gets one home. Comparison lives only at `/books/<slug>/readers` (renamed from `progress`), as a calm roster, unranked, you-first — the StoryGraph stats-tab move.
- The chapter page becomes a discussion page you can also log on, instead of a logging page with a discussion under it.

### The biggest single change

Replace the book-detail progress callout + eight-card metric roadmap with a `This week` hero: current chapter, meeting date, one or two quoted member notes, and a single `Open Ch. 3 — Hardware` button — while the roadmap collapses into a quiet, badge-free table of contents. That one swap moves the page's center of gravity from my telemetry to our conversation, and every other change follows from it.

## 3. Wireframe-level IA, page by page

Guest gating is unchanged everywhere: one `_gated_access_card.html` per page, placed where the member content would start; headers and navigation stay public.

### 3.1 `/books` — hub (Frame, `max-w-7xl`)

| # | Section | vs current build |
|---|---|---|
| 1 | Header: eyebrow `Community · Books`, H1, lead. Fold the how-it-works content into the lead as one extra sentence ("Each week we read a chapter, share notes, and meet to talk it through."). | Kills the three-card `How it works` band lower down. |
| 2 | Reading now spotlight: cover-forward callout card — cover at real presence (about a third of the card), title, author, one description line, then one club-state sentence (`Week 4 of 8 — we're reading Ch. 3, Hardware, for Thursday's call`) and one quoted note excerpt with the member's name. Single CTA `Open the book`. Guests keep `{% member_access_badge %}`; members see no badges here. | Replaces the current badge cluster (3 pills) + five-item icon-meta strip + small cover. The quote is new: a human voice on the front door. |
| 3 | Coming up: existing compact-rail card, minus counters. One line: title, author, `Up next`. | Same card role, less chrome. |
| 4 | Past reads: compact-rail cards; the one metadata line becomes `Finished Jun 2026 · read the group's summary` (the summary is the artifact worth advertising, not the reader count). | Drops `41 readers` counters and sparkle icons. |

### 3.2 `/books/<slug>` — active book (Detail, `max-w-5xl`)

| # | Section | vs current build |
|---|---|---|
| 1 | Back link + book header: cover meaningfully larger (this is the poster wall of the club room), title, author, description. Resource links (`Book page`, `Kickoff event`, `#book-club on Slack`) as a quiet text-link row, not bordered chips. | Cover-forward; de-chips the header. |
| 2 | This week (member hero): callout/action card, inline action-row layout. Left: eyebrow `Week 4`, title `Ch. 3 — Hardware`, one line `For Thursday's call · Sep 7`, then one or two short note excerpts as quoted text with attribution (`"Batch size is the lever…" — Priya`). Right: single primary button `Open Ch. 3`. Below the card, one muted sentence for your own state: `You're 3 chapters in — Ch. 2 was your last read.` | Replaces the progress callout (counter + streak + link). The member's number becomes a sentence; the streak is gone; the group's voice is the hook. |
| 3 | Chapters (table of contents): section h2, then eight quiet `_list_row.html` rows — number marker, `Ch. 3 — Hardware`, right-aligned muted caption `Week 4 · Sep 7`. Your read chapters get the check marker; the current chapter gets the active-row treatment (`bg-accent/10`, `aria-current`). No status badges, no read/note counters, no per-row icon strips. | The eight bordered metric cards become an eight-line list. This is the largest visual density drop on the site. |
| 4 | Meetings: `Next call` as one or two list rows linking to the event pages. | Same content, framed as the club's heartbeat rather than an appendix. |
| 5 | Footer links: one narrative discovery link `See who's reading along ->` (to the readers page) and, once published, `Read the book summary ->`. | The leaderboard/summaries never return as widgets. |

### 3.3 `/books/<slug>/chapters/<n>` — chapter page (Reader, `max-w-3xl`)

| # | Section | vs current build |
|---|---|---|
| 1 | Back link, eyebrow `Week 4`, H1 `Ch. 3 — Hardware`, one sentence `For Thursday's call · Sep 7 — notes here are spoiler-safe through Ch. 3.` Read state as a quiet inline control next to that line: `Mark as read` (`secondary`, `sm`) flipping to `You've read this · unmark`. | The Mark-as-read primary button stops being the page hero; the icon meta strip (`15 read · 2 notes`) becomes part of the section heading below. The spoiler-safety promise (Fable's structural pattern) is now said out loud — it is a feature, so name it. |
| 2 | What the group took away (only when the summary is published): eyebrow + `.prose` body. Editorial prose, not a boxed widget. | Same content, calmer chrome. |
| 3 | The conversation: heading `Notes` with one muted caption (`2 notes · 15 of us have read this chapter`). Note cards as today (info/static, author linking to their reading page, comments inside) — they are already the warmest element; keep them. | Feed moves above the composer: the page reads as a discussion you join, not a form you fill. |
| 4 | Your note composer: after the feed, the current accent callout with textarea + `Post note`. When the feed is empty, `member_empty_state` inverts the order invitation-first (`Be the first — what stuck with you?`). | Composer relocates below the conversation (comment-thread convention); everything else per the existing per-chapter spec. |
| 5 | Prev / next chapter `_list_row.html` rows. | Unchanged. |

### 3.4 `/books/<slug>/readers` — the group (Detail, `max-w-5xl`; today `/progress`)

| # | Section | vs current build |
|---|---|---|
| 1 | Header: H1 `Who's reading along`, lead `34 of us are reading Inference Engineering.` | Renamed from `Progress`; the URL and copy stop implying a scoreboard. |
| 2 | Roster: table-like rows per the design system's comparison-list rule — identity first (avatar, name, tagline from their profile), then where they are (`Ch. 3` as text, optionally the muted `3 of 8`), then `notes shared`. Your row first, then most-recently-active. No rank column, no progress meters, no streak column, no flames. | Leaderboard becomes a roster. Comparison survives (the design system mandates table-like rows and you-first) but ranking, meters, and streaks go. Sorting by recency also stops publicly shaming the slowest readers — lighter social. |

### 3.5 `/books/<slug>/readers/<handle>` — reading page (Reader, `max-w-3xl`)

Largely as built: name, tagline, then their notes as the page body. Change: replace the stat-chip row (`8 chapters · 8 notes · 3w streak`) with one sentence (`Read through Ch. 7 · 8 notes shared`) and drop the streak. Notes remain the content.

### 3.6 `/books/<slug>/summary` and secondary books

As specified in the IA plan — Reader tier, editorial `.prose`. These pages were already the calmest in the set; they are the temperature the rest now matches. Secondary book pages drop reader-count chips the same way the hub cards do.

## 4. Expressing it inside the design system

Nothing above needs new chrome. The calm comes from choosing quieter owners, not inventing them.

### Compose with these existing roles

| Need | Owner / pattern from `_docs/design-system.md` |
|---|---|
| This-week hero, composer | Callout/action card, inline action-row layout (`_starting_soon_card.html` reference); one `{% button_classes 'primary' %}` per page |
| Chapter ToC, meetings, prev/next | `templates/includes/_list_row.html` — the sanctioned numbered-navigation row; current chapter uses the documented active-row treatment (`bg-accent/10 text-accent` + `aria-current`) |
| Quoted note excerpts | Body-lead text (`text-lg leading-relaxed text-muted-foreground`) with a `text-xs text-muted-foreground` attribution caption inside the callout — plain typography, no new quote component |
| Note cards, comments | Info/static card via `_info_card_classes.html` + `bg-card`, exactly as the per-chapter spec already defines |
| Roster | Table-like rows per Comparison and progress lists (identity -> metric -> details), viewer's row first, `overflow-x-auto` |
| Hub cards | `_content_card.html` catalog/compact-rail roles; the spotlight stays `rounded-lg` (the `rounded-xl` spotlight exception covers only tier cards and the home featured-sprint card) |
| Gate, empty states, badges | `_gated_access_card.html` once per guest page; `{% member_empty_state %}`; `{% member_access_badge %}` for guests only |
| Dates | `member_compact_date` / `member_short_date`; meeting times via the event datetime helpers once real events back the data |
| Summary / long text | Reader tier + `.prose` |

### Stop doing (the density diet)

- No per-row icon-meta strips (`calendar` + `users` + `message-square` repeated eight times). A count appears at most once per page, as a sentence.
- No hand-rolled progress meters, donuts, or bars anywhere in Books; no rank column; no streak concept at all (data field `VIEWER_STREAK` and the roster `streak` column retire).
- At most one badge per surface: guests' access badge, and `Reading now` on the current ToC row expressed as the active-row treatment instead of a pill. The current three-pill clusters violate calm, not the letter of the system — this is a composition rule.
- No `How it works` card trio; explainer copy lives in the lead paragraph.
- No bordered chip rows for external links; quiet text links with icons.
- Everything repeated stays `rounded-lg` (the existing `rounded-2xl` drift in the older bookclub templates dies with this redesign, per the audit).

### Why this stays compliant

The design system already prefers this shape: single-column flows over widget rails, table-like rows for comparison, one primary action per surface, sentences under headings instead of metadata pinned opposite them, and typography tokens that make muted prose the default voice. The current build is dashboardy because it composed the loudest sanctioned parts (badges, meters, callouts) everywhere at once; the redesign composes the quietest sanctioned parts (list rows, muted body text, one callout) and spends accent color on exactly one action per page.

## Sources

- [Fable discussion rooms (spoiler-safe, chapter-scoped)](https://help.fable.co/article/88-what-are-rooms), [Fable app review — Book Riot](https://bookriot.com/fable-book-club-app-review/), [Fable review 2026 — Headway](https://makeheadway.com/blog/fable-app-review/)
- [StoryGraph vs Goodreads — Headway](https://makeheadway.com/blog/storygraph-vs-goodreads/), [StoryGraph vs Goodreads — Astropad](https://astropad.com/blog/storygraph-vs-goodreads/), [Goodreads vs StoryGraph vs Fable — Tales of Belle](https://talesofbelle.com/2025/09/11/goodreads-vs-storygraph-vs-fable/)
- [Bookclubs app features](https://bookclubs.com/blog/the-best-of-bookclubs-app), [Bookclubs video meetings](https://bookclubs.com/blog/host-video-meetings-on-bookclubs)
- [Basmo review — Book Riot](https://bookriot.com/basmo-app-review/), [Basmo user reviews](https://justuseapp.com/en/app/1542456934/book-tracker-reading-log/reviews)
- [Notion book tracker templates and abandonment friction — ClickUp](https://clickup.com/blog/notion-book-tracker-templates/)
- [Duolingo gamification critique — UX Collective](https://uxdesign.cc/the-good-the-bad-and-the-ugly-of-duolingo-gamification-3a12f0e80dc7), [Gamification misuse case study — arXiv](https://arxiv.org/pdf/2203.16175), [Winning at what cost — HEAD Foundation](https://digest.headfoundation.org/2025/09/21/winning-at-what-cost-the-psychology-of-gamification-and-the-fight-for-our-focus/)
- [Readwise Reader design](https://blakecrosley.com/guides/design/readwise-reader), [Matter vs Readwise Reader — The Sweet Setup](https://thesweetsetup.com/is-matter-or-readwise-reader-the-read-later-app-for-you/)
- [Goodreads groups guide — Book Riot](https://bookriot.com/guide-to-goodreads-groups/)
