from allauth.socialaccount.signals import social_account_added, pre_social_login


def mark_email_verified_on_social_login(sender, request, sociallogin, **kwargs):
    """Mark the user's email as verified when logging in via OAuth.

    OAuth providers (Google, GitHub) already verify the user's email,
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
