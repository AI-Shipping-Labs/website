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
            populate_name_from_social,
            set_signup_source_oauth_on_social_signup,
            set_slack_user_id_on_social_login,
            set_slack_user_id_on_social_signup,
        )

        pre_social_login.connect(mark_email_verified_on_social_login)
        social_account_added.connect(mark_email_verified_on_social_signup)
        pre_social_login.connect(set_slack_user_id_on_social_login)
        social_account_added.connect(set_slack_user_id_on_social_signup)
        pre_social_login.connect(populate_name_from_social)
        social_account_added.connect(populate_name_from_social)
        # Issue #768: stamp signup_source='oauth' + account_activated=True
        # when a brand-new social account is linked.
        social_account_added.connect(set_signup_source_oauth_on_social_signup)

        from accounts.services.import_course_db import register_course_db_import_adapter

        register_course_db_import_adapter()
