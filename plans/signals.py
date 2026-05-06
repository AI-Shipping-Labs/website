"""Signals for the plans app (issue #443).

We auto-create a ``SprintEnrollment`` row whenever a ``Plan`` is created
without one. This keeps the new "enrollment is the membership row"
invariant transparent to callers that still create plans directly --
the Studio plan-create form, the bulk-import API, and the existing #440
cohort-board tests all keep working without modification.

Update / save flows on an existing plan do NOT touch enrollments: the
member-leave flow auto-privates the plan but only the dedicated leave
view (or the API ``DELETE`` endpoint) deletes the enrollment.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from plans.models import Plan, SprintEnrollment


@receiver(post_save, sender=Plan, dispatch_uid='plans_plan_post_save_enrollment')
def ensure_sprint_enrollment_on_plan_create(sender, instance, created, **kwargs):
    """Create a ``SprintEnrollment`` row for newly-created plans.

    Idempotent via ``get_or_create``; safe if an enrollment already
    exists (e.g. the member self-joined and Studio later created the
    plan). ``enrolled_by`` is left ``NULL`` because plan-create is not a
    "staff bulk-enrolled this user" event in the audit sense -- the
    Studio bulk-enroll page sets ``enrolled_by`` explicitly when it
    creates the row before any plan exists.
    """
    if not created:
        return
    SprintEnrollment.objects.get_or_create(
        sprint=instance.sprint,
        user=instance.member,
    )
