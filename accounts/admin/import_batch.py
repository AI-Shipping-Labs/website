from django.contrib import admin

from accounts.models import ImportBatch
from studio.admin_links import studio_link


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    """Admin view for import audit batches."""

    list_display = [
        "source",
        "status",
        "dry_run",
        "users_created",
        "users_updated",
        "users_skipped",
        "emails_queued",
        "started_at",
        "finished_at",
        "studio_link",
    ]
    list_filter = ["source", "status", "dry_run"]
    search_fields = ["actor__email", "summary"]
    raw_id_fields = ["actor"]
    readonly_fields = [
        "source",
        "actor",
        "started_at",
        "finished_at",
        "dry_run",
        "status",
        "users_created",
        "users_updated",
        "users_skipped",
        "emails_queued",
        "errors",
        "summary",
        "studio_link",
    ]
    ordering = ["-started_at"]

    @admin.display(description='Studio')
    def studio_link(self, obj):
        return studio_link(
            obj,
            'studio_import_batch_detail',
            lambda o: {'batch_id': o.pk},
        )
