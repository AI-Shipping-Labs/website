from django.apps import AppConfig


class ContentConfig(AppConfig):
    name = 'content'

    def ready(self):
        import content.signals  # noqa: F401
        from content.sync_parsers import register_all

        # A2.3: site parsers register with the package content_sync engine
        # at startup; registration is deterministic and fails on duplicates.
        register_all()
        from comments.threads import register_thread_owner
        from content.models import Unit, WorkshopPage

        # These UUIDs come from content frontmatter and are reused when sync
        # deletes and rebuilds rows. Register them for orphan detection, but
        # never cascade: ordinary content sync must preserve course Q&A.
        register_thread_owner(
            Unit,
            content_id_field='content_id',
            cascade_thread_delete=False,
        )
        register_thread_owner(
            WorkshopPage,
            content_id_field='content_id',
            cascade_thread_delete=False,
        )
