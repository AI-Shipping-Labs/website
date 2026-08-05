"""Hardcoded sample data for the Books (book club) UI prototype.

Everything here is fake and static. It exists only so the prototype templates
render with realistic shapes (chapters, deadlines, progress, notes) so we can
judge the UX. No database, no real users.
"""

# --- The book being read this cohort ------------------------------------

BOOK = {
    "slug": "inference-engineering",
    "title": "Inference Engineering",
    "subtitle": "The technologies behind every AI product in production",
    "author": "Philip Kiely",
    # Standard tier access, same as other content. Rendered via the design
    # system's {% member_access_badge %}. 20 = Main (LEVEL_MAIN); the exact
    # tier is a grooming decision.
    "required_level": 20,
    "cover_image": "bookclub/inference-engineering.png",  # real cover (Baseten)
    "cover_accent": "from-accent/30",
    "description": (
        "A book for engineers who want to understand the technologies behind "
        "every AI product in production — the stack from model architecture and "
        "hardware to serving software, optimization techniques, and running "
        "inference in production."
    ),
    "meeting_cadence": "Weekly · 17:00 CET",
    "start_label": "Kickoff Aug 10, 2026",
    "links": [
        {"label": "Book page (Baseten)", "url": "https://www.baseten.co/inference-engineering/", "icon": "book-open"},
        {"label": "Kickoff event", "url": "/events/inference-engineering-book-club-kickoff", "icon": "calendar"},
        # Slack joining lives on /account (no dead "#" anchor in the prototype).
        {"label": "#book-club on Slack", "url": "/account", "icon": "message-circle"},
    ],
    # Weekly discussion occurrences from the linked event series. In production
    # each occurrence links its own event-detail page; the prototype points at
    # /events (always resolves locally).
    "meetings": [
        {"title": "Ch. 3 discussion", "when": "Thu Aug 28 · 18:00 CET", "event_url": "/events", "next": True},
        {"title": "Ch. 4 discussion", "when": "Thu Sep 4 · 18:00 CET", "event_url": "/events", "next": False},
    ],
    # cohort-wide progress used for the summary strip
    "readers_count": 34,
    "chapters_count": 8,
}

# The current viewer's reading streak (weeks). Rendered in the progress
# callout; kept out of the template so the copy has a single source.
VIEWER_STREAK = 2

# --- Chapter roadmap -----------------------------------------------------
# Two independent axes (chapter-page spec section 2):
#   timeline: cohort/deadline axis — done | current | upcoming
#   viewer_read: the current viewer's own read state

