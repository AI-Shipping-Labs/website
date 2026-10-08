from django.apps import AppConfig


class PodsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'pods'
    verbose_name = 'Pods'

    def ready(self):
        # Registers the CohortEnrollment post_delete receiver; importing at
        # ready() time is Django's documented signal-registration hook.
        from pods import signals  # noqa: F401, PLC0415
