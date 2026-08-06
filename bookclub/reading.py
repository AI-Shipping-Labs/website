"""Derived reading progress for the current viewer (issue #1364).

Progress is never stored — it is computed from the presence of
``ChapterRead`` rows. The read set for a viewer over a whole book is fetched
in a single query (``chapter__book=book``), so the roadmap can mark every
chapter row without an N+1 over chapters.
"""

from __future__ import annotations

from bookclub.models import ChapterRead


def viewer_read_numbers(user, book):
    """Return the set of chapter numbers this viewer has marked read.

    One query. Anonymous / gated viewers get an empty set (they never see
    the control anyway).
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    return set(
        ChapterRead.objects.filter(user=user, chapter__book=book)
        .values_list('chapter__number', flat=True)
    )


def viewer_reading_progress(user, book, chapters=None):
    """Compute the viewer's derived progress over ``book``'s chapters.

    Returns a dict with ``total``, ``done``, ``pct`` and the ``read_numbers``
    set so the roadmap can mark each row. Uses one query for the read set.
    """
    if chapters is None:
        chapters = list(book.chapters.all())
    total = len(chapters)
    read_numbers = viewer_read_numbers(user, book)
    done = sum(1 for chapter in chapters if chapter.number in read_numbers)
    pct = round(done / total * 100) if total else 0
    return {
        'total': total,
        'done': done,
        'pct': pct,
        'read_numbers': read_numbers,
    }
