"""Books (book club) prototype views.

Plain function views rendering hardcoded context. No auth, no models, no
writes — this is a clickable UI prototype only. See __init__.py.

Interactive states (mark-as-read, edit note, notify-me) are demonstrated with
GET-state query params (``?read=1`` / ``?read=0`` / ``?edit=1`` / ``?notify=1``)
that override the rendered state, so every control does something visible
without a database.
"""

from django.http import Http404
from django.shortcuts import render

from . import prototype_data as data


def _resolve_is_member(request):
    """Whether to render the authenticated (member) view.

    Defaults to the real auth state, but a ``?view=member`` / ``?view=guest``
    override lets us preview both states without actually logging in — the
    prototype has no real auth.
    """
    override = request.GET.get("view")
    if override == "member":
        return True
    if override == "guest":
        return False
    return request.user.is_authenticated


def _progress_rows_for(is_member):
    """Rows for the progress board.

    Members see their own row first (design-system own-record-first rule) with
    its real rank, then everyone else in rank order. Guests have no 'You' row,
    so it is dropped and the ranks are renumbered to close the gap.
    """
    if is_member:
        you = [r for r in data.LEADERBOARD if r.get("you")]
        others = [r for r in data.LEADERBOARD if not r.get("you")]
        return you + others
    rows = [r for r in data.LEADERBOARD if not r.get("you")]
    return [{**r, "rank": i + 1} for i, r in enumerate(rows)]


def _book_for(slug):
    """A book-like dict for any known slug (active or secondary), else None."""
    if slug == data.BOOK["slug"]:
        return data.BOOK
    sec = data.get_secondary_book(slug)
    if sec is None:
        return None
    return {
        "slug": sec["slug"],
        "title": sec["title"],
        "chapters_count": sec.get("chapters", 0),
        "is_secondary": True,
        "is_finished": sec.get("status") == "finished",
    }


def _base_context(request):
    is_member = _resolve_is_member(request)
    return {
        "prototype": True,
        "is_member": is_member,
        "book": data.BOOK,
        "chapters": data.CHAPTERS,
        "viewer_done": data.VIEWER_DONE,
        "viewer_total": data.VIEWER_TOTAL,
        "viewer_pct": data.VIEWER_PCT,
        "viewer_streak": data.VIEWER_STREAK,
        # Carry the ?view= override so in-page GET-state links keep the
        # member/guest preview they were opened in. ``view_query`` is the
        # ready-to-append query string ("?view=member" or "").
        "view_param": request.GET.get("view"),
        "view_query": f"?view={request.GET['view']}" if request.GET.get("view") in ("member", "guest") else "",
    }


def _gate(testid, icon, heading, description, value_items=None):
    """Context for the canonical ``_gated_access_card.html`` partial.

    Access decisions live in the view (design-system rule); the partial only
    standardizes the visible hierarchy. Every Books gate is Main-tier, so the
    pill reads "Main or above required".
    """
    return {
        "gated_card_testid": testid,
        "gated_icon": icon,
        "gated_heading": heading,
        "gated_description": description,
        "required_tier_name": "Main",
        "gated_value_items": value_items or [],
        # Canonical paid-tier gate CTA, matching the rest of the site
        # (content/access.py, content/views/pages.py): "/pricing" + "Upgrade".
        "gated_cta_url": "/pricing",
        "gated_cta_label": "Upgrade",
        "gated_cta_testid": testid + "-cta",
    }


def index(request):
    """Books hub — intro + the active book + past/upcoming."""
    ctx = _base_context(request)
    ctx["upcoming_books"] = data.UPCOMING_BOOKS
    ctx["past_books"] = data.PAST_BOOKS
    return render(request, "bookclub/index.html", ctx)


def book_detail(request, slug):
    # The active book gets the full experience; past/upcoming books get a
    # lighter detail page reflecting their lifecycle state.
    if slug != data.BOOK["slug"]:
        secondary = data.get_secondary_book(slug)
        if secondary is None:
            raise Http404("Unknown book.")
        ctx = _base_context(request)
        ctx["secondary"] = secondary
        # Upcoming "Get notified" confirmation is a free GET-state interaction.
        ctx["notified"] = request.GET.get("notify") == "1"
        return render(request, "bookclub/book_secondary.html", ctx)
    ctx = _base_context(request)
    ctx["current_chapter"] = next(
        (c for c in data.CHAPTERS if c["timeline"] == "current"), None
    )
    ctx["meetings"] = data.BOOK["meetings"]
    ctx.update(_gate(
        "book-guest-gate",
        "book-marked",
        "Read along with the community",
        "Follow the chapter roadmap, mark chapters read, share notes, and join "
        "the weekly discussions as the group reads this book together.",
        value_items=[
            {"icon": "check-check", "label": "Mark chapters read"},
            {"icon": "pen-line", "label": "Share notes"},
            {"icon": "users", "label": "Weekly discussions"},
        ],
    ))
    return render(request, "bookclub/book_detail.html", ctx)


