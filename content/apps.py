from django.apps import AppConfig


class ContentConfig(AppConfig):
    name = "content"

    def ready(self):
        import content.signals  # noqa: F401
        from content.curriculum_compat import apply as apply_curriculum_compat
        from content.kinds import register_aisl_kinds
        from content.sync_parsers import register_all

        # A7.2a: site kinds before parsers so check_content and the engine
        # see workshop/project/curated_link/interview_question/member_wiki.
        register_aisl_kinds()
        apply_curriculum_compat()
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
            content_id_field='source_content_id',
            cascade_thread_delete=False,
        )
        register_thread_owner(
            WorkshopPage,
            content_id_field="content_id",
            cascade_thread_delete=False,
        )
