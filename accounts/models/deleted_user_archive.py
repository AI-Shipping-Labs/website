from django.db import models


class DeletedUserArchive(models.Model):
    """Backup of one account removed by the verify-or-delete job.

    Written in the same transaction as the delete, so a deleted account always
    has its row here. ``campaign_id`` is a plain integer (not a foreign key) on
    purpose: the archive must outlive both the user and the campaign.
    """

    original_user_id = models.PositiveBigIntegerField(db_index=True)
    email = models.CharField(max_length=254, db_index=True)
    campaign_id = models.PositiveBigIntegerField(db_index=True)
    snapshot = models.JSONField(default=dict)
    deleted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.email} (campaign {self.campaign_id})"