def progress(request, slug):
    """Progress board (formerly 'leaderboard')."""
    book = _book_for(slug)
    if book is None:
        raise Http404("Unknown book.")
    ctx = _base_context(request)
    ctx["book"] = book
    ctx["progress_rows"] = _progress_rows_for(ctx["is_member"])
    ctx.update(_gate(
        "progress-guest-gate",
        "bar-chart-3",
        "Track your reading with the group",
        "Join to appear on the board, compare chapters read, and keep your "
        "reading streak going.",
    ))
    return render(request, "bookclub/progress.html", ctx)


def chapter_detail(request, slug, number):
    """A single chapter's page: notes, mark-as-read, summary, prev/next."""
    if slug != data.BOOK["slug"]:
        raise Http404("Chapters exist only for the active book in this prototype.")
    ch = data.get_chapter(number)
    if ch is None:
        raise Http404("No such chapter.")
    ctx = _base_context(request)

    # Viewer read state, overridable via ?read=1 / ?read=0 so the button works.
    viewer_read = ch["viewer_read"]
    read_override = request.GET.get("read")
    if read_override == "1":
        viewer_read = True
    elif read_override == "0":
        viewer_read = False

    ctx["chapter"] = ch
    ctx["viewer_read"] = viewer_read
    # ?edit=1 swaps a written note back to the composer.
    ctx["editing"] = request.GET.get("edit") == "1"
    prev_chapter = data.get_chapter(number - 1)
    next_chapter = data.get_chapter(number + 1)
    ctx["prev_chapter"] = prev_chapter
    ctx["next_chapter"] = next_chapter
    if prev_chapter:
        ctx["prev_label"] = f"Ch. {prev_chapter['number']} — {prev_chapter['title']}"
    if next_chapter:
        ctx["next_label"] = f"Ch. {next_chapter['number']} — {next_chapter['title']}"
    # Own note pinned first (design-system own-record-first rule).
    ctx["chapter_notes"] = sorted(ch["notes"], key=lambda n: not n.get("you"))
    ctx.update(_gate(
        "chapter-guest-gate",
        "book-marked",
        "Join to read along",
        "Mark this chapter read, write your own note, and read and comment on "
        "everyone else's as the group works through the book.",
    ))
    return render(request, "bookclub/book_chapter.html", ctx)


def reader_profile(request, slug, handle):
    book = _book_for(slug)
    if book is None:
        raise Http404("Unknown book.")
    profile = data.get_profile(handle)
    if profile is None:
        raise Http404("This reader's profile is private or does not exist.")
    ctx = _base_context(request)
    ctx["book"] = book
    ctx["profile"] = profile
    ctx.update(_gate(
        "reader-guest-gate",
        "message-square",
        "Join the conversation",
        "Comment on notes and share your own as you read along.",
    ))
    return render(request, "bookclub/reader_profile.html", ctx)


def book_summary(request, slug):
    """Compiled book summary — an index of published chapter summaries while
    the book is in progress, or the full compiled body once finished."""
    book = _book_for(slug)
    if book is None:
        raise Http404("Unknown book.")
    ctx = _base_context(request)
    ctx["book"] = book
    is_finished = book.get("is_finished", False)
    ctx["is_finished"] = is_finished
    # Active book: list only the chapters whose summary has been published.
    if is_finished:
        ctx["published_summaries"] = []
    else:
        ctx["published_summaries"] = [
            {
                "number": c["number"],
                "summary_row_label": (
                    f"Ch. {c['number']} — {c['title']} · "
                    f"Published {c['summary']['published']}"
                ),
            }
            for c in data.CHAPTERS
            if c.get("summary")
        ]
    ctx.update(_gate(
        "summary-guest-gate",
        "sparkles",
        "Read the group's summary",
        "The compiled summary is drawn from everyone's chapter notes. Join to "
        "read it in full.",
    ))
    return render(request, "bookclub/book_summary.html", ctx)
