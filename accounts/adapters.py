"""Custom allauth social-account adapter (issue #845).

The only override is :meth:`SocialAccountAdapter.pre_social_login`, which routes
a FIRST-EVER OAuth login whose verified email is an ``EmailAlias`` onto the
canonical account instead of the deactivated secondary (or a brand-new
duplicate).

Why this is needed
------------------

``SOCIALACCOUNT_EMAIL_AUTHENTICATION`` makes allauth match an incoming social
login to an existing user by its verified email. After an account merge the
merged-away email survives only as an ``EmailAlias`` of canonical -- the
original ``User`` row is deactivated and (since #845) its ``email`` is scrubbed.
So allauth's email match finds nothing and would create a duplicate account. By
consulting ``resolve_user_by_email`` (the single resolution source of truth) and
calling ``sociallogin.connect(request, canonical)`` we attach the identity to
canonical instead.

An identity that was LINKED before the merge already works without this adapter:
the merge engine repoints its allauth ``SocialAccount`` rows to canonical, so
``sociallogin.is_existing`` is true and we leave it untouched.
"""

import logging

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from accounts.services.email_resolution import resolve_user_by_email

logger = logging.getLogger(__name__)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Connect an as-yet-unlinked OAuth login to the canonical account.

    Only acts when the social login is NOT already linked to a user and its
    verified email resolves (via an ``EmailAlias``) to a canonical account.
    Never connects to or activates the deactivated secondary, and never lets a
    duplicate user be created for an aliased email.
    """

    def pre_social_login(self, request, sociallogin):
        # A login that already maps to a user (e.g. a SocialAccount repointed to
        # canonical by the merge engine) is handled by allauth -- don't touch it.
        if sociallogin.is_existing:
            return

        # allauth only fills ``sociallogin.user.pk`` once the identity matches an
        # existing user; an unmatched first-ever login has an unsaved user.
        if getattr(sociallogin.user, "pk", None):
            return

        email = (sociallogin.user.email or "").strip()
        if not email:
            return

        canonical = resolve_user_by_email(email)
        # Only intervene when resolution came through an alias, i.e. there is no
        # ACTIVE primary login for this email (resolve_user_by_email already
        # skips inactive primaries, so a returned user whose own email differs
        # from the typed one is the alias-owner canonical). When the typed email
        # IS an active primary, allauth's own email match handles it -- and the
        # ``is_existing`` / ``pk`` guards above will normally have returned
        # already.
        if canonical is None or not canonical.is_active:
            return

        # Attach this identity to the canonical account. ``connect`` saves the
        # SocialAccount against canonical and sets ``sociallogin.user``, so
        # allauth logs the session in as canonical -- never the dead secondary,
        # never a duplicate.
        sociallogin.connect(request, canonical)
