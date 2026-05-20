"""Regression guard for issue #716.

Django-Q resolves a ``Schedule.func`` dotted path via ``pydoc.locate``
at fire time. When a package contains a submodule whose name matches
a function re-exported at the package level (e.g.
``accounts.tasks.purge_unverified_users`` — both a submodule and a
re-exported function), ``pydoc.locate`` returns the SUBMODULE and the
worker raises ``TypeError: 'module' object is not callable`` when it
tries to call it.

This test walks every ``Schedule`` row that ``setup_schedules``
registers and asserts the resolved target is a function (not a module).
Anyone who adds a future schedule entry with an ambiguous dotted path
fails CI immediately instead of discovering the crash in the prod
worker log.
"""

import inspect
import pydoc
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django_q.models import Schedule


class ScheduleFuncResolvesToFunctionTest(TestCase):
    """Every ``Schedule.func`` must resolve to a function (issue #716)."""

    def test_every_schedule_func_resolves_to_a_function(self):
        """Walk every row in ``Schedule`` and resolve its dotted ``func``.

        Failure message names the offending ``Schedule.name`` and
        ``func`` so the cause is obvious in CI.
        """
        call_command('setup_schedules', stdout=StringIO())

        rows = list(Schedule.objects.all())
        # Sanity: the test is meaningless if no schedules exist.
        self.assertGreater(
            len(rows), 0,
            'setup_schedules produced no Schedule rows; nothing to verify',
        )

        for row in rows:
            target = pydoc.locate(row.func)

            self.assertIsNotNone(
                target,
                f"Schedule {row.name!r} func={row.func!r} did not resolve "
                f"(pydoc.locate returned None). Django-Q will fail to fire "
                f"this schedule.",
            )

            self.assertFalse(
                inspect.ismodule(target),
                f"Schedule {row.name!r} func={row.func!r} resolves to a "
                f"MODULE, not a function. Django-Q will raise "
                f"TypeError: 'module' object is not callable at fire time. "
                f"Use the fully-qualified dotted path to the function "
                f"(e.g. 'pkg.mod.func' instead of 'pkg.mod' when 'mod' "
                f"re-exports a function of the same name).",
            )

            self.assertTrue(
                inspect.isfunction(target),
                f"Schedule {row.name!r} func={row.func!r} resolves to "
                f"{type(target).__name__}, not a function. Django-Q "
                f"expects a callable function reference.",
            )
