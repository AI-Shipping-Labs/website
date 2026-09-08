"""django-q entry points for the community-base durable jobs housekeeping.

`setup_schedules` registers these on the cluster (names ``cb-jobs-run-due``
and ``cb-jobs-sweep``) so the package's durable intents run and recover on
the same worker that already runs every other recurring job (plan A1.1).
Each wrapper is a thin `call_command` shim so the q payload stays a static
dotted path and the management command owns its own arguments/limits.
"""

import logging

from django.core.management import call_command

logger = logging.getLogger(__name__)


def run_due_jobs():
    """Submit due community-base durable job intents to the django_q backend."""
    call_command("jobs_run_due")


def sweep_jobs():
    """Recover expired leases / mark exhausted community-base intents dead."""
    call_command("jobs_sweep")


logger.debug("community_base_jobs task wrappers imported")
