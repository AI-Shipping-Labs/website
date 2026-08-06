"""Permission hook that lets the shared comments app host Book Club note threads.

Issue #1365 (folding #1360) reuses ``comments.Comment`` / ``CommentVote`` and
the existing ``/api/comments/<uuid:content_id>`` endpoints for the discussion
under each member's chapter note. The gating differs from course / workshop
comments, so — exactly like the plan bridge (``plans.comments_permissions``,
issue #499) — this module exposes a single resolver plus read/write predicates
that the comments API consults whenever a ``content_id`` happens to resolve to
a ``Note.comment_content_id``.

Rules (matched 1:1 with the issue ACs):

- A note thread is gated on the note's book tier: a viewer may read or write
  iff ``get_user_level(viewer) >= note.chapter.book.required_level`` (staff
  bypass, mirroring every other content surface).
- Anonymous viewers can never read (the API returns 404 so a below-tier /
  anonymous viewer cannot even confirm the note exists) and never write (the
  shared ``create_comment`` already returns 401 before this hook runs).

The public surface mirrors the plan module:

- :func:`resolve_book_note_for_content_id` — returns the :class:`Note` whose
  ``comment_content_id`` matches, or ``None`` to fall through to the default
  comments behaviour (course units, workshop pages, plans).
- :func:`viewer_can_read_book_note_thread`,
  :func:`viewer_can_write_book_note_thread` — the boolean predicates the
  comments API calls.

This file lives in the ``bookclub`` app (not ``comments``): comment access
policy is a Book Club domain concern, and the comments app stays generic.
"""

from __future__ import annotations

from typing import Optional

from bookclub.models import Note
from content.access import get_user_level


def resolve_book_note_for_content_id(content_id) -> Optional[Note]:
    """Return the :class:`Note` whose ``comment_content_id`` is ``content_id``.

    Returns ``None`` when the UUID is not a Book Club note thread (e.g. it
    belongs to a course unit, workshop page, or plan). Callers must treat
    ``None`` as "fall through to the default comments behaviour", not as an
    error.
    """
    if content_id is None:
        return None
    return (
        Note.objects
        .filter(comment_content_id=content_id)
        .select_related('chapter__book')
        .first()
    )


def _viewer_meets_book_tier(note: Note, viewer) -> bool:
    """Whether ``viewer`` clears the note's book required level.

    Anonymous viewers are level 0, so they never clear a paid book — reads on
    a note thread therefore 404 for them (existence is not leaked) and writes
    are already rejected as 401 by the shared endpoint's auth check.
    """
    return get_user_level(viewer) >= note.chapter.book.required_level


def viewer_can_read_book_note_thread(note: Note, viewer) -> bool:
    """Whether ``viewer`` may GET ``/api/comments/<note.comment_content_id>``."""
    return _viewer_meets_book_tier(note, viewer)


def viewer_can_write_book_note_thread(note: Note, viewer) -> bool:
    """Whether ``viewer`` may POST top-level / reply / vote on this note thread."""
    return _viewer_meets_book_tier(note, viewer)
