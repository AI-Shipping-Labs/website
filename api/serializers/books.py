"""Plain-dict serializers for the Book Club API (issue #1362).

Pure functions, not DRF serializers — mirror ``api/serializers/plans.py``.
Each ``serialize_*`` takes a model instance and returns the JSON-ready shape.
"""

from __future__ import annotations


def _isoformat_or_none(value):
    if value is None:
        return None
    return value.isoformat()


def serialize_chapter(chapter):
    """Flat chapter row.

    ``event`` is a resolvable object (``{id, slug, title, url}``) when the
    chapter is linked to an event, else ``null`` (issue #1369). This replaces
    the bare ``event_id`` the foundation (#1362) returned so an attach/detach
    is verifiable through the API.
    """
    event = chapter.event
    return {
        "number": chapter.number,
        "title": chapter.title,
        "deadline": _isoformat_or_none(chapter.deadline),
        "week_label": chapter.week_label,
        "event": (
            {
                "id": event.id,
                "slug": event.slug,
                "title": event.title,
                "url": event.get_absolute_url(),
            }
            if event is not None
            else None
        ),
        "summary": chapter.summary,
        "summary_published": chapter.is_summary_published,
        "summary_published_at": _isoformat_or_none(chapter.summary_published_at),
        "created_at": _isoformat_or_none(chapter.created_at),
        "updated_at": _isoformat_or_none(chapter.updated_at),
    }


def serialize_book(book, *, include_chapters=False):
    """Book dict shape used by every book endpoint.

    ``include_chapters`` embeds the ordered chapter list; the detail endpoint
    passes it while the collection list stays flat.
    """
    series = book.event_series
    data = {
        "slug": book.slug,
        "title": book.title,
        "subtitle": book.subtitle,
        "author": book.author,
        "description": book.description,
        "cover_image_url": book.cover_image_url,
        "cover_accent": book.cover_accent,
        "required_level": book.required_level,
        "status": book.status,
        "start_date": (
            book.start_date.isoformat() if book.start_date else None
        ),
        "meeting_cadence": book.meeting_cadence,
        "buy_url": book.buy_url,
        "event_series": (
            {"id": series.id, "slug": series.slug} if series else None
        ),
        "summary": book.summary,
        "summary_published": book.is_summary_published,
        "summary_published_at": _isoformat_or_none(book.summary_published_at),
        "chapter_count": book.chapters.count(),
        "created_at": _isoformat_or_none(book.created_at),
        "updated_at": _isoformat_or_none(book.updated_at),
    }
    if include_chapters:
        data["chapters"] = [
            serialize_chapter(chapter) for chapter in book.chapters.all()
        ]
    return data
