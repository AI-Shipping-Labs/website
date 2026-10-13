"""Keep pod membership in step with enrollment (issues #1918, #1921).

``CohortEnrollment`` is the membership authority for cohort pods (the Maven
webhook, Studio and the API all write it) and ``SprintEnrollment`` for
sprint pods (``sprint_leave``, Studio unenroll and the enrollments API all
delete it). Deleting a user's enrollment removes them from that activity's
pods with the normal leave rules and cancels their open requests there;
other cohorts and sprints are untouched.
"""

from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete
from django.dispatch import receiver

from content.models import Cohort, CohortEnrollment
from plans.models import Sprint, SprintEnrollment
from pods.services.activity import sprint_activity
from pods.services.membership import remove_user_from_activity, remove_user_from_cohort


@receiver(post_delete, sender=CohortEnrollment, dispatch_uid='pods_cohort_enrollment_deleted')
def remove_pod_memberships_on_unenroll(sender, instance, **kwargs):
    # A cascading delete of the user or the cohort removes the pod rows as
    # well; only act while both sides still exist.
    user = get_user_model().objects.filter(pk=instance.user_id).first()
    cohort = Cohort.objects.filter(pk=instance.cohort_id).first()
    if user is None or cohort is None:
        return
    remove_user_from_cohort(user, cohort)


@receiver(post_delete, sender=SprintEnrollment, dispatch_uid='pods_sprint_enrollment_deleted')
def remove_sprint_pod_memberships_on_unenroll(sender, instance, **kwargs):
    # Same rule as the cohort receiver: a cascading user or sprint delete
    # takes the pod rows with it, so only act while both sides exist.
    user = get_user_model().objects.filter(pk=instance.user_id).first()
    sprint = Sprint.objects.filter(pk=instance.sprint_id).first()
    if user is None or sprint is None:
        return
    remove_user_from_activity(user, sprint_activity(sprint))
