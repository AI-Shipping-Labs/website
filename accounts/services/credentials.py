"""Revoke API credentials when an account is deactivated.

Three models store a hashed secret keyed to a user, and deactivation has to
handle all of them (issue #1744):

- ``accounts.MemberAPIKey`` -- revoked in place; the row is durable security
  history and must never be deleted.
- ``accounts.Token`` -- deleted, because it has no revocation field; row
  deletion is its only revoke mechanism.
- ``community_base.api.APIKey`` (``cb_api.APIKey``) -- revoked in place, the
  ``MemberAPIKey`` precedent, because it carries ``revoked_at``.

Reactivation must not restore any of them. For the package key that is the
whole point of revoking rather than leaning on ``APIKey.authenticate``'s
``user__is_active=True`` filter: that filter is suppression, so flipping
``is_active`` back on made the same plaintext authenticate again, and it is
package internals on a pinned release that a version bump could relax.

``DEACTIVATION_CREDENTIAL_HANDLERS`` is the importable enumeration of the
credential models this service handles, keyed like
``account_merge._SPECIAL_STRATEGIES``. A structural guard in
``accounts/tests/test_cb_api_key_lifecycle_1744.py`` walks every user-owned
model with a concrete ``key_hash`` field against it (and against
``privacy.CREDENTIAL_EXPORT_SECTIONS``), so the next credential model mounted
onto the site fails a test instead of shipping a hole.

The package model is imported directly here, while ``privacy.py`` resolves the
same model through ``apps.get_model`` and degrades to ``[]``. That asymmetry is
deliberate, not an inconsistency to harmonize: an incomplete export is
recoverable, a silently unrevoked bearer credential is an incident. This path
must fail loudly if the package ever goes missing.
"""

from community_base.api.models import APIKey as PackageAPIKey
from django.utils import timezone

from accounts.models.member_api_key import MemberAPIKey
from accounts.models.token import Token


def _revoke_member_api_keys(user):
    """Stamp ``revoked_at`` on the user's live member keys."""
    MemberAPIKey.objects.filter(user=user, revoked_at__isnull=True).update(
        revoked_at=timezone.now(),
    )


def _delete_operator_tokens(user):
    """Delete the user's operator tokens -- they have no revocation field."""
    Token.objects.filter(user=user).delete()


def _revoke_package_api_keys(user):
    """Stamp ``revoked_at`` on the user's live ``cb_api.APIKey`` rows.

    Both ``kind="member"`` and ``kind="staff"`` rows are revoked; a deactivated
    operator must not be one flag flip away from a live wildcard credential.

    The revocation MUST stay a queryset ``update()``. ``APIKey.save()`` calls
    ``full_clean()`` and ``APIKey.clean()`` rejects a ``kind="staff"`` row whose
    owner is no longer ``is_staff`` -- exactly the legacy row a demoted
    operator's deactivation has to revoke. This runs inside ``User.save()``, so
    a raise here would abort the deactivation itself. ``APIKey.revoke()``
    happens to be a queryset ``update()`` in the pinned release and would work
    today, but the site must not depend on a package internal staying that way
    across versions. A ``.update()`` written here is safe under either package
    implementation, and it is one query instead of N.
    """
    PackageAPIKey.objects.filter(user=user, revoked_at__isnull=True).update(
        revoked_at=timezone.now(),
    )


#: ``(model label, user field name)`` -> the handler that kills that credential
#: on deactivation. Every user-owned model storing a ``key_hash`` must appear
#: here; the structural guard for #1744 fails the build if one does not.
DEACTIVATION_CREDENTIAL_HANDLERS = {
    ("accounts.MemberAPIKey", "user"): _revoke_member_api_keys,
    ("accounts.Token", "user"): _delete_operator_tokens,
    ("cb_api.APIKey", "user"): _revoke_package_api_keys,
}


def revoke_api_credentials_on_deactivation(user):
    """Kill every API credential owned by ``user``, in place where possible.

    Already-revoked rows keep their original ``revoked_at``; each handler
    filters on ``revoked_at__isnull=True`` so a second deactivating save does
    not restamp them.
    """
    for handler in DEACTIVATION_CREDENTIAL_HANDLERS.values():
        handler(user)
