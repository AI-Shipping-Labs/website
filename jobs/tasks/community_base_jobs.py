"""django-q entry points for the community-base durable jobs housekeeping.

`setup_schedules` registers these on the cluster (names ``cb-jobs-run-due``
and ``cb-jobs-sweep``) so the package's durable intents run and recover on
the same worker that already runs every other recurring job (plan A1.1).
Each wrapper is a thin `call_command` shim so the q payload stays a static
dotted path and the management command owns its own arguments/limits.
"""

import logging

from community_base.jobs.scheduling import dispatch_registered_schedule
from django.core.management import call_command

logger = logging.getLogger(__name__)


def run_due_jobs():
    """Submit due community-base durable job intents to the django_q backend."""
    call_command("jobs_run_due")


def sweep_jobs():
    """Recover expired leases / mark exhausted community-base intents dead."""
    call_command("jobs_sweep")


# Issue #1696 phase 4: community_base.coursework registers these two package
# schedules (``coursework/pooling.py``). AISL never runs the package
# ``sync_schedules``; SCHEDULE_DEFINITIONS is authoritative, so these wrappers
# enqueue the registered schedule as a durable job intent and
# ``cb-jobs-run-due`` runs its handler.
COURSEWORK_FORM_POOLED_BATCHES_SCHEDULE = 'coursework.form_pooled_batches.every_15_minutes'
COURSEWORK_EXPIRE_POOLED_REVIEWS_SCHEDULE = 'coursework.expire_pooled_reviews.every_15_minutes'


def form_pooled_batches():
    """Enqueue the coursework sweep that forms pooled peer-review batches."""
    return dispatch_registered_schedule(
        schedule_name=COURSEWORK_FORM_POOLED_BATCHES_SCHEDULE,
    )


def expire_pooled_reviews():
    """Enqueue the coursework sweep that expires overdue pooled reviews."""
    return dispatch_registered_schedule(
        schedule_name=COURSEWORK_EXPIRE_POOLED_REVIEWS_SCHEDULE,
    )


logger.debug("community_base_jobs task wrappers imported")
