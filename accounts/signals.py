from django.utils import timezone

from accounts.models.user import (
    SIGNUP_SOURCE_OAUTH,
    SIGNUP_SOURCE_UNKNOWN,
)
from accounts.utils.activation import mark_activated
from accounts.utils.names import set_name_from_external


def mark_email_verified_on_social_login(sender, request, sociallogin, **kwargs):
    """Mark the user's email as verified when logging in via OAuth.

    OAuth providers (Google, GitHub, Slack) already verify the user's email,
    so we trust their verification.
    """
    user = sociallogin.user
    if user.pk and not user.email_verified:
        user.email_verified = True
        user.save(update_fields=["email_verified"])


def mark_email_verified_on_social_signup(sender, request, sociallogin, **kwargs):
    """Mark email verified when a new social account is connected."""
    user = sociallogin.user
    if user.pk and not user.email_verified:
        user.email_verified = True
        user.save(update_fields=["email_verified"])


def set_signup_source_oauth_on_social_signup(sender, request, sociallogin, **kwargs):
    """Stamp ``signup_source='oauth'`` and ``account_activated=True`` on social signup.

    Fires on ``social_account_added`` (allauth signal for a brand-new
    social account being linked to a user — covers both fresh signups
    and an existing email-signup user later linking an OAuth provider).

    Two safety rails (issue #768):

    - A no-op when the user already has a non-``unknown`` signup source
      (e.g. an existing email-signup or newsletter user later linking
      Google). The original source is canonical and must not be
      overwritten — only the ``unknown`` placeholder gets upgraded
      to ``oauth``.
    - Always sets ``account_activated=True``. OAuth providers verified
      the email AND the user just successfully clicked through to link
      an identity — both branches (new account, existing account
      linking OAuth) are activation events.
    """
    user = sociallogin.user
    if not user.pk:
        return

    update_fields = []

    # Only promote the placeholder ``unknown`` source. An existing
    # ``newsletter`` / ``signup`` user linking Google later keeps
    # their original source — the OAuth link is not the row's origin.
    if user.signup_source == SIGNUP_SOURCE_UNKNOWN:
        user.signup_source = SIGNUP_SOURCE_OAUTH
        update_fields.append("signup_source")

    # OAuth link is always an activation event.
    if not user.account_activated:
        user.account_activated = True
        update_fields.append("account_activated")

    if update_fields:
        user.save(update_fields=update_fields)


def _extract_slack_user_id(extra_data):
    """Extract the Slack user ID from sociallogin extra_data.

    The Slack OpenID Connect userInfo response includes the user ID in
    the `https://slack.com/user_id` field. If that is not present, fall
    back to the nested `user.id` field.
    """
    slack_user_id = extra_data.get("https://slack.com/user_id", "")
    if not slack_user_id:
        user_obj = extra_data.get("user", {})
        if isinstance(user_obj, dict):
            slack_user_id = user_obj.get("id", "")
    return slack_user_id or ""


def _apply_slack_oauth_membership(user, sociallogin):
    """Persist slack_user_id, slack_member, slack_checked_at after Slack OAuth.

    Slack OAuth proves the user signed in with a Slack identity from
    our workspace's auth flow, so we set ``slack_member=True``. Issue
    #358 keeps this canonical bit on the User row so the dashboard CTA
    and campaign filters get the right answer immediately.
    """
    slack_user_id = _extract_slack_user_id(sociallogin.account.extra_data)
    update_fields = []

    if slack_user_id and not user.slack_user_id:
        user.slack_user_id = slack_user_id
        update_fields.append("slack_user_id")

    if not user.slack_member:
        user.slack_member = True
        update_fields.append("slack_member")

    user.slack_checked_at = timezone.now()
    update_fields.append("slack_checked_at")

    if update_fields:
        user.save(update_fields=update_fields)

    # Issue #768: a Slack-membership-flip via OAuth proves the user
    # joined our community workspace — flip ``account_activated`` so
    # platform-only UI starts surfacing for them. ``mark_activated`` is
    # idempotent and uses its own ``save(update_fields=...)`` call,
    # so it does not collide with the slack save above.
    mark_activated(user)


def set_slack_user_id_on_social_login(sender, request, sociallogin, **kwargs):
    """Populate the user's slack_user_id after Slack OAuth login.

    This fires on pre_social_login (existing account) so the Slack
    identity is linked immediately without waiting for the email-matcher
    background job. Also flips ``slack_member=True`` (issue #358).
    """
    if sociallogin.account.provider != "slack":
        return
    user = sociallogin.user
    if not user.pk:
        return
    _apply_slack_oauth_membership(user, sociallogin)


def set_slack_user_id_on_social_signup(sender, request, sociallogin, **kwargs):
    """Populate slack_user_id and slack_member when a new Slack social account is added.

    This fires on social_account_added (new account or newly linked provider).
    """
    if sociallogin.account.provider != "slack":
        return
    user = sociallogin.user
    if not user.pk:
        return
    _apply_slack_oauth_membership(user, sociallogin)


def populate_name_from_social(sender, request, sociallogin, **kwargs):
    """Populate ``first_name`` / ``last_name`` from an OAuth identity (issue #699).

    Wired to both ``pre_social_login`` (existing account re-signing in)
    and ``social_account_added`` (new social signup). Reads
    provider-specific claims from ``sociallogin.account.extra_data``:

    - Google (OIDC): ``given_name`` + ``family_name``, with a fallback
      to the combined ``name`` when only that is present.
    - GitHub: ``name`` (single string — GitHub does not split it).
    - Slack (OIDC): ``given_name`` + ``family_name``, with a fallback
      to the combined ``name`` when only that is present.

    The helper refuses to overwrite a value the user already has, so
    re-signing in is a safe no-op for users whose names are already set.
    """
    user = sociallogin.user
    if not user.pk:
        return

    provider = sociallogin.account.provider
    extra = sociallogin.account.extra_data or {}

    given = (extra.get("given_name") or "").strip()
    family = (extra.get("family_name") or "").strip()
    combined = (extra.get("name") or "").strip()

    changed = False
    if provider == "github":
        # GitHub only fills ``name`` and never splits it.
        if combined:
            changed = set_name_from_external(
                user, full_name=combined, source="oauth:github",
            )
    elif provider in ("google", "slack"):
        if given or family:
            changed = set_name_from_external(
                user,
                first=given,
                last=family,
                source=f"oauth:{provider}",
            )
        elif combined:
            # Some IdP responses only carry the combined ``name`` —
            # fall through to splitting it rather than no-op.
            changed = set_name_from_external(
                user, full_name=combined, source=f"oauth:{provider}",
            )
    else:
        # Unknown provider: ignore. We deliberately do not guess.
        return

    if changed:
        user.save(update_fields=["first_name", "last_name"])
