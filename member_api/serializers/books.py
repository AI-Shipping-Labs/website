"""Member-safe Book Club reading serializers for ``/member-api/v1``."""

from __future__ import annotations

from bookclub.models import ChapterRead


def serialize_book_reading(book, user):
    """Serialize the caller's per-chapter reading state for a book.

    One query for the caller's read rows (``chapter__book=book``); no N+1
    over chapters. Only the key owner's own state is ever read.
    """
    chapters = list(book.chapters.all())
    read_at_by_chapter = {
        row.chapter_id: row.read_at
        for row in ChapterRead.objects.filter(user=user, chapter__book=book)
    }

    chapter_payload = []
    done = 0
    for chapter in chapters:
        read_at = read_at_by_chapter.get(chapter.id)
        is_read = read_at is not None
        if is_read:
            done += 1
        chapter_payload.append({
            "number": chapter.number,
            "title": chapter.title,
            "read": is_read,
            "read_at": read_at.isoformat() if read_at else None,
        })

    total = len(chapters)
    pct = round(done / total * 100) if total else 0
    return {
        "book": {"slug": book.slug, "title": book.title},
        "total": total,
        "done": done,
        "pct": pct,
        "chapters": chapter_payload,
    }
