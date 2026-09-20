"""Keep exactly one MemberExtra row per user.

Mirrors the ``payments.Membership`` creation invariant from #1579: a
post-create receiver owns row creation so no signup entry point has to
remember it, and ``MemberExtra.for_user`` stays the lazy fallback for rows
that predate the receiver.
"""

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.db.utils import OperationalError, ProgrammingError
from django.dispatch import receiver

from accounts_ext.models import MemberExtra


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="accounts_ext.create_member_extra")
def create_member_extra(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        with transaction.atomic():
            MemberExtra.objects.get_or_create(user_id=instance.pk)
    except (OperationalError, ProgrammingError):
        # Historical-migration tests and migrate itself can create User rows
        # before accounts_ext tables exist. Membership.for_user is the
        # fallback once the table is there.
        return
