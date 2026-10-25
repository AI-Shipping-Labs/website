"""Production outbound email remains Relay-owned after legacy removal."""

from pathlib import Path

from django.test import SimpleTestCase

from email_app.tests.outbound_email_guard import source_violations

REPO_ROOT = Path(__file__).resolve().parents[2]
REMOVED_PATHS = (
    "templates/email_app/base_email.html",
    "email_app/services/email_service.py", "email_app/services/email_rendering.py",
    "email_app/services/email_errors.py", "email_app/services/relay_transport.py",
    "email_app/template_catalog.py",
    "email_app/models/template_override.py",
    "email_app/management/commands/sync_email_templates.py",
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
        present = []
        for path in REMOVED_PATHS:
            if (REPO_ROOT / path).exists():
                present.append(path)
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
            "import boto3 as aws; service = 'ses'; aws.client(service)",
            "from boto3 import client as make_client; make_client(service_name='sesv2')",
            "from django_ses import SESBackend; SESBackend()",
            "from django.conf import settings; FROM = settings.SES_FROM_EMAIL",
            "import smtplib as smtp; smtp.SMTP_SSL('smtp.example.com')",
            "from django.core.mail.backends.smtp import EmailBackend; EmailBackend()",
            "EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'",
            "COMMUNITY_BASE = {'MAIL_BACKEND': 'ses_local'}",
            "SETTING = 'SES_FROM_EMAIL'",
            "from email_app.services.email_rendering import render_template",
        )
        for source in snippets:
            with self.subTest(source=source):
                self.assertTrue(source_violations(source, "example.py"))

    def test_detector_allows_s3_and_ses_event_ingress(self):
        snippets = (
            "import boto3; boto3.client('s3')",
            "message_id = event.get('mail', {}).get('messageId')",
            "topic = 'ses-bounces'",
            "from django.core.mail import EmailMessage; EmailMessage().send()",
        )
        for source in snippets:
            with self.subTest(source=source):
                self.assertEqual(source_violations(source, "example.py"), [])
