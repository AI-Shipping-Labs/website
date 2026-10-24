from django.apps import AppConfig


class EmailAppConfig(AppConfig):
    name = 'email_app'

    def ready(self):
        # Importing the checks module is enough to register the
        # ``@register``-decorated Relay configuration checks with Django's
        # system-check framework. (Relay cutover P4: the SES_ENABLED E001
        # check is gone with the SES send paths.)
        # A6.2: importing relay_sync registers its durable job handlers and
        # connects the relay_callback_processed receiver.
        from email_app import (
            checks,  # noqa: F401
            relay_sync,  # noqa: F401
        )
