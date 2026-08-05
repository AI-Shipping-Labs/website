"""Hardcoded sample data for the Book Club UI prototype.

Everything here is fake and static. It exists only so the prototype templates
render with realistic shapes (chapters, deadlines, leaderboard, notes) so we
can judge the UX. No database, no real users.
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
    "meeting_cadence": "Weekly · 17:00 CET · Zoom",
    "start_label": "Kickoff Aug 10, 2026",
    "links": [
        {"label": "Book page (Baseten)", "url": "https://www.baseten.co/inference-engineering/", "icon": "book-open"},
        {"label": "Kickoff event", "url": "/events/inference-engineering-book-club-kickoff", "icon": "calendar"},
        {"label": "#book-club on Slack", "url": "#", "icon": "message-circle"},
        {"label": "Zoom room", "url": "#", "icon": "video"},
    ],
    # cohort-wide progress used for the summary strip
    "readers_count": 34,
    "chapters_count": 8,
}

# --- Chapter roadmap -----------------------------------------------------
# status is from the *current viewer's* perspective in the prototype.

# Chapters are the real table of contents of "Inference Engineering" (Ch 0–7,
# from the Baseten book page linked in the kickoff event). Deadlines are
# illustrative — the actual cadence is decided together on the kickoff call.
CHAPTERS = [
    {
        "number": 0,
        "title": "Inference",
        "deadline": "Aug 17",
        "week": "Week 1",
        "status": "done",
        "readers_done": 32,
        "notes_count": 24,
        "your_note": (
            "Good framing of what 'inference' actually means as a systems "
            "problem, not a model problem. Sets up the whole rest of the book."
        ),
    },
    {
        "number": 1,
        "title": "Prerequisites",
        "deadline": "Aug 24",
        "week": "Week 2",
        "status": "done",
        "readers_done": 29,
        "notes_count": 19,
        "your_note": (
            "Solid refresher on the terms and math the later chapters lean on. "
            "The tokens/latency/throughput vocabulary is worth memorizing early."
        ),
    },
    {
        "number": 2,
        "title": "Architecture",
        "deadline": "Aug 31",
        "week": "Week 3",
        "status": "done",
        "readers_done": 24,
        "notes_count": 16,
        "your_note": (
            "The KV-cache walkthrough finally made attention memory cost click "
            "for me. Screenshotted the diagram of how sequence length blows up "
            "memory."
        ),
    },
    {
        "number": 3,
        "title": "Hardware",
        "deadline": "Sep 7",
        "week": "Week 4",
        "status": "reading",
        "readers_done": 15,
        "notes_count": 11,
        "your_note": "",
    },
    {
        "number": 4,
        "title": "Software",
        "deadline": "Sep 14",
        "week": "Week 5",
        "status": "upcoming",
        "readers_done": 6,
        "notes_count": 4,
        "your_note": "",
    },
    {
        "number": 5,
        "title": "Techniques",
        "deadline": "Sep 21",
        "week": "Week 6",
        "status": "upcoming",
        "readers_done": 3,
        "notes_count": 2,
        "your_note": "",
    },
    {
        "number": 6,
        "title": "Modalities",
        "deadline": "Sep 28",
        "week": "Week 7",
        "status": "upcoming",
        "readers_done": 2,
        "notes_count": 1,
        "your_note": "",
    },
    {
        "number": 7,
        "title": "Production",
        "deadline": "Oct 5",
        "week": "Week 8",
        "status": "upcoming",
        "readers_done": 1,
        "notes_count": 0,
        "your_note": "",
    },
]


def _progress():
    done = sum(1 for c in CHAPTERS if c["status"] == "done")
    total = len(CHAPTERS)
    pct = round(done / total * 100)
    return done, total, pct


VIEWER_DONE, VIEWER_TOTAL, VIEWER_PCT = _progress()

# --- Leaderboard ---------------------------------------------------------

LEADERBOARD = [
    {"rank": 1, "name": "Priya Nair", "handle": "priya", "chapters": 8,
     "notes": 8, "streak": 3, "public": True, "you": False},
    {"rank": 2, "name": "Marco Silva", "handle": "marco", "chapters": 7,
     "notes": 6, "streak": 3, "public": True, "you": False},
    {"rank": 3, "name": "Aisha Khan", "handle": "aisha", "chapters": 6,
     "notes": 7, "streak": 2, "public": True, "you": False},
    {"rank": 4, "name": "You", "handle": "you", "chapters": 3,
     "notes": 2, "streak": 2, "public": True, "you": True},
    {"rank": 5, "name": "Tomás Ruiz", "handle": "tomas", "chapters": 2,
     "notes": 1, "streak": 1, "public": True, "you": False},
    {"rank": 6, "name": "Lena Fischer", "handle": "lena", "chapters": 2,
     "notes": 3, "streak": 1, "public": False, "you": False},
    {"rank": 7, "name": "Sam O'Brien", "handle": "sam", "chapters": 1,
     "notes": 0, "streak": 1, "public": True, "you": False},
]

# --- A member's public reading profile (notes + comments) ----------------

PUBLIC_PROFILE = {
    "name": "Priya Nair",
    "handle": "priya",
    "tagline": "ML engineer · serving models in prod, reading to cut latency",
    "chapters_read": 8,
    "notes_written": 8,
    "streak": 3,
    "notes": [
        {
            "chapter": 3,
            "chapter_title": "Hardware",
            "posted": "2 days ago",
            "body": (
                "The section on memory bandwidth vs compute finally made me "
                "understand why our workload is memory-bound, not compute-bound. "
                "Key takeaway: batch size is the lever, and we were leaving it "
                "at 1. Bumped it and saw throughput jump without touching the "
                "model."
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
                "The KV-cache explanation is the cleanest I've seen. The point "
                "that generation is sequential and can't be parallelized the way "
                "prefill can reframed how I think about our tail latency. "
                "Continuous batching suddenly makes sense as the fix."
            ),
            "likes": 9,
            "comments": [
                {"name": "Aisha Khan", "body": "This chapter made me stop "
                 "blaming the model for slow responses — it's the decode loop."},
            ],
        },
    ],
}
