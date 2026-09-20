"""Keep exactly one MemberExtra row per user.

Mirrors the ``payments.Membership`` creation invariant from #1579: a
post-create receiver owns row creation so no signup entry point has to
remember it, and ``MemberExtra.for_user`` stays the lazy fallback for rows
that predate the receiver.
"""

from django.conf import settings
from django.db import connection
from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts_ext.models import MemberExtra


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="accounts_ext.create_member_extra")
def create_member_extra(sender, instance, created, **kwargs):
    if not created:
        return
    # Historical-migration tests create User rows before accounts_ext tables
    # exist. Raising here poisons the caller's transaction; skip instead.
    if MemberExtra._meta.db_table not in connection.introspection.table_names():
        return
    MemberExtra.objects.get_or_create(user_id=instance.pk)
