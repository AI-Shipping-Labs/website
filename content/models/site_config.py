from django.db import models


class SiteConfig(models.Model):
    """Key-value store for site configuration synced from the content repo.

    Used to store parsed YAML data (like tiers.yaml) as JSON so that
    runtime code reads from the database instead of from disk.
    """

    key = models.CharField(max_length=100, unique=True)
    data = models.JSONField(default=list)

    class Meta:
        verbose_name = 'site config'
        verbose_name_plural = 'site configs'

    def __str__(self):
        return self.key
