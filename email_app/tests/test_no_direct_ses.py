"""Production outbound email remains Relay-owned after legacy removal."""

from pathlib import Path

from django.test import SimpleTestCase

from email_app.tests.outbound_email_guard import source_violations

REPO_ROOT = Path(__file__).resolve().parents[2]
REMOVED_PATHS = (
    "templates/email_app/base_email.html",
    "email_app/services/email_service.py", "email_app/services/email_rendering.py",
    "email_app/template_catalog.py",
)


def _production_sources():
    for path in sorted(REPO_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if {"tests", "playwright_tests", "migrations"}.intersection(relative.parts):
            continue
        if path.name.startswith("test_"):
            continue
        yield relative.as_posix(), path


class OutboundEmailGuardTest(SimpleTestCase):
    def test_removed_local_sources_stay_absent(self):
        present = [path for path in REMOVED_PATHS if (REPO_ROOT / path).exists()]
        present.extend(
            path.relative_to(REPO_ROOT).as_posix()
            for path in (REPO_ROOT / "email_app/email_templates").glob("*.md")
        )
        self.assertEqual(present, [])

    def test_production_sources_have_no_legacy_outbound_path(self):
        violations = []
        for relative, path in _production_sources():
            violations.extend(source_violations(path.read_text(), relative))
        self.assertEqual(violations, [])

    def test_detector_rejects_each_legacy_boundary(self):
        snippets = (
            "import boto3; boto3.client('ses')", "client.send_email()",
            "EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'",
            "COMMUNITY_BASE = {'MAIL_BACKEND': 'ses_local'}",
            "SETTING = 'SES_FROM_EMAIL'",
            "from email_app.services.email_rendering import render_template",
        )
        for source in snippets:
            with self.subTest(source=source):
                self.assertTrue(source_violations(source, "example.py"))
