"""Revoke API credentials when an account is deactivated."""

from django.utils import timezone

from accounts.models.member_api_key import MemberAPIKey
from accounts.models.token import Token


def revoke_api_credentials_on_deactivation(user):
    """Revoke member keys in place and delete operator tokens for ``user``.

    Already-revoked member keys are left unchanged. ``Token`` has no
    revocation field, so row deletion is the revoke mechanism. Reactivation
    must not restore these credentials.
    """
    MemberAPIKey.objects.filter(user=user, revoked_at__isnull=True).update(
        revoked_at=timezone.now(),
    )
    Token.objects.filter(user=user).delete()
