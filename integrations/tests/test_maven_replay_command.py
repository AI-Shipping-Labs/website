"""Tests for the replay_maven_event management command (issue #960)."""

import io
import json
from io import StringIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

from content.models import Cohort, CohortEnrollment, Course, CourseAccess
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from payments.models import TierOverride

User = get_user_model()


SECRET = "replay-probe-secret"

PATCH_OPEN = "integrations.management.commands.replay_maven_event.build_opener"
PATCH_SECRET = (
    "integrations.management.commands.replay_maven_event.maven_shared_secret"
)


def _ok_response(body=b'{"status": "onboarded"}'):
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = 200
    response.read.return_value = body
    return response


def _set_secret():
    IntegrationSetting.objects.update_or_create(
        key="MAVEN_WEBHOOK_SHARED_SECRET", defaults={"value": SECRET}
    )
    from integrations.config import clear_config_cache

    clear_config_cache()


def _enable():
    IntegrationSetting.objects.update_or_create(
        key="MAVEN_ENROLLMENT_ENABLED", defaults={"value": "true"}
    )
    from integrations.config import clear_config_cache

    clear_config_cache()


class ReplayMavenEventTest(TestCase):
    def setUp(self):
        _enable()
        from integrations.config import clear_config_cache

        self.addCleanup(clear_config_cache)

    def test_dry_run_makes_no_writes(self):
        out = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.enrolled",
            "--email", "dryrun@test.com",
            "--course", "LLM Zoomcamp",
            "--cohort", "Spring 2026",
            "--dry-run",
            stdout=out,
        )
        self.assertFalse(User.objects.filter(email="dryrun@test.com").exists())
        self.assertFalse(TierOverride.objects.filter(user__email="dryrun@test.com").exists())
        self.assertFalse(MavenEnrollmentEvent.objects.exists())
        output = out.getvalue()
        self.assertIn("DRY RUN", output)
        self.assertIn("Would", output)

    @patch("integrations.services.maven.send_package_mail")
    def test_real_run_then_idempotent(self, package_mail):
        # Issue #1659: already_processed now also requires ``enrollment`` to
        # be terminal, so this needs a resolvable maven_course_key/external_key.
        course = Course.objects.create(
            title="LLM Zoomcamp", slug="llm-zoomcamp-replay-idempotent",
            maven_course_key="llm zoomcamp",
        )
        Cohort.objects.create(
            course=course, external_key="spring 2026", name="Spring 2026",
            start_date="2026-01-01", end_date="2026-03-01",
        )
        out = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.enrolled",
            "--email", "real@test.com",
            "--course", "LLM Zoomcamp",
            "--cohort", "Spring 2026",
            stdout=out,
        )
        self.assertTrue(User.objects.filter(email="real@test.com").exists())
        self.assertIn("onboarded", out.getvalue())

        out2 = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.enrolled",
            "--email", "real@test.com",
            "--course", "LLM Zoomcamp",
            "--cohort", "Spring 2026",
            stdout=out2,
        )
        self.assertIn("already_processed", out2.getvalue())
        self.assertEqual(
            TierOverride.objects.filter(
                user__email="real@test.com", is_active=True
            ).count(),
            1,
        )
        self.assertEqual(package_mail.call_count, 1)

    @patch("integrations.services.maven.send_package_mail")
    def test_backfill_replay_runs_the_enrollment_step_like_a_live_webhook(self, package_mail):
        # Issue #1659: cohort-4 members enrolled before this feature shipped
        # are backfilled through this exact replay path.
        course = Course.objects.create(
            title="Buildcamp", slug="buildcamp-replay-1659",
            maven_course_key="buildcamp",
        )
        cohort = Cohort.objects.create(
            course=course, external_key="cohort 4", name="Cohort 4",
            start_date="2026-09-21", end_date="2026-11-22",
        )
        out = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.enrolled",
            "--email", "backfill-1659@test.com",
            "--course", "Buildcamp",
            "--cohort", "Cohort 4",
            stdout=out,
        )

        occurrence = MavenEnrollmentEvent.objects.get(email="backfill-1659@test.com")
        self.assertEqual(occurrence.enrollment_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertTrue(
            CourseAccess.objects.filter(
                user__email="backfill-1659@test.com", course=course,
            ).exists()
        )
        self.assertTrue(
            CohortEnrollment.objects.filter(
                cohort=cohort, user__email="backfill-1659@test.com",
            ).exists()
        )

    @patch("community.services.staff_notifications.notify_maven_cohort_removal")
    def test_removal_replay(self, notify):
        out = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.removed",
            "--email", "rem@test.com",
            "--course", "LLM Zoomcamp",
            "--cohort", "C1",
            stdout=out,
        )
        self.assertIn("removal_notified", out.getvalue())
        notify.assert_called_once()

    def test_http_mode_posts_signed_payload_and_writes_nothing(self):
        _set_secret()
        opener = MagicMock()
        opener.open.return_value = _ok_response()
        out = StringIO()
        with patch(PATCH_OPEN, return_value=opener) as build_opener:
            call_command(
                "replay_maven_event",
                "--event", "user_cohort.enrolled",
                "--email", "http-probe@test.com",
                "--course", "LLM Zoomcamp",
                "--cohort", "Spring 2026",
                "--http",
                stdout=out,
            )
        request = opener.open.call_args.args[0]
        self.assertEqual(
            request.full_url, "https://aishippinglabs.com/api/webhooks/maven"
        )
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("X-maven-secret"), SECRET)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(
            json.loads(request.data.decode("utf-8"))["event"],
            "user_cohort.enrolled",
        )
        self.assertIn("HTTP 200", out.getvalue())
        self.assertIn("onboarded", out.getvalue())
        self.assertNotIn(SECRET, out.getvalue())
        self.assertFalse(User.objects.filter(email="http-probe@test.com").exists())
        self.assertFalse(
            MavenEnrollmentEvent.objects.filter(email="http-probe@test.com").exists()
        )
        self.assertEqual(build_opener.call_count, 1)

    def test_http_sends_envelope_payload_unnormalized(self):
        _set_secret()
        opener = MagicMock()
        opener.open.return_value = _ok_response()
        out = StringIO()
        with patch(PATCH_OPEN, return_value=opener):
            call_command(
                "replay_maven_event",
                "--payload",
                '{"event": "user_cohort.enrolled",'
                ' "data": {"email": "envelope@test.com",'
                ' "course": "C", "cohort": "K"}}',
                "--http",
                "--url", "https://example.invalid/api/webhooks/maven",
                stdout=out,
            )
        request = opener.open.call_args.args[0]
        self.assertEqual(
            request.full_url, "https://example.invalid/api/webhooks/maven"
        )
        body = json.loads(request.data.decode("utf-8"))
        self.assertIn("data", body)
        self.assertNotIn(SECRET, out.getvalue())

    def test_http_mode_without_secret_fails_loudly(self):
        opener = MagicMock()
        with patch(PATCH_SECRET, return_value=""):
            with patch(PATCH_OPEN, return_value=opener):
                with self.assertRaises(CommandError) as ctx:
                    call_command(
                        "replay_maven_event",
                        "--event", "user_cohort.enrolled",
                        "--email", "nosecret@test.com",
                        "--course", "LLM Zoomcamp",
                        "--cohort", "Spring 2026",
                        "--http",
                    )
        self.assertIn("MAVEN_WEBHOOK_SHARED_SECRET", str(ctx.exception))
        opener.open.assert_not_called()

    def test_http_rejects_dry_run(self):
        _set_secret()
        opener = MagicMock()
        with patch(PATCH_OPEN, return_value=opener):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "replay_maven_event",
                    "--event", "user_cohort.enrolled",
                    "--email", "dryhttp@test.com",
                    "--course", "LLM Zoomcamp",
                    "--cohort", "Spring 2026",
                    "--dry-run", "--http",
                )
        self.assertIn("--dry-run", str(ctx.exception))
        opener.open.assert_not_called()

    def test_http_rejects_insecure_url(self):
        _set_secret()
        opener = MagicMock()
        with patch(PATCH_OPEN, return_value=opener):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "replay_maven_event",
                    "--event", "user_cohort.enrolled",
                    "--email", "insecure@test.com",
                    "--course", "LLM Zoomcamp",
                    "--cohort", "Spring 2026",
                    "--http", "--url", "http://evil.example/api/webhooks/maven",
                )
        self.assertIn("https", str(ctx.exception))
        opener.open.assert_not_called()

    def test_http_allows_localhost_http_url(self):
        _set_secret()
        opener = MagicMock()
        opener.open.return_value = _ok_response()
        with patch(PATCH_OPEN, return_value=opener):
            call_command(
                "replay_maven_event",
                "--event", "user_cohort.enrolled",
                "--email", "localhost@test.com",
                "--course", "LLM Zoomcamp",
                "--cohort", "Spring 2026",
                "--http", "--url", "http://127.0.0.1:8000/api/webhooks/maven",
                stdout=StringIO(),
            )
        request = opener.open.call_args.args[0]
        self.assertEqual(
            request.full_url, "http://127.0.0.1:8000/api/webhooks/maven"
        )

    def test_http_error_surfaces_code_without_the_secret(self):
        _set_secret()
        opener = MagicMock()
        opener.open.side_effect = HTTPError(
            "https://aishippinglabs.com/api/webhooks/maven",
            403,
            "Forbidden",
            {},
            io.BytesIO(b'{"error": "forbidden"}'),
        )
        with patch(PATCH_OPEN, return_value=opener):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "replay_maven_event",
                    "--event", "user_cohort.enrolled",
                    "--email", "forbidden@test.com",
                    "--course", "LLM Zoomcamp",
                    "--cohort", "Spring 2026",
                    "--http",
                )
        self.assertIn("HTTP 403", str(ctx.exception))
        self.assertIn("may not match", str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_http_refused_redirect_gets_hint(self):
        _set_secret()
        opener = MagicMock()
        opener.open.side_effect = HTTPError(
            "https://aishippinglabs.com/api/webhooks/maven",
            302,
            "Found",
            {},
            io.BytesIO(b""),
        )
        with patch(PATCH_OPEN, return_value=opener):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "replay_maven_event",
                    "--event", "user_cohort.enrolled",
                    "--email", "redirected@test.com",
                    "--course", "LLM Zoomcamp",
                    "--cohort", "Spring 2026",
                    "--http",
                )
        self.assertIn("redirect refused", str(ctx.exception))

    def test_http_unreachable_reports_command_error(self):
        _set_secret()
        opener = MagicMock()
        opener.open.side_effect = URLError("connection refused")
        with patch(PATCH_OPEN, return_value=opener):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "replay_maven_event",
                    "--event", "user_cohort.enrolled",
                    "--email", "unreachable@test.com",
                    "--course", "LLM Zoomcamp",
                    "--cohort", "Spring 2026",
                    "--http",
                )
        self.assertIn("Endpoint unreachable", str(ctx.exception))

    def test_http_disabled_status_fails_loudly(self):
        _set_secret()
        opener = MagicMock()
        opener.open.return_value = _ok_response(b'{"status": "disabled"}')
        with patch(PATCH_OPEN, return_value=opener):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "replay_maven_event",
                    "--event", "user_cohort.enrolled",
                    "--email", "disabled@test.com",
                    "--course", "LLM Zoomcamp",
                    "--cohort", "Spring 2026",
                    "--http",
                )
        self.assertIn("disabled", str(ctx.exception))
