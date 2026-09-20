"""Per-user holder for the site-only remainder of the user model.

Plan issue A3.2 (#1692): when AISL adopts ``community_base.accounts`` the
shared ``User`` carries the C3.1 field table only. The mechanical diff of
``User._meta.get_fields()`` against that table leaves exactly one field-level
remainder, the ``contact_tags`` many-to-many, so that relation lives here.

``MemberExtra`` is keyed on the user row itself, so its primary key equals the
user id and the contact-tag relation copies across without renumbering.
"""

from django.conf import settings
from django.db import models

from .contact_tag import ContactTag


class MemberExtra(models.Model):
    """Site-owned extension row for one user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="member_extra",
        primary_key=True,
    )
    contact_tags = models.ManyToManyField(
        ContactTag,
        related_name="member_extras",
        blank=True,
        help_text="Indexed membership relation mirroring the tags JSON payload.",
    )

    class Meta:
        ordering = ["user_id"]
        verbose_name = "Member extra"
        verbose_name_plural = "Member extras"

    def __str__(self):
        return f"MemberExtra({self.user_id})"

    @classmethod
    def for_user(cls, user):
        """Return the user's row, creating it lazily when missing."""
        extra, _ = cls.objects.get_or_create(user_id=user.pk)
        return extra


__all__ = ["MemberExtra"]
