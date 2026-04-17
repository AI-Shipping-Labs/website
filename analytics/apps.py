from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    name = 'analytics'
    verbose_name = 'Analytics'

    def ready(self):
        # Wire up the post_save handler that snapshots UTM attribution
        # at signup time, plus the allauth handler that refines
        # signup_path for OAuth signups.
        from django.apps import apps
        from django.conf import settings
        from django.db.models.signals import post_save

        from analytics.signals import (
            create_user_attribution,
            update_signup_path_for_social_signup,
        )

        user_model = apps.get_model(settings.AUTH_USER_MODEL)
        post_save.connect(
            create_user_attribution,
            sender=user_model,
            dispatch_uid='analytics.create_user_attribution',
        )

        # allauth user_signed_up — fires for both plain and social signups.
        try:
            from allauth.account.signals import user_signed_up
        except ImportError:  # pragma: no cover — allauth always installed
            return
        user_signed_up.connect(
            update_signup_path_for_social_signup,
            dispatch_uid='analytics.update_signup_path_for_social_signup',
        )