# Chapters are the real table of contents of "Inference Engineering" (Ch 0–7,
# from the Baseten book page linked in the kickoff event). Deadlines are
# illustrative — the actual cadence is decided together on the kickoff call.
CHAPTERS = [
    {
        "number": 0,
        "title": "Inference",
        "deadline": "Aug 17",
        "week": "Week 1",
        "timeline": "done",
        "viewer_read": True,
        "readers_done": 32,
        "your_note": (
            "Good framing of what 'inference' actually means as a systems "
            "problem, not a model problem. Sets up the whole rest of the book."
        ),
        "summary": {
            "published": "Aug 20",
            "teaser": (
                "The group's biggest takeaway: inference is a systems problem "
                "long before it is a modeling one."
            ),
            "body": (
                "Across everyone's notes, the recurring theme was that "
                "'inference' names the whole serving system, not the model "
                "call. The chapter's framing of latency, throughput, and cost "
                "as the three levers set up the vocabulary the rest of the book "
                "leans on, and several readers flagged the cost-per-token lens "
                "as the one that changed how they think about their own stack."
            ),
        },
        "notes": [
            {
                "name": "You",
                "handle": "you",
                "posted": "5 days ago",
                "you": True,
                "body": (
                    "Good framing of what 'inference' actually means as a "
                    "systems problem, not a model problem. Sets up the whole "
                    "rest of the book."
                ),
                "likes": 4,
                "comments": [
                    {"name": "Priya Nair", "body": "Exactly — the systems lens is the whole point."},
                ],
            },
            {
                "name": "Priya Nair",
                "handle": "priya",
                "posted": "2 days ago",
                "you": False,
                "body": (
                    "The part I keep coming back to is how much of this is a "
                    "systems problem, not a modeling one. Wrote up a small "
                    "example from our own stack that made it click for the team."
                ),
                "likes": 8,
                "comments": [
                    {"name": "Marco Silva", "body": "Same — going to try this on our service this week."},
                ],
            },
            {
                "name": "Aisha Khan",
                "handle": "aisha",
                "posted": "4 days ago",
                "you": False,
                "body": (
                    "Good chapter to read slowly. The tradeoffs only landed on "
                    "the second pass. Bringing questions to Thursday's discussion."
                ),
                "likes": 5,
                "comments": [],
            },
        ],
    },
    {
        "number": 1,
        "title": "Prerequisites",
        "deadline": "Aug 24",
        "week": "Week 2",
        "timeline": "done",
        "viewer_read": True,
        "readers_done": 29,
        "your_note": (
            "Solid refresher on the terms and math the later chapters lean on. "
            "The tokens/latency/throughput vocabulary is worth memorizing early."
        ),
        "summary": {
            "published": "Aug 27",
            "teaser": (
                "A shared glossary chapter: the group agreed the "
                "tokens/latency/throughput vocabulary is worth memorizing early."
            ),
            "body": (
                "Readers treated this as the reference chapter. The notes "
                "converge on a small set of terms — tokens, latency, "
                "throughput, and batch size — that every later chapter reuses. "
                "A few people built quick flashcards; the consensus was that "
                "the time spent here pays off once the hardware and techniques "
                "chapters start stacking these concepts."
            ),
        },
        "notes": [
            {
                "name": "You",
                "handle": "you",
                "posted": "4 days ago",
                "you": True,
                "body": (
                    "Solid refresher on the terms and math the later chapters "
                    "lean on. The tokens/latency/throughput vocabulary is worth "
                    "memorizing early."
                ),
                "likes": 3,
                "comments": [],
            },
            {
                "name": "Priya Nair",
                "handle": "priya",
                "posted": "3 days ago",
                "you": False,
                "body": (
                    "Made a one-page glossary out of this chapter. Happy to "
                    "share it on Slack if anyone wants a head start."
                ),
                "likes": 6,
                "comments": [
                    {"name": "Tomás Ruiz", "body": "Yes please — that would help a lot."},
                ],
            },
            {
                "name": "Marco Silva",
                "handle": "marco",
                "posted": "3 days ago",
                "you": False,
                "body": (
                    "The math is lighter than I feared. The throughput vs "
                    "latency distinction is the one to internalize."
                ),
                "likes": 4,
                "comments": [],
            },
        ],
    },
    {
        "number": 2,
        "title": "Architecture",
        "deadline": "Aug 31",
        "week": "Week 3",
        "timeline": "done",
        "viewer_read": True,
        "readers_done": 24,
        "your_note": (
            "The KV-cache walkthrough finally made attention memory cost click "
            "for me. Screenshotted the diagram of how sequence length blows up "
            "memory."
        ),
        "summary": None,
        "notes": [
            {
                "name": "You",
                "handle": "you",
                "posted": "6 days ago",
                "you": True,
                "body": (
                    "The KV-cache walkthrough finally made attention memory "
                    "cost click for me. Screenshotted the diagram of how "
                    "sequence length blows up memory."
                ),
                "likes": 7,
                "comments": [
                    {"name": "Aisha Khan", "body": "That diagram is the best in the book so far."},
                ],
            },
            {
                "name": "Aisha Khan",
                "handle": "aisha",
                "posted": "5 days ago",
                "you": False,
                "body": (
                    "This chapter made me stop blaming the model for slow "
                    "responses — it's the decode loop. Continuous batching "
                    "suddenly makes sense as the fix."
                ),
                "likes": 5,
                "comments": [],
            },
        ],
    },
    {
        "number": 3,
        "title": "Hardware",
        "deadline": "Sep 7",
        "week": "Week 4",
        "timeline": "current",
        "viewer_read": False,
        "readers_done": 15,
        "your_note": "",
        "summary": None,
        "notes": [
            {
                "name": "Priya Nair",
                "handle": "priya",
                "posted": "2 days ago",
                "you": False,
                "body": (
                    "The section on memory bandwidth vs compute finally made me "
                    "understand why our workload is memory-bound. Batch size is "
                    "the lever, and we were leaving it at 1."
                ),
                "likes": 11,
                "comments": [
                    {"name": "Marco Silva", "body": "What batch size did you land on before latency started hurting?"},
                ],
            },
            {
                "name": "Aisha Khan",
                "handle": "aisha",
                "posted": "1 day ago",
                "you": False,
                "body": (
                    "Roofline chart is worth the price of the chapter. Going to "
                    "plot our own workload against it this week."
                ),
                "likes": 4,
                "comments": [],
            },
        ],
    },
    {
        "number": 4,
        "title": "Software",
        "deadline": "Sep 14",
        "week": "Week 5",
        "timeline": "upcoming",
        "viewer_read": False,
        "readers_done": 6,
        "your_note": "",
        "summary": None,
        "notes": [],
    },
    {
        "number": 5,
        "title": "Techniques",
        "deadline": "Sep 21",
        "week": "Week 6",
        "timeline": "upcoming",
        "viewer_read": False,
        "readers_done": 3,
        "your_note": "",
        "summary": None,
        "notes": [],
    },
    {
        "number": 6,
        "title": "Modalities",
        "deadline": "Sep 28",
        "week": "Week 7",
        "timeline": "upcoming",
        "viewer_read": False,
        "readers_done": 2,
        "your_note": "",
        "summary": None,
        "notes": [],
    },
    {
        "number": 7,
        "title": "Production",
        "deadline": "Oct 5",
        "week": "Week 8",
        "timeline": "upcoming",
        "viewer_read": False,
        "readers_done": 1,
        "your_note": "",
        "summary": None,
        "notes": [],
    },
]

