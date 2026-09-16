"""Issue #1651 guard: ``EmailService`` is a DeprecationWarning shim.

A1.2 slice 5 reduced ``email_app/services/email_service.py`` to a shim
that warns on construction — a ``DeprecationWarning``, never an error —
while the recorded exempt producers keep working unchanged:

- ``email_app/tasks/send_campaign.py`` and ``studio/views/campaigns.py``:
  the campaign-family transport, retired by A6.3.
- ``accounts/services/privacy_workflow.py``: ``prepare_template`` /
  ``send_prepared``; the package ``send`` cannot model its redacted
  recipient (recorded at slice 1 acceptance in ``_docs/configuration.md``).

This module holds the protections:

1. the shim warns on construction, so a new non-exempt caller is
   visible the moment one exists;
2. a static AST scan proves the non-test ``EmailService`` inventory is
   exactly the recorded exemptions. Adding a caller anywhere else fails
   the scan; removing an exemption without updating the recorded
   inventory fails it too, so the list cannot silently rot.

The calendar lifecycle mail (``events`` raw-SES path) and the
workshop-ready / recording-ready notifications (the synchronous
``ses_transport`` path) are part of the proof: they own their transport
directly and must never reference ``EmailService``.
"""

import ast
import warnings
from pathlib import Path

from django.test import TestCase

from email_app.services.email_service import EmailService

REPO_ROOT = Path(__file__).resolve().parents[2]

# The recorded exemptions (issue #1651, _docs/configuration.md). The shim
# itself is listed so the inventory diff reads as callers-only.
EXEMPT_EMAILSERVICE_FILES = frozenset(
    {
        "email_app/services/email_service.py",  # the shim itself
        "email_app/tasks/send_campaign.py",
        "studio/views/campaigns.py",
        "accounts/services/privacy_workflow.py",
    }
)

# Site-owned transport paths that replaced the shim entirely. The scan
# proves none of them touches ``EmailService``.
NON_SHIM_SEND_PATHS = (
    # Calendar lifecycle mail: raw MIME with .ics parts via boto3.
    "events/services/registration_email.py",
    # Workshop-ready / recording-ready notifications: synchronous
    # email_app.services.ses_transport.send_ses_email.
    "events/services/workshop_ready_notification.py",
    "events/services/recording_ready_notification.py",
)

# Public and private surfaces the exempt producers (and the tests that
# borrow them) still call. A cleanup that drops one of these breaks the
# recorded exemptions, so the shim contract pins them by name.
EXEMPT_PRODUCER_SURFACE = (
    "send",
    "send_rendered",
    "prepare_rendered",
    "send_prepared",
    "prepare_template",
    "render_html_email",
    "render_markdown_email",
    "render_plain_text_email",
    "_send_ses",
    "_build_unsubscribe_url",
)

# Module-level names consumed outside the shim by production code
# (email_app.hooks, email_app.package_mail, studio template editor) and
# by borrowed tests. These must stay importable from the shim module.
SHIM_MODULE_SURFACE = (
    "EMAIL_TYPES_WITHOUT_VERIFY_FOOTER",
    "VERIFY_FOOTER_TOKEN_EXPIRY_HOURS",
    "UNSUBSCRIBED_AT_SEND",
    "TEMPLATES_DIR",
    "DEFAULT_WELCOME_REPLY_TO_EMAIL",
    "WELCOME_REPLY_TO_KEY",
    "EmailServiceError",
    "EmailTransportOutcomeUnknown",
    "RenderedEmailSendResult",
    "PreparedRenderedEmail",
)

_SKIP_DIRS = frozenset({".venv", "node_modules", "staticfiles", "htmlcov"})


def _is_test_path(relative_path):
    """Mirror the AC grep's ``| grep -v tests`` scope plus test files."""
    parts = relative_path.parts
    if "tests" in parts or parts[0] == "playwright_tests":
        return True
    return relative_path.name.startswith("test_") or relative_path.name == "conftest.py"


