from django.apps import AppConfig


class BookclubConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bookclub'
    verbose_name = 'Book Club'

    def ready(self):
        from bookclub.models import Note
        from comments.threads import register_thread_owner

        register_thread_owner(
            Note,
            content_id_field='comment_content_id',
            cascade_thread_delete=True,
        )
