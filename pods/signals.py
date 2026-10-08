"""Keep pod membership in step with cohort enrollment (issue #1918).

``CohortEnrollment`` is the membership authority (the Maven webhook, Studio
and the API all write it). Deleting a user's enrollment removes them from
that cohort's pods with the normal leave rules and cancels their open
requests in that cohort; other cohorts are untouched.
"""

from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete
from django.dispatch import receiver

from content.models import Cohort, CohortEnrollment
from pods.services.membership import remove_user_from_cohort


@receiver(post_delete, sender=CohortEnrollment, dispatch_uid='pods_cohort_enrollment_deleted')
def remove_pod_memberships_on_unenroll(sender, instance, **kwargs):
    # A cascading delete of the user or the cohort removes the pod rows as
    # well; only act while both sides still exist.
    user = get_user_model().objects.filter(pk=instance.user_id).first()
    cohort = Cohort.objects.filter(pk=instance.cohort_id).first()
    if user is None or cohort is None:
        return
    remove_user_from_cohort(user, cohort)
