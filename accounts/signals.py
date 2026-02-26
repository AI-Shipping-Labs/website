from allauth.socialaccount.signals import social_account_added, pre_social_login


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


def set_slack_user_id_on_social_login(sender, request, sociallogin, **kwargs):
    """Populate the user's slack_user_id after Slack OAuth login.

    This fires on pre_social_login (existing account) so the Slack
    identity is linked immediately without waiting for the email-matcher
    background job.
    """
    if sociallogin.account.provider != "slack":
        return
    user = sociallogin.user
    if not user.pk:
        return
    slack_user_id = _extract_slack_user_id(sociallogin.account.extra_data)
    if slack_user_id and not user.slack_user_id:
        user.slack_user_id = slack_user_id
        user.save(update_fields=["slack_user_id"])


def set_slack_user_id_on_social_signup(sender, request, sociallogin, **kwargs):
    """Populate the user's slack_user_id when a new Slack social account is added.

    This fires on social_account_added (new account or newly linked provider).
    """
    if sociallogin.account.provider != "slack":
        return
    user = sociallogin.user
    if not user.pk:
        return
    slack_user_id = _extract_slack_user_id(sociallogin.account.extra_data)
    if slack_user_id and not user.slack_user_id:
        user.slack_user_id = slack_user_id
        user.save(update_fields=["slack_user_id"])
