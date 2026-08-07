"""Reader-profile visibility helpers (issue #1366).

Every read of a member's public/private posture routes through this single
seam so the board (#1367), the group feed (#1365), and the profile page all
consult one place — and a later per-book visibility extension can be added
without reshaping any caller. Absence of a ``ReaderProfile`` row means
``private`` (the chosen default).
"""

from __future__ import annotations

from bookclub.models import READER_VISIBILITY_PUBLIC, ReaderProfile


def is_reader_public(user) -> bool:
    """Return whether ``user`` has opted their reading profile public.

    One query. Anonymous / ``None`` users are never public. A member with no
    ``ReaderProfile`` row is treated as private (the default).
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return ReaderProfile.objects.filter(
        user=user, visibility=READER_VISIBILITY_PUBLIC,
    ).exists()


def public_reader_ids(user_ids) -> set:
    """Return the subset of ``user_ids`` whose reader profile is public.

    One query regardless of roster size (no N+1) — this is the single seam the
    board and feed consult to partition readers. Ids without a ``ReaderProfile``
    row (or with a private one) are excluded, matching the private default.
    """
    ids = {uid for uid in user_ids if uid is not None}
    if not ids:
        return set()
    return set(
        ReaderProfile.objects.filter(
            user_id__in=ids, visibility=READER_VISIBILITY_PUBLIC,
        ).values_list('user_id', flat=True)
    )
