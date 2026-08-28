from django.apps import AppConfig


class PlansConfig(AppConfig):
    name = 'plans'

    def ready(self):
        # Wire post_save signal so plan creation back-creates the
        # ``SprintEnrollment`` row (issue #443).
        from comments.threads import register_thread_owner
        from plans import signals  # noqa: F401
        from plans.models import Plan

        register_thread_owner(
            Plan,
            content_id_field='comment_content_id',
            cascade_thread_delete=True,
        )
