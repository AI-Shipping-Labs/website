"""Derived reading progress for the current viewer (issue #1364).

Progress is never stored — it is computed from the presence of
``ChapterRead`` rows. The read set for a viewer over a whole book is fetched
in a single query (``chapter__book=book``), so the roadmap can mark every
chapter row without an N+1 over chapters.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Count

from accounts.utils.display import display_name as _display_name
from bookclub.models import ChapterRead, Note
from bookclub.profiles import public_note_author_ids


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


def build_reader_rows(viewer, book, *, include_self):
    """Build the you-first Progress board roster for ``book`` (issue #1367).

    Mirrors ``plans.cohort_rows.build_progress_rows``: the view stays thin and
    the ordering / row shape is unit-testable here. The roster is derived from
    ``ChapterRead`` rows (there is no book "enrollment" model): every distinct
    user with at least one read on this book's chapters, plus the viewing
    member themselves (you-first) when ``include_self`` — even at ``0 of N``.

    Query budget is fixed, independent of roster size (no N+1): one aggregate
    for read counts, one for note counts, one bulk fetch of the ``User`` rows,
    one ``public_note_author_ids`` lookup. Returns ``(rows,
    distinct_readers)`` where ``distinct_readers`` counts every reader with
    >= 1 read. Notes visibility never removes a named roster row (#1457).
    """
    total = book.chapters.count()

    read_counts = {
        row['user']: row['chapters_read']
        for row in (
            ChapterRead.objects.filter(chapter__book=book)
            .values('user')
            .annotate(chapters_read=Count('chapter', distinct=True))
        )
    }
    note_counts = {
        row['user']: row['notes_shared']
        for row in (
            Note.objects.filter(chapter__book=book)
            .values('user')
            .annotate(notes_shared=Count('id'))
        )
    }
    distinct_readers = len(read_counts)

    viewer_id = viewer.pk if include_self and getattr(
        viewer, 'is_authenticated', False,
    ) else None

    reader_ids = set(read_counts)
    if viewer_id is not None:
        reader_ids.add(viewer_id)

    members = {
        u.pk: u
        for u in get_user_model().objects.filter(pk__in=reader_ids)
    }
    # #1457: one query marks which readers' notes are public. Visibility only
    # explains the Notes cell; every reader remains named on the board.
    public_note_ids = public_note_author_ids(reader_ids)

    rows = []
    for uid, member in members.items():
        is_self = uid == viewer_id
        notes_private = uid not in public_note_ids
        chapters_read = read_counts.get(uid, 0)
        pct = round(chapters_read / total * 100) if total else 0
        rows.append({
            'member': member,
            'chapters_read': chapters_read,
            'notes_shared': note_counts.get(uid, 0),
            'total': total,
            'pct': pct,
            'is_self': is_self,
            'notes_private': notes_private,
        })

    # You-first, then chapters read desc, notes shared desc, display_name asc.
    # No rank number is derived — order is presentational only.
    rows.sort(key=lambda r: (
        not r['is_self'],
        -r['chapters_read'],
        -r['notes_shared'],
        _display_name(r['member']).lower(),
    ))
    return rows, distinct_readers
