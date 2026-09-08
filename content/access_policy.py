"""Kernel access policy backed by the site's tiers (plan issue A0.3).

Implements the ``community_base.kernel.access`` protocol with the same
semantics as ``content.access``: the user's tier level plus any active
``TierOverride`` via ``get_user_level``, staff accounts at
``LEVEL_PREMIUM``, and the issue-#465 registration wall (anonymous
denied, any authenticated tier allowed once their email is verified).
Individual ``CourseAccess`` bypasses stay at the content call sites —
they are content-specific and out of scope for a level policy. Call
sites keep calling ``content.access.can_access``; they re-point at the
kernel per app in later adoption phases (playbook P3).
"""

from community_base.kernel.access import (
    LEVEL_BASIC,
    LEVEL_LABELS,
    LEVEL_OPEN,
    LEVEL_REGISTERED,
)

from content.access import get_user_level


class TierAccessPolicy:
    """The ``ACCESS_POLICY`` implementation configured in website settings."""

    def user_level(self, user) -> int:
        return get_user_level(user)

    def can_access(self, user, required_level: int) -> bool:
        if required_level == LEVEL_OPEN:
            return True
        if required_level == LEVEL_REGISTERED:
            if user is None or not user.is_authenticated:
                return False
            if get_user_level(user) >= LEVEL_BASIC:
                return True
            return bool(getattr(user, 'email_verified', False))
        return get_user_level(user) >= required_level

    def level_label(self, level: int) -> str:
        return LEVEL_LABELS.get(level, str(level))
