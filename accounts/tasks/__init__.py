"""Background tasks owned by the accounts app.

This package re-exports the existing import-related tasks from
``imports.py`` plus the unverified-account lifecycle tasks (issue #452):

- ``purge_unverified_users`` — daily hard-delete of expired unverified
  email signups that have no related activity.
- ``remind_unverified_users`` — daily one-shot reminder email sent ~24h
  before the verification window closes.
"""

from .imports import (
    SCHEDULE_NAME_BY_SOURCE,
    SCHEDULED_IMPORT_SOURCES,
    run_import_batch_task,
    run_scheduled_import,
)
from .purge_unverified_users import purge_unverified_users
from .remind_onboarding import remind_onboarding_incomplete
from .remind_unverified_users import remind_unverified_users

__all__ = [
    "run_import_batch_task",
    "run_scheduled_import",
    "SCHEDULED_IMPORT_SOURCES",
    "SCHEDULE_NAME_BY_SOURCE",
    "purge_unverified_users",
    "remind_unverified_users",
    "remind_onboarding_incomplete",
]
