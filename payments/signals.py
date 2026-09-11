"""Signal receivers for the payments app (issue #1579).

The post-create receiver keeps the "every User row has exactly one
Membership row" invariant for brand-new users from every entry point
(registration, newsletter double opt-in, OAuth, Stripe import, course-db
import, staff create). ``Membership.for_user`` stays as the lazy fallback;
row creation is deliberately NOT spread across the signup entry points.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import User
from payments.models import Membership


@receiver(
    post_save,
    sender=User,
    dispatch_uid="payments.ensure_membership_on_user_create",
)
def ensure_membership_on_user_create(sender, instance, created, **kwargs):
    if not created:
        return
    Membership.for_user(instance)
