"""UI gating helpers for newsletter-only accounts (issue #769).

A "newsletter-only" user is someone whose row was created by the
newsletter-subscribe form (``signup_source == "newsletter"``) and who has
not yet taken any platform action that flipped ``account_activated``.
They confirmed their email so they could read the newsletter — but they
never agreed to become a platform user. The product decision (#769) is
to hide platform affordances (notification bell, profile editor,
membership, Slack card, change-password, timezone, full dashboard) from
them and surface a trimmed ``/account/`` page that lets them manage
newsletter preferences and offers a "Set a password to do more" CTA.

The predicate is intentionally narrow: ANY other ``signup_source``
(``signup``, ``oauth``, ``imported``, ``staff_create``, ``unknown``)
sees the full UI, even when ``account_activated`` is still ``False``.
The newsletter-only combination is the only one that gates.

Once ``account_activated=True`` (set by ``mark_activated`` per #768 on
password-set, OAuth link, comment post, event registration, course unit
complete, etc.), the predicate returns ``False`` on the very next
request and the full UI re-appears immediately — no cache invalidation
or session reset needed.
"""

from __future__ import annotations

from accounts.models.user import SIGNUP_SOURCE_NEWSLETTER
from accounts.utils.user_checks import is_authenticated_user


def is_newsletter_only_user(user) -> bool:
    """Return ``True`` iff ``user`` is a newsletter-only subscriber.

    ``True`` requires all three:

    - ``user.is_authenticated`` (anonymous visitors always see the full
      public UI; gating is irrelevant for them).
    - ``user.signup_source == "newsletter"`` (the row was created by the
      newsletter-subscribe form, not by an email+password signup, an
      OAuth flow, a Stripe checkout, or a Studio operator).
    - ``user.account_activated is False`` (the user has never taken a
      platform action). Once activation flips, the user permanently
      leaves the newsletter-only set even though ``signup_source`` keeps
      its original value.

    Safe to call with ``None`` or an anonymous user — both return
    ``False`` without touching the DB.
    """
    if not is_authenticated_user(user):
        return False
    if getattr(user, "signup_source", None) != SIGNUP_SOURCE_NEWSLETTER:
        return False
    return not getattr(user, "account_activated", False)