# Derivable: the meta rows show len(notes) as the note count.
for _ch in CHAPTERS:
    _ch["notes_count"] = len(_ch["notes"])


# --- Other books in the library (index sections) -------------------------
# Placeholder covers are rendered as a tinted spine box (no image) since these
# are secondary cards. Titles/authors only — real covers land at build time.

UPCOMING_BOOKS = [
    {
        "slug": "designing-data-intensive-applications",
        "title": "Designing Data-Intensive Applications",
        "author": "Martin Kleppmann",
        "when_label": "Up next",
        "status": "upcoming",
        "chapters": 12,
        "start_label": "Starts after we finish Inference Engineering",
        "cover_accent": "from-blue-500/30",
        "description": (
            "The big ideas behind reliable, scalable, and maintainable data "
            "systems — storage engines, replication, partitioning, "
            "transactions, and the tradeoffs behind each."
        ),
    },
]

PAST_BOOKS = [
    {
        "slug": "designing-machine-learning-systems",
        "title": "Designing Machine Learning Systems",
        "author": "Chip Huyen",
        "when_label": "Finished Jun 2026",
        "status": "finished",
        "chapters": 11,
        "readers": 41,
        "summary_available": True,
        "cover_accent": "from-purple-500/30",
        "description": (
            "An iterative framework for designing ML systems that are reliable, "
            "scalable, and maintainable in production — from data to deployment "
            "to monitoring."
        ),
    },
    {
        "slug": "the-pragmatic-programmer",
        "title": "The Pragmatic Programmer",
        "author": "Hunt & Thomas",
        "when_label": "Finished Apr 2026",
        "status": "finished",
        "chapters": 9,
        "readers": 36,
        "summary_available": True,
        "cover_accent": "from-amber-500/30",
        "description": (
            "Timeless, practical advice on the craft of software — from personal "
            "responsibility and career development to the technical practices "
            "that keep code flexible and durable."
        ),
    },
]


