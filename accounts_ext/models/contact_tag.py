"""Operator-managed contact-tag namespace (plan issue A3.2, #1692).

The shared ``community_base.accounts`` donor does not own contact tags, so
they stay site-owned here. Only the app label moved: the table is pinned to
``accounts_contacttag`` and is never rebuilt.
"""

from django.db import models


class ContactTag(models.Model):
    """Indexed contact-tag namespace used for membership queries."""

    slug = models.CharField(max_length=255, unique=True)

    class Meta:
        # Pinned: this model moved app label only (SeparateDatabaseAndState).
        db_table = "accounts_contacttag"
        ordering = ["slug"]

    def __str__(self):
        return self.slug


__all__ = ["ContactTag"]
