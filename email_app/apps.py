from django.apps import AppConfig


class EmailAppConfig(AppConfig):
    name = 'email_app'

    def ready(self):
        # Importing the checks module is enough to register the
        # ``@register``-decorated ``check_ses_enabled_in_production``
        # function with Django's system-check framework. Issue #521.
        # A6.2: importing relay_sync registers its durable job handlers and
        # connects the relay_callback_processed receiver.
        from email_app import (
            checks,  # noqa: F401
            relay_sync,  # noqa: F401
        )
