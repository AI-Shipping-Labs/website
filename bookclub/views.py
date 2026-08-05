"""Book Club prototype views.

Plain function views rendering hardcoded context. No auth, no models, no
writes — this is a clickable UI prototype only. See __init__.py.
"""

from django.http import Http404
from django.shortcuts import render

from . import prototype_data as data


def _base_context():
    return {
        "prototype": True,
        "book": data.BOOK,
        "chapters": data.CHAPTERS,
        "viewer_done": data.VIEWER_DONE,
        "viewer_total": data.VIEWER_TOTAL,
        "viewer_pct": data.VIEWER_PCT,
    }


def index(request):
    """Book Club landing — intro + the active book."""
    return render(request, "bookclub/index.html", _base_context())


def book_detail(request, slug):
    if slug != data.BOOK["slug"]:
        raise Http404("Unknown book (prototype only ships one).")
    ctx = _base_context()
    ctx["leaderboard_top"] = data.LEADERBOARD[:5]
    return render(request, "bookclub/book_detail.html", ctx)


def leaderboard(request, slug):
    if slug != data.BOOK["slug"]:
        raise Http404("Unknown book (prototype only ships one).")
    ctx = _base_context()
    ctx["leaderboard"] = data.LEADERBOARD
    return render(request, "bookclub/leaderboard.html", ctx)


def reader_profile(request, slug, handle):
    if slug != data.BOOK["slug"]:
        raise Http404("Unknown book (prototype only ships one).")
    ctx = _base_context()
    ctx["profile"] = data.PUBLIC_PROFILE
    return render(request, "bookclub/reader_profile.html", ctx)
