from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    name = 'integrations'

    def ready(self):
        # Do not initialize Logfire here. AppConfig.ready() runs inside
        # django.setup(), which is on the ECS pre-bind critical path. Logfire
        # is initialized from serving-process hooks instead; see issue #1141.
        return
