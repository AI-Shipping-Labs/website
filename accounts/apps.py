from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = 'accounts'

    def ready(self):
        from allauth.socialaccount.signals import (
            social_account_added,
            pre_social_login,
        )
        from accounts.signals import (
            mark_email_verified_on_social_login,
            mark_email_verified_on_social_signup,
        )

        pre_social_login.connect(mark_email_verified_on_social_login)
        social_account_added.connect(mark_email_verified_on_social_signup)