def _is_skipped_path(relative_path):
    return any(part in _SKIP_DIRS or part.startswith(".") for part in relative_path.parts[:-1])


def _references_emailservice(tree):
    """True when the AST references the exact name ``EmailService``.

    Identifier-exact: ``EmailServiceError`` and docstring/comment
    mentions do not match, mirroring code usage rather than raw grep.
    A ``class EmailService`` definition counts too (the shim itself).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "EmailService":
            return True
        if isinstance(node, ast.Name) and node.id == "EmailService":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "EmailService":
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "EmailService" or alias.name.endswith(".EmailService"):
                    return True
                if alias.asname == "EmailService":
                    return True
    return False


def _non_test_emailservice_inventory():
    """Return the non-test files whose code references ``EmailService``."""
    flagged = set()
    for path in sorted(REPO_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        if _is_test_path(relative) or _is_skipped_path(relative):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _references_emailservice(tree):
            flagged.add(relative.as_posix())
    return flagged


class EmailServiceShimWarningTest(TestCase):
    """The shim warns on construction — visibly, but never fatally."""

    def test_construction_emits_deprecation_warning_not_error(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            service = EmailService()

        deprecations = [
            warning for warning in caught if issubclass(warning.category, DeprecationWarning)
        ]
        self.assertEqual(len(deprecations), 1)
        self.assertIs(deprecations[0].category, DeprecationWarning)
        self.assertIn("send_package_mail", str(deprecations[0].message))
        # A warning, not an error: construction returned an instance and
        # the exempt producers keep working unchanged.
        self.assertIsInstance(service, EmailService)

    def test_warning_names_the_guard_test(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            EmailService()

        message = str(caught[0].message)
        self.assertIn("test_email_service_shim_1651", message)


class EmailServiceShimBehaviorTest(TestCase):
    """The shim still delegates: the exempt producers' surface works."""

    def test_exempt_producer_surface_remains_callable(self):
        for name in EXEMPT_PRODUCER_SURFACE:
            self.assertTrue(callable(getattr(EmailService, name)), name)

    def test_shim_module_surface_remains_importable(self):
        from email_app.services import email_service as shim

        for name in SHIM_MODULE_SURFACE:
            self.assertTrue(hasattr(shim, name), name)

    def test_rendering_still_delegates_to_email_rendering(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            service = EmailService()

        full_html = service.render_markdown_email("Subject", "Hello **world**")
        self.assertIn("<strong>world</strong>", full_html)
        self.assertIn("Subject", full_html)

        plain = service.render_plain_text_email("Hello **world**")
        self.assertIn("Hello world", plain)


class ExemptInventoryGuardTest(TestCase):
    """The non-test ``EmailService`` inventory is exactly the exemptions."""

    def test_non_test_inventory_is_exactly_the_recorded_exemptions(self):
        flagged = _non_test_emailservice_inventory()
        unexpected = flagged - EXEMPT_EMAILSERVICE_FILES
        missing = EXEMPT_EMAILSERVICE_FILES - flagged

        self.assertEqual(
            unexpected,
            set(),
            "New non-test EmailService caller(s) detected. New callers must "
            "use email_app.package_mail.send_package_mail; only a recorded "
            "exemption (see EXEMPT_EMAILSERVICE_FILES and the send-paths "
            "paragraph in _docs/configuration.md) may construct EmailService.",
        )
        self.assertEqual(
            missing,
            set(),
            "Recorded exemption(s) no longer reference EmailService. Update "
            "EXEMPT_EMAILSERVICE_FILES and the send-paths paragraph in "
            "_docs/configuration.md to match the converted inventory.",
        )


class SiteOwnedTransportGuardTest(TestCase):
    """The events raw-SES and ses_transport paths never use the shim."""

    def test_calendar_and_ready_notifications_never_reference_emailservice(self):
        for relative in NON_SHIM_SEND_PATHS:
            path = REPO_ROOT / relative
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            self.assertFalse(_references_emailservice(tree), relative)
