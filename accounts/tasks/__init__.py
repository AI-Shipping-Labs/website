"""Background tasks owned by the accounts app.

This package re-exports the existing import-related tasks from
``imports.py`` plus the unverified-account lifecycle tasks (issue #452):

- ``purge_unverified_users`` — daily hard-delete of expired unverified
  email signups that have no related activity.
- ``remind_unverified_users`` — daily one-shot reminder email sent ~24h
  before the verification window closes.

``mail_admins`` and ``run_import_batch`` are bound here at the package
level so existing tests that patch ``accounts.tasks.mail_admins`` and
``accounts.tasks.run_import_batch`` keep working after the move from
``accounts/tasks.py`` to a package layout.
"""

from django.core.mail import mail_admins  # noqa: F401  re-exported for tests

from accounts.services.import_users import run_import_batch  # noqa: F401  re-exported

from .imports import (
    SCHEDULE_NAME_BY_SOURCE,
    SCHEDULED_IMPORT_SOURCES,
    run_import_batch_task,
    run_scheduled_import,
)
from .purge_unverified_users import purge_unverified_users
from .remind_unverified_users import remind_unverified_users

__all__ = [
    "mail_admins",
    "run_import_batch",
    "run_import_batch_task",
    "run_scheduled_import",
    "SCHEDULED_IMPORT_SOURCES",
    "SCHEDULE_NAME_BY_SOURCE",
    "purge_unverified_users",
    "remind_unverified_users",
]
