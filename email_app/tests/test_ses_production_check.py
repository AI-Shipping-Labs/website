"""Tests for the ``email_app.E001`` production SES system check (issue #521).

The check (``email_app/checks.py::check_ses_enabled_in_production``)
fires when ``DEBUG=False`` and ``SES_ENABLED`` is not truthy, so an
operator who deploys to prod without setting ``SES_ENABLED=true`` gets
a non-zero exit from ``manage.py check`` instead of a silent
transactional-email outage. These tests assert each branch of the
truth table plus the registration-and-integration guarantees: the
check is wired into Django's check registry by ``EmailAppConfig.ready``
and ``manage.py check`` actually exits non-zero in the misconfigured
case.
"""

from io import StringIO

from django.conf import settings
from django.core import checks
from django.core.checks import registry as checks_registry
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.test import SimpleTestCase, override_settings

from email_app.checks import check_ses_enabled_in_production


class SesProductionCheckTruthTableTest(SimpleTestCase):
    """Every cell of the (TESTING, DEBUG, SES_ENABLED) truth table.

    All tests force ``TESTING=False`` because ``manage.py test``
    propagates ``TESTING=True`` to the loaded settings module, and the
    check short-circuits on that flag (see the docstring of
    ``check_ses_enabled_in_production``). The override_settings
    decorator below restores it after each test.
    """

    @override_settings(TESTING=False, DEBUG=True, SES_ENABLED=False)
    def test_check_passes_when_debug_true_and_ses_disabled(self):
        # Local dev path: DEBUG=True, kill-switch off. Must be silent —
        # operators do not get an error during a normal development
        # workflow.
        self.assertEqual(check_ses_enabled_in_production(None), [])

    @override_settings(TESTING=False, DEBUG=True, SES_ENABLED=True)
    def test_check_passes_when_debug_true_and_ses_enabled(self):
        # Edge case: developer locally flipped SES_ENABLED on. Still
        # not a production-like deploy because DEBUG is on, so we stay
        # silent.
        self.assertEqual(check_ses_enabled_in_production(None), [])

    @override_settings(TESTING=False, DEBUG=False, SES_ENABLED=True)
    def test_check_passes_when_ses_enabled_in_prod(self):
        # The healthy production state: DEBUG=False, SES_ENABLED=True.
        # No errors expected.
        self.assertEqual(check_ses_enabled_in_production(None), [])

    @override_settings(TESTING=False, DEBUG=False, SES_ENABLED=False)
    def test_check_fails_when_ses_disabled_in_prod(self):
        # The exact misconfiguration this issue is preventing. The
        # check must return one error with the documented id and a
        # message that names the affected features so the operator
        # knows what they just broke.
        errors = check_ses_enabled_in_production(None)
        self.assertEqual(len(errors), 1)
        error = errors[0]
        self.assertEqual(error.id, "email_app.E001")
        self.assertEqual(error.level, checks.ERROR)
        self.assertIn("SES_ENABLED=true", error.msg)
        # The message should name every silently broken feature so the
        # operator who reads it understands the blast radius.
        for feature in ("registration", "password reset", "newsletter", "event"):
            self.assertIn(feature, error.msg)
        self.assertIsNotNone(error.hint)
        self.assertIn("_docs/configuration.md", error.hint)

    @override_settings(TESTING=True, DEBUG=False, SES_ENABLED=False)
    def test_check_passes_when_testing_flag_set(self):
        # ``manage.py test`` flips DEBUG to False internally before
        # running system checks. Without the TESTING short-circuit the
        # check would fire on every test invocation. This is the
        # branch that keeps the test runner silent.
        self.assertEqual(check_ses_enabled_in_production(None), [])

    def test_check_fails_when_ses_setting_missing_entirely(self):
        # If a future refactor accidentally drops the SES_ENABLED
        # setting from website/settings.py, the check still has to
        # fire — getattr with default=False is the safety net.
        with override_settings(TESTING=False, DEBUG=False):
            had_attr = hasattr(settings, "SES_ENABLED")
            original = getattr(settings, "SES_ENABLED", None)
            try:
                if had_attr:
                    del settings.SES_ENABLED
                errors = check_ses_enabled_in_production(None)
            finally:
                if had_attr:
                    settings.SES_ENABLED = original
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "email_app.E001")


class SesProductionCheckRegistrationTest(SimpleTestCase):
    """The check must be wired into Django's system-check registry."""

    def test_check_is_registered(self):
        # ``EmailAppConfig.ready`` imports email_app.checks which runs
        # the @register decorator. If that wiring breaks the check
        # never fires at startup; this test is the canary.
        registered = checks_registry.registry.get_checks()
        self.assertIn(check_ses_enabled_in_production, registered)


class SesProductionCheckCommandTest(SimpleTestCase):
    """End-to-end: ``manage.py check`` exits non-zero in a misconfigured prod."""

    @override_settings(TESTING=False, DEBUG=False, SES_ENABLED=False)
    def test_manage_check_command_raises_in_prod_misconfig(self):
        # Django's ``check`` command raises ``SystemCheckError`` when
        # an Error-level check fires. Deploy pipelines see this as a
        # non-zero exit code, which is the contract this issue is
        # establishing.
        stderr = StringIO()
        with self.assertRaises(SystemCheckError) as ctx:
            call_command("check", stdout=StringIO(), stderr=stderr)
        # The error message surfaced by ``manage.py check`` should
        # contain the check id so the operator can grep for it.
        self.assertIn("email_app.E001", str(ctx.exception))

    @override_settings(TESTING=False, DEBUG=False, SES_ENABLED=True)
    def test_manage_check_command_passes_when_ses_enabled(self):
        # The healthy prod state must NOT raise.
        call_command("check", stdout=StringIO(), stderr=StringIO())

    def test_manage_check_command_passes_in_test_environment(self):
        # ``manage.py test`` propagates TESTING=True to the settings
        # module, which short-circuits the check. A plain ``check``
        # call with no overrides must succeed because we are running
        # under the test runner right now.
        call_command("check", stdout=StringIO(), stderr=StringIO())


class SesProductionCheckSilencedTest(SimpleTestCase):
    """``SILENCED_SYSTEM_CHECKS`` is the documented escape hatch.

    Regression guard for the Playwright E2E suite (``playwright_tests/
    conftest.py::_start_django_server``). The Playwright session fixture
    deliberately sets ``SES_ENABLED=False`` so no real emails go out
    during E2E runs, and ``pytest-django`` defaults ``DEBUG=False``.
    Without ``SILENCED_SYSTEM_CHECKS = ['email_app.E001']``, the
    ``runserver --noreload`` thread would raise ``SystemCheckError`` at
    startup, never bind port 8765, and break every Playwright test at
    fixture setup. If somebody removes the silence in the future, the
    test below pinpoints the breakage as a one-line config issue
    instead of a 678-test cascade.
    """

    @override_settings(
        TESTING=False,
        DEBUG=False,
        SES_ENABLED=False,
        SILENCED_SYSTEM_CHECKS=['email_app.E001'],
    )
    def test_silenced_system_check_does_not_raise(self):
        # Even with the misconfiguration that would normally trigger
        # email_app.E001, ``manage.py check`` must succeed when the
        # check id is listed in SILENCED_SYSTEM_CHECKS — that is the
        # contract the Playwright conftest depends on.
        call_command("check", stdout=StringIO(), stderr=StringIO())
