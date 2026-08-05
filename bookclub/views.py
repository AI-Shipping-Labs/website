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
    ctx["leaderboard_top"] = _leaderboard_for(ctx["is_member"])[:5]
    return render(request, "bookclub/book_detail.html", ctx)


def leaderboard(request, slug):
    if slug != data.BOOK["slug"]:
        raise Http404("Unknown book (prototype only ships one).")
    ctx = _base_context(request)
    ctx["leaderboard"] = _leaderboard_for(ctx["is_member"])
    return render(request, "bookclub/leaderboard.html", ctx)


def reader_profile(request, slug, handle):
    if slug != data.BOOK["slug"]:
        raise Http404("Unknown book (prototype only ships one).")
    ctx = _base_context(request)
    ctx["profile"] = data.PUBLIC_PROFILE
    return render(request, "bookclub/reader_profile.html", ctx)
