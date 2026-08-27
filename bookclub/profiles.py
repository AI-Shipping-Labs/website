"""Reader-note visibility helpers (issues #1366/#1457).

Every read of a member's public/private posture routes through this single
seam so the group feed (#1365) and profile page consult one place — and a
later per-book visibility extension can be added without reshaping any caller.
Absence of a ``ReaderProfile`` row means public; only an explicit private row
hides notes from other members.
"""

from __future__ import annotations

from bookclub.models import READER_VISIBILITY_PRIVATE, ReaderProfile


def notes_are_public(user) -> bool:
    """Return whether other members may read ``user``'s book notes.

    One query. Anonymous / ``None`` users never expose notes. A member with no
    ``ReaderProfile`` row is public by default; only an explicit private row
    hides notes.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return not ReaderProfile.objects.filter(
        user=user, visibility=READER_VISIBILITY_PRIVATE,
    ).exists()


def public_note_author_ids(user_ids) -> set:
    """Return ids whose book notes are visible to other members.

    One query regardless of input size (no N+1). Ids without a profile row are
    included; ids with an explicit private row are excluded. ``None`` and
    anonymous objects (whose ``pk`` is ``None``) are ignored.
    """
    ids = {
        getattr(uid, 'pk', uid)
        for uid in user_ids
        if getattr(uid, 'pk', uid) is not None
    }
    if not ids:
        return set()
    private_ids = set(
        ReaderProfile.objects.filter(
            user_id__in=ids, visibility=READER_VISIBILITY_PRIVATE,
        ).values_list('user_id', flat=True)
    )
    return ids - private_ids
