# Per-chapter page — Book Club prototype spec

Design spec for a dedicated page per chapter of the active Book Club book,
where a member marks the chapter read, writes their own note, and reads and
comments on everyone else's notes. Extends the existing prototype
(`bookclub/prototype_data.py`, `templates/bookclub/`), does not break the
current pages.

## 1. Requirements

### What the page does

| Capability | Member | Guest |
|---|---|---|
| Chapter header: number, title, week, deadline, status | Yes | Yes |
| Cohort stats: N read, N notes | Yes | Yes |
| Mark as read / marked-read indicator | Yes | No — gated |
| Own note: add, edit | Yes | No — gated |
| Group notes feed (author, body, likes, comments) | Yes | No — gated (counts visible in header only) |
| Comment on a note | Yes | No — gated |
| Chapter summary (once published) | Yes | Teaser line only, behind gate |
| Prev / next chapter navigation + back to book | Yes | Yes |
| Join / sign-in gate card | — | Yes, replaces all member sections |

Access rule: the book carries `required_level` (20 = Main in the prototype).
The chapter page is a tier-gated content surface: header and navigation are
public (they are discovery/SEO surface, same as the book detail header), but
the participation body (mark-as-read, note editor, notes feed, comments,
summary body) requires membership. Guests get exactly one gate card, not a
lock icon per section.

### State matrix

Two independent axes that the current prototype conflates in one `status`
field, plus the note axis:

| Axis | Values | Drives |
|---|---|---|
| Chapter timeline (cohort) | `upcoming` / `current` / `done` (deadline-relative) | Status badge tone, whether "Reading now" ring shows, whether summary can exist |
| Viewer read state | not read / read | Mark-as-read button vs marked-read indicator |
| Viewer note | empty / written | Editor shows blank composer vs the saved note with Edit |
| Summary | unpublished / published | Summary section hidden (member: "compiled after the deadline" hint) vs summary card |
| Notes feed | 0 notes / N notes | `{% member_empty_state %}` vs feed |
| Viewer | guest / member | Gate card vs participation body |

Interactions worth calling out:

- A member can mark an `upcoming` chapter read (reading ahead is allowed today
  on `book_detail`; keep that). The button is secondary, not primary, there.
- A member can write a note only after marking the chapter read? No — keep it
  permissive: note composer is always available to members; posting a note
  implies nothing about read state. Simpler mental model, matches the
  prototype's independent `notes_count` / `readers_done` numbers.
- `current` + not read is the page's prime state: primary Mark-as-read CTA.
- `done` + read + note written is the steady state: green read indicator,
  saved note with Edit, summary card if published.

## 2. URL and data shape

### Route

```
/books/<slug>/chapters/<int:number>          name="bookclub_chapter_detail"
```

- No trailing slash (site-wide rule, `RemoveTrailingSlashMiddleware`).
- `<int:number>` not `<slug>` — chapters are numbered in the source data and
  the book starts at Chapter 0, so `path("books/<slug:slug>/chapters/<int:number>", ...)`
  must accept 0. Example: `/books/inference-engineering/chapters/3`.
- 404 for unknown slug, non-active books (secondary books have no chapter
  pages in the prototype), and out-of-range numbers.
- View follows the existing pattern: `_base_context(request)` + chapter lookup
  + `?view=member|guest` override via `_resolve_is_member`.

### Data shape (extend `prototype_data.py`, additive only)

Existing `CHAPTERS` entries keep every current field (`number`, `title`,
`deadline`, `week`, `status`, `readers_done`, `notes_count`, `your_note`) so
`book_detail.html` keeps rendering unchanged. Add per chapter:

```python
{
    # existing fields unchanged ...
    "viewer_read": True,          # split out of "status": viewer axis
    "timeline": "done",           # cohort axis: done | current | upcoming
    "summary": {                  # None until published
        "published": "Sep 2",
        "teaser": "One-paragraph lead of the compiled summary...",
        "body": "Full compiled-from-notes summary text...",
    },
    "notes": [                    # group notes, same shape as PUBLIC_PROFILE notes
        {
            "name": "Priya Nair",
            "handle": "priya",    # links to bookclub_reader_profile
            "posted": "2 days ago",
            "body": "...",
            "likes": 12,
            "you": False,         # True marks the viewer's own note in the feed
            "comments": [
                {"name": "Marco Silva", "body": "..."},
            ],
        },
    ],
}
```

Notes reuse the `PUBLIC_PROFILE["notes"]` comment shape exactly (name + body),
plus `handle`/`posted`/`likes` which the profile page already renders, so the
note card partial can eventually be shared between `reader_profile.html` and
the chapter page. Derivable, not stored: `notes_count == len(notes)` for
chapters that get the full list (prototype can populate `notes` for chapters
0-3 and leave `notes: []` for upcoming ones to exercise the empty state).

Prev/next context is computed in the view from `CHAPTERS` order:
`prev_chapter` / `next_chapter` (either may be `None` at the ends).

