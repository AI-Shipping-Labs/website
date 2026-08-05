"""Book Club prototype views.

Plain function views rendering hardcoded context. No auth, no models, no
writes — this is a clickable UI prototype only. See __init__.py.
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


def _leaderboard_for(is_member):
    """Full board for members; guests don't have a 'You' row (and it's
    renumbered so there's no gap)."""
    if is_member:
        return data.LEADERBOARD
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
    }


def index(request):
    """Book Club landing — intro + the active book + past/upcoming."""
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
        return render(request, "bookclub/book_secondary.html", ctx)
    ctx = _base_context(request)
    ctx["current_chapter"] = next(
        (c for c in data.CHAPTERS if c["status"] == "reading"), None
    )
    return render(request, "bookclub/book_detail.html", ctx)


def progress(request, slug):
    """Progress board (formerly 'leaderboard')."""
    book = _book_for(slug)
    if book is None:
        raise Http404("Unknown book.")
    ctx = _base_context(request)
    ctx["book"] = book
    ctx["leaderboard"] = _leaderboard_for(ctx["is_member"])
    return render(request, "bookclub/leaderboard.html", ctx)


def chapter_detail(request, slug, number):
    """A single chapter's page: notes, mark-as-read, summary, prev/next."""
    if slug != data.BOOK["slug"]:
        raise Http404("Chapters exist only for the active book in this prototype.")
    ch = data.get_chapter(number)
    if ch is None:
        raise Http404("No such chapter.")
    ctx = _base_context(request)
    ctx["chapter"] = ch
    ctx["prev_number"] = number - 1 if data.get_chapter(number - 1) else None
    ctx["next_number"] = number + 1 if data.get_chapter(number + 1) else None
    ctx["chapter_notes"] = data.CHAPTER_SAMPLE_NOTES if ch["notes_count"] else []
    return render(request, "bookclub/book_chapter.html", ctx)


def reader_profile(request, slug, handle):
    book = _book_for(slug)
    if book is None:
        raise Http404("Unknown book.")
    ctx = _base_context(request)
    ctx["book"] = book
    ctx["profile"] = data.PUBLIC_PROFILE
    return render(request, "bookclub/reader_profile.html", ctx)


def book_summary(request, slug):
    """Compiled book summary (prototype placeholder text)."""
    book = _book_for(slug)
    if book is None:
        raise Http404("Unknown book.")
    ctx = _base_context(request)
    ctx["book"] = book
    return render(request, "bookclub/book_summary.html", ctx)
