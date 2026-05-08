from django.apps import AppConfig


class EmailAppConfig(AppConfig):
    name = 'email_app'

    def ready(self):
        # Importing the checks module is enough to register the
        # ``@register``-decorated ``check_ses_enabled_in_production``
        # function with Django's system-check framework. Issue #521.
        from email_app import checks  # noqa: F401