## 3. Interface design

Single column, Reader width tier (`max-w-3xl`) — the page is a notes feed plus
one composer, the same shape as `reader_profile.html`, not a
media-plus-sidebar detail page. Frame: `mx-auto max-w-3xl px-4 py-12 sm:px-6 lg:px-8 lg:py-16`
inside the standard `hero-gradient` section, `pt-24`, header/footer includes,
`_proto_banner.html` at the end.

One deliberate correction to the existing prototype pages: repeated cards on
this new page use `rounded-lg` per the design-system role contract
("radius drift is role drift"), not the `rounded-2xl` the older bookclub
templates hand-rolled. New surface, contract-correct classes.

Sections top to bottom:

### 3.1 Back link

`&larr; {{ book.title }}` to `bookclub_book_detail` — same accent
arrow-left link pattern the other bookclub pages use
(`inline-flex items-center gap-1 text-sm text-accent hover:underline`).
Identical for member and guest.

### 3.2 Chapter header

- Eyebrow row: `Book Club · Week 3` caption (`text-sm uppercase tracking-wide text-muted-foreground`)
  plus the badge cluster, all owned by `member_badges`:
  - Timeline/read badge via `{% member_status_badge %}`, tones from the
    design-system semantics table:
    read: `{% member_status_badge "Read" status="active" icon="check" %}`
    (green — success semantic); `current` + not read:
    `{% member_status_badge "Reading now" status="active" %}` (green,
    live-positive, matches the index page's `In progress`); `upcoming`:
    `{% member_status_badge "Upcoming" status="upcoming" %}` (blue);
    `done` + not read (viewer behind schedule):
    `{% member_status_badge "Past deadline" status="past" %}` (neutral/muted —
    never red; missing a book-club deadline is not an error).
  - Access badge via `{% member_access_badge book.required_level %}` — shown to
    guests only (members are already inside; badge is the above-the-fold gate
    signal the design system requires on gated detail surfaces).
- H1: `Ch. 3 — Hardware`, compact page h1 scale
  (`text-2xl font-semibold tracking-tight sm:text-3xl`).
- Meta row (`mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-muted-foreground`):
  `calendar-clock` Due Sep 7 (deadline uses the member compact date vocabulary,
  `member_compact_date` once real dates exist) · `users` 15 read ·
  `message-square` 11 notes. Same icon-meta pattern as the roadmap rows.

Identical for member and guest except the badge cluster (guest adds the access
badge; guest never sees the viewer-read badge, only the timeline badge).

### 3.3 Guest gate (guest only)

Immediately below the header, guests get
`templates/content/_gated_access_card.html` with the documented gate context
(`required_tier_name` = Main, vocabulary `Main or above required`). This is the
single gate for the page; sections 3.4-3.8 do not render for guests at all.
No hand-rolled join box — the book_detail guest aside predates the shared
gated card and this page should not copy it.

After the gate card, guests still get section 3.9 (prev/next navigation), so
they can browse the chapter list.

### 3.4 Mark as read (member only)

A one-line action row, not a card, sitting under the header:

- Not read, `current` chapter: `{% button_classes 'primary' %}` `Mark as read`
  (md size — this is the page-level CTA here, unlike the compact roadmap rows).
- Not read, `upcoming` or `done` chapter: `{% button_classes 'secondary' %}`
  `Mark as read`.
- Read: green success indicator, same treatment book_detail already uses
  (`check-circle-2` + `Marked read`, `text-emerald-400` scale), plus a
  small secondary `Mark unread` text link for reversibility.

### 3.5 Your note (member only)

This is the CTA-box role: callout/action card, inline action-row layout
(`rounded-lg border border-accent/40 bg-accent/5 p-6`), because it is the one
action the page flow asks of the member.

- Note empty: heading `Your note` (large card title `text-lg font-semibold`),
  supporting line `What stuck with you from this chapter? Everyone reading
  along will see it.`, a full-width `<textarea>` (form-control chrome:
  `rounded-md border border-input bg-background px-4 py-2.5 text-sm ...
  focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent`),
  and a `{% button_classes 'primary' %}` `Post note` button below,
  left-aligned. The button is the sanctioned CTA-box action — never a
  hand-rolled link.
- Note written: the card drops the accent surface and becomes an info/static
  card (`{% include 'content/_info_card_classes.html' %} bg-card`) showing the
  saved note body with an `Edit note` `{% button_classes 'secondary' size='sm' %}`
  action; clicking Edit swaps back to the composer prefilled (prototype:
  render the composer variant behind a `?edit=1` state or just show both
  variants on different chapters — chapters 0-2 have `your_note`, chapter 3
  does not, which already exercises both).

The viewer's own note also appears in the group feed (`you: True`) pinned
first, per the design-system rule that the signed-in member's own record leads.

### 3.6 Chapter summary (once published)

Only for chapters with `summary != None`; members only.

Callout/action card, accent variant (`border-accent/30 bg-accent/5`, `p-6`):
`sparkles` icon + `Chapter summary` title, the `teaser` paragraph, published
date caption, and the `body` in `.prose` below (or a `Read the summary` link
if summaries later get their own page — for the prototype, inline body,
collapsed with `templates/includes/_accordion.html` if long).

Unpublished + `done` chapter: a single caption line under the notes heading —
`Summary is compiled from everyone's notes after the deadline.` No card, no
fake empty state (absence of one optional field is not a collection empty
state).

### 3.7 Notes from the group (member only)

Section heading row: `Notes` (`text-lg font-semibold tracking-tight`) with a
count caption on the right (`11 notes`).

- Zero notes: `{% member_empty_state title='No notes yet'
  body='Be the first — post what stuck with you from this chapter.'
  icon='message-square' kind='fresh' %}`. No CTA inside the empty state —
  the composer is directly above it.
- One card per note, the `reader_profile.html` note-card pattern promoted to
  contract-correct classes: info/static card
  (`{% include 'content/_info_card_classes.html' %} bg-card`), containing:
  - Author row: initial avatar disc, author name linking to
    `bookclub_reader_profile` (accent hover underline), `posted` caption
    right-aligned. The viewer's own note gets a muted `You` static tag chip.
  - Note body: `text-sm leading-relaxed text-foreground/90`.
  - Reaction row: heart + likes count, `message-square` + comment count
    (identical to reader_profile).
  - Comments: `border-t border-border pt-4` block, avatar + name + body rows
    (identical to reader_profile).
  - Comment composer: inline input + `{% button_classes 'secondary' size='sm' %}`
    `Post` (identical to reader_profile's member state).

These are static cards with explicit inner actions, not clickable cards: no
`group`, no hover border, no arrow — the card itself navigates nowhere.

### 3.8 Prev / next chapter navigation

Bottom of the page, both member and guest. Two `templates/includes/_list_row.html`
rows (the sanctioned owner for numbered navigation rows), stacked:

- Previous: `href` to chapter N-1, `title="Ch. 2 — Architecture"`,
  `marker_kind="circle"`; omitted on the first chapter.
- Next: `href` to chapter N+1, `title="Ch. 4 — Software"`; omitted on the last
  chapter, where a single caption `Last chapter — the full book summary lands
  at the end` renders instead.

Rows carry the standard `min-h-[44px]` interactive-row target and
`aria-label` direction context (`Previous chapter`, `Next chapter` eyebrow
captions above each row).

### 3.9 Relation to `book_detail`

The chapter page becomes the home of notes; the roadmap becomes navigation.
Inline notes on `book_detail` are removed, not duplicated — one authoritative
surface per behavior.

## 4. Changes to the `book_detail` roadmap

Each roadmap `<li>` links to its chapter page. Minimal, mechanical changes:

- Each roadmap row becomes a whole-row anchor to
  `{% url 'bookclub_chapter_detail' slug=book.slug number=ch.number %}`. A
  whole-card anchor is only sanctioned when the card has no competing inner
  interactive elements, so this depends on the next bullet stripping the inner
  actions out first.
- Remove from the row: the `Your note` inset box, `Edit note`, `Add note`, and
  the `Chapter summary` link — all now live on the chapter page. This clears
  the row of inner interactive elements.
- Keep on the row: status marker disc, week caption, status badge, title,
  deadline / readers / notes meta row. Keep the single `Mark as read` quick
  action only on the `current` chapter (one primary action on the whole
  roadmap); all other rows have zero inner buttons and become whole-row
  anchors via `templates/content/_clickable_card_classes.html` + `rounded-lg`,
  gaining the canonical top-right translating arrow so the affordance is the
  sanctioned one, not a bare hover.
- Guest rows: drop the per-row `Join to mark read and take notes` lock line;
  rows are plain links (guests can open the chapter page and meet the single
  gate card there).
- The sidebar `Summaries` card links each published summary to
  `/books/<slug>/chapters/<n>` (anchor `#summary` optional) instead of `#`.

Net effect: `book_detail` gets lighter (roadmap is an index), the chapter page
is the participation surface.

## 5. Prototype build notes (non-normative)

- New template `templates/bookclub/chapter_detail.html`; new view
  `chapter_detail(request, slug, number)`; one new `path()` in
  `bookclub/urls.py` before nothing in particular (no conflicts).
- Populate `notes` for chapters 0-3 (reusing PUBLIC_PROFILE-style voices),
  `notes: []` for 4-7 to show the empty state; `summary` on chapters 0-1 to
  match the sidebar's published `Ch. 1 / Ch. 2` summaries (adjust one of the
  two lists so they agree).
- States are all reachable via URL alone: `?view=guest`, chapter 0 (read +
  note + summary), chapter 3 (current, unread, no note, notes present),
  chapter 7 (upcoming, zero notes).
