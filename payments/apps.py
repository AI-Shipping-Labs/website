from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    name = 'payments'

    def ready(self):
        from payments.services.import_stripe import register_stripe_import_adapter

        register_stripe_import_adapter()

        # Connect the Membership post-create receiver (issue #1579).
        from payments import signals  # noqa: F401