def get_chapter(number):
    """Return the chapter dict for the active book by number, else None."""
    for c in CHAPTERS:
        if c["number"] == number:
            return c
    return None


def get_secondary_book(slug):
    """Look up a past/upcoming book by slug (None if not found)."""
    for b in UPCOMING_BOOKS + PAST_BOOKS:
        if b["slug"] == slug:
            return b
    return None


def _progress():
    done = sum(1 for c in CHAPTERS if c["viewer_read"])
    total = len(CHAPTERS)
    pct = round(done / total * 100)
    return done, total, pct


VIEWER_DONE, VIEWER_TOTAL, VIEWER_PCT = _progress()

# --- Progress board (formerly "leaderboard") -----------------------------

LEADERBOARD = [
    {"rank": 1, "name": "Priya Nair", "handle": "priya", "chapters": 8,
     "notes": 8, "streak": 3, "public": True, "you": False},
    {"rank": 2, "name": "Marco Silva", "handle": "marco", "chapters": 7,
     "notes": 6, "streak": 3, "public": True, "you": False},
    {"rank": 3, "name": "Aisha Khan", "handle": "aisha", "chapters": 6,
     "notes": 7, "streak": 2, "public": True, "you": False},
    {"rank": 4, "name": "You", "handle": "you", "chapters": 3,
     "notes": 3, "streak": 2, "public": True, "you": True},
    {"rank": 5, "name": "Tomás Ruiz", "handle": "tomas", "chapters": 2,
     "notes": 1, "streak": 1, "public": True, "you": False},
    {"rank": 6, "name": "Lena Fischer", "handle": "lena", "chapters": 2,
     "notes": 3, "streak": 1, "public": False, "you": False},
    {"rank": 7, "name": "Sam O'Brien", "handle": "sam", "chapters": 1,
     "notes": 0, "streak": 1, "public": True, "you": False},
]

# --- Members' public reading profiles (progress + notes) -----------------
# Keyed by handle. Private members (e.g. "lena") have no entry, so
# get_profile() returns None and the reader-profile view 404s.

