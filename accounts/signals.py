from django.utils import timezone


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
