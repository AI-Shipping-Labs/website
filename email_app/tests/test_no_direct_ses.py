"""No production module may bypass Relay for SES transport."""

from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from email_app.testing import StubSESClient, local_render_backend
from email_app.tests.ses_send_guard import source_violations

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_SUPPORT_MODULES = {"email_app/testing.py", "email_app/testing_relay_transactional.py"}


def _non_test_sources():
    for path in sorted(REPO_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if {"tests", "playwright_tests", "migrations"}.intersection(relative.parts):
            continue
        if path.name.startswith("test_") or relative.as_posix() in TEST_SUPPORT_MODULES:
            continue
        yield relative.as_posix(), path


class NoDirectSesSendPathTest(SimpleTestCase):
    def test_production_sources_never_bypass_relay(self):
        violations = []
        for relative, path in _non_test_sources():
            violations.extend(source_violations(path.read_text(), relative))
        self.assertEqual(violations, [])

    def test_detector_rejects_ses_factory_and_send_variants(self):
        snippets = (
            "import boto3; boto3.client('ses')",
            "import boto3; boto3.client(service_name='sesv2')",
            "import boto3 as aws; aws.client('ses')",
            "from boto3 import client as factory; factory(service_name='ses')",
            "import boto3; session = boto3.Session(); session.client('sesv2')",
            "from boto3.session import Session as Factory; Factory().client('ses')",
            "import boto3; service = 'ses'; boto3.session.Session().client(service)",
            "import boto3; service = 'ses'; boto3.client(service); service = 's3'",
            "import boto3 as aws\ndef harmless():\n    aws = object()\naws.client('ses')",
            "import boto3; alias = service; service = 'ses'; boto3.client(alias)",
            "client.send_email()",
            "client.send_raw_email()",
            "from boto3 import client; send = client('ses').send_email; send()",
        )
        for source in snippets:
            with self.subTest(source=source):
                self.assertTrue(source_violations(source, "example.py"))

    def test_detector_allows_unrelated_aws_services(self):
        for service in ("s3", "secretsmanager", "sqs", "sts"):
            source = f"import boto3 as aws; session = aws.Session(); session.client('{service}')"
            with self.subTest(service=service):
                self.assertEqual(source_violations(source, "example.py"), [])

    def test_detector_rejects_forbidden_backend_configuration(self):
        for source in (
            "EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'",
            "COMMUNITY_BASE = {'MAIL_BACKEND': 'ses_local'}",
            "EMAIL_BACKEND = 'django_ses.SESBackend'",
        ):
            with self.subTest(source=source):
                self.assertTrue(source_violations(source, "example.py"))


class LocalRenderBackendSafetyTest(SimpleTestCase):
    @override_settings(TESTING=False)
    def test_rejects_use_outside_tests(self):
        with self.assertRaisesMessage(
            RuntimeError,
            "local_render_backend requires TESTING=True",
        ):
            with local_render_backend():
                pass

    def test_replaces_unpatched_client_factory_with_offline_stub(self):
        calls = []

        def network_client_factory():
            calls.append(True)
            raise AssertionError("network client factory must not run")

        with (
            patch(
                "community_base.mail.backends.ses_local.configured_client",
                new=network_client_factory,
            ),
            local_render_backend(),
        ):
            from community_base.mail.backends import get_backend

            self.assertIsInstance(get_backend().configured_client(), StubSESClient)

        self.assertEqual(calls, [])

    def test_preserves_existing_outer_stub(self):
        outer_stub = StubSESClient("outer-stub")
        with (
            patch(
                "community_base.mail.backends.ses_local.configured_client",
                return_value=outer_stub,
            ) as configured_client,
            local_render_backend(),
        ):
            from community_base.mail.backends import get_backend

            self.assertIs(get_backend().configured_client(), outer_stub)

        configured_client.assert_called_once_with()
