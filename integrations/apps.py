from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    name = 'integrations'

    def ready(self):
        # This app-init pass deliberately resolves settings/env/defaults only.
        # Serving containers make a DB-backed pass after django.setup().
        from integrations.services.observability import init_logfire
        init_logfire()