PUBLIC_PROFILES = {
    "priya": {
        "name": "Priya Nair",
        "handle": "priya",
        "tagline": "ML engineer · serving models in prod, reading to cut latency",
        "chapters_read": 8,
        "notes_written": 8,
        "streak": 3,
        "read_chapters": [0, 1, 2, 3, 4, 5, 6, 7],
        "notes": [
            {
                "chapter": 3,
                "chapter_title": "Hardware",
                "posted": "2 days ago",
                "body": (
                    "The section on memory bandwidth vs compute finally made me "
                    "understand why our workload is memory-bound, not "
                    "compute-bound. Key takeaway: batch size is the lever, and "
                    "we were leaving it at 1. Bumped it and saw throughput jump "
                    "without touching the model."
                ),
                "likes": 12,
                "comments": [
                    {"name": "Marco Silva", "body": "Same realization here. What "
                     "batch size did you land on before latency started hurting?"},
                    {"name": "You", "body": "Curious how you measured memory-bound "
                     "vs compute-bound — did you use the roofline chart from the book?"},
                ],
            },
            {
                "chapter": 2,
                "chapter_title": "Architecture",
                "posted": "6 days ago",
                "body": (
                    "The KV-cache explanation is the cleanest I've seen. The "
                    "point that generation is sequential and can't be "
                    "parallelized the way prefill can reframed how I think about "
                    "our tail latency. Continuous batching suddenly makes sense "
                    "as the fix."
                ),
                "likes": 9,
                "comments": [
                    {"name": "Aisha Khan", "body": "This chapter made me stop "
                     "blaming the model for slow responses — it's the decode loop."},
                ],
            },
        ],
    },
    "marco": {
        "name": "Marco Silva",
        "handle": "marco",
        "tagline": "Backend engineer · shipping an LLM feature this quarter",
        "chapters_read": 7,
        "notes_written": 6,
        "streak": 3,
        "read_chapters": [0, 1, 2, 3, 4, 5, 6],
        "notes": [
            {
                "chapter": 1,
                "chapter_title": "Prerequisites",
                "posted": "3 days ago",
                "body": (
                    "The math is lighter than I feared. The throughput vs "
                    "latency distinction is the one to internalize before the "
                    "hardware chapter."
                ),
                "likes": 4,
                "comments": [],
            },
        ],
    },
    "aisha": {
        "name": "Aisha Khan",
        "handle": "aisha",
        "tagline": "Platform engineer · curious about serving internals",
        "chapters_read": 6,
        "notes_written": 7,
        "streak": 2,
        "read_chapters": [0, 1, 2, 3, 4, 5],
        "notes": [
            {
                "chapter": 3,
                "chapter_title": "Hardware",
                "posted": "1 day ago",
                "body": (
                    "Roofline chart is worth the price of the chapter. Going to "
                    "plot our own workload against it this week."
                ),
                "likes": 4,
                "comments": [],
            },
        ],
    },
    "you": {
        "name": "You",
        "handle": "you",
        "tagline": "Reading along with the group",
        "chapters_read": 3,
        "notes_written": 3,
        "streak": 2,
        "read_chapters": [0, 1, 2],
        "notes": [
            {
                "chapter": 2,
                "chapter_title": "Architecture",
                "posted": "6 days ago",
                "body": (
                    "The KV-cache walkthrough finally made attention memory "
                    "cost click for me. Screenshotted the diagram of how "
                    "sequence length blows up memory."
                ),
                "likes": 7,
                "comments": [
                    {"name": "Aisha Khan", "body": "That diagram is the best in the book so far."},
                ],
            },
            {
                "chapter": 1,
                "chapter_title": "Prerequisites",
                "posted": "4 days ago",
                "body": (
                    "Solid refresher on the terms and math the later chapters "
                    "lean on. The tokens/latency/throughput vocabulary is worth "
                    "memorizing early."
                ),
                "likes": 3,
                "comments": [],
            },
            {
                "chapter": 0,
                "chapter_title": "Inference",
                "posted": "5 days ago",
                "body": (
                    "Good framing of what 'inference' actually means as a "
                    "systems problem, not a model problem. Sets up the whole "
                    "rest of the book."
                ),
                "likes": 4,
                "comments": [
                    {"name": "Priya Nair", "body": "Exactly — the systems lens is the whole point."},
                ],
            },
        ],
    },
    "tomas": {
        "name": "Tomás Ruiz",
        "handle": "tomas",
        "tagline": "Data engineer · new to inference, learning fast",
        "chapters_read": 2,
        "notes_written": 1,
        "streak": 1,
        "read_chapters": [0, 1],
        "notes": [
            {
                "chapter": 0,
                "chapter_title": "Inference",
                "posted": "6 days ago",
                "body": (
                    "First time thinking about serving as its own discipline. "
                    "The cost-per-token framing is going to stick with me."
                ),
                "likes": 2,
                "comments": [],
            },
        ],
    },
    "sam": {
        "name": "Sam O'Brien",
        "handle": "sam",
        "tagline": "Full-stack dev · reading to keep up with the team",
        "chapters_read": 1,
        "notes_written": 0,
        "streak": 1,
        "read_chapters": [0],
        "notes": [],
    },
}

# Back-compat alias until every caller migrates to get_profile().
PUBLIC_PROFILE = PUBLIC_PROFILES["priya"]


def get_profile(handle):
    """Return a public reading profile by handle, or None if the member is
    private / unknown (the view raises 404 for None)."""
    return PUBLIC_PROFILES.get(handle)
