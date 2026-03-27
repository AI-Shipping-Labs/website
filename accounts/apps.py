from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = 'accounts'

    def ready(self):
        from allauth.socialaccount.signals import (
            pre_social_login,
            social_account_added,
        )

        from accounts.signals import (
            mark_email_verified_on_social_login,
            mark_email_verified_on_social_signup,
            set_slack_user_id_on_social_login,
            set_slack_user_id_on_social_signup,
        )

        pre_social_login.connect(mark_email_verified_on_social_login)
        social_account_added.connect(mark_email_verified_on_social_signup)
        pre_social_login.connect(set_slack_user_id_on_social_login)
        social_account_added.connect(set_slack_user_id_on_social_signup)
