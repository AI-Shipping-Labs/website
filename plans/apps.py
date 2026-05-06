from django.apps import AppConfig


class PlansConfig(AppConfig):
    name = 'plans'

    def ready(self):
        # Wire post_save signal so plan creation back-creates the
        # ``SprintEnrollment`` row (issue #443).
        from plans import signals  # noqa: F401

