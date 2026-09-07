"""PostgreSQL regression coverage for the Maven step lock (issue #1574).

Production incident: every ``user_cohort.enrolled`` delivery returned an
HTML 500. ``_run_step`` locked the occurrence with a bare
``select_for_update()`` on a queryset that also ``select_related("user")``.
``MavenEnrollmentEvent.user`` is nullable, so that compiles to a LEFT OUTER
JOIN and PostgreSQL rejects it with

    NotSupportedError: FOR UPDATE cannot be applied to the nullable side of
    an outer join

raised on the first statement of the step, above the ``try`` that records a
failed step — so it escaped the view entirely instead of returning the
``processing_failed`` JSON.

SQLite has ``has_select_for_update = False`` and silently drops the clause,
which is why ~14,800 green SQLite tests never saw it. These tests therefore
only mean anything on PostgreSQL and are tagged for the CI
``PostgreSQL 16 Verification`` job (``manage.py test --tag=postgresql``).
"""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase, tag

from accounts.models import TierOverride
from community.models import CommunityAuditLog
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from payments.models import Tier

User = get_user_model()

WEBHOOK_URL = "/api/webhooks/maven"
SECRET = "postgres-lock-secret-1574"

# The exact shape and widths of the production backfill that broke: a
# 44-character course label and a terse cohort label.
COURSE = "AI Engineering Buildcamp: From RAG to Agents"
COHORT = "4/11"


def _enable():
    for key, value in (
        ("MAVEN_ENROLLMENT_ENABLED", "true"),
        ("MAVEN_WEBHOOK_SHARED_SECRET", SECRET),
    ):
        IntegrationSetting.objects.update_or_create(key=key, defaults={"value": value})


def _clear_config_cache():
    from integrations.config import clear_config_cache

    clear_config_cache()


@tag("core", "postgresql")
@patch(
    "integrations.services.maven._invite_to_slack",
    lambda user, actions: (MavenEnrollmentEvent.STEP_SUCCEEDED, ""),
)
class MavenStepLockPostgresTest(TestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest(
                "FOR UPDATE over a nullable outer join is a PostgreSQL-only "
                "failure; SQLite drops the locking clause entirely"
            )
        _enable()
        _clear_config_cache()
        self.addCleanup(_clear_config_cache)
        self.main = Tier.objects.get(slug="main")

    def _post(self, body):
        return self.client.post(
            WEBHOOK_URL,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_X_MAVEN_SECRET=SECRET,
        )

    def _enrolled_payload(self, email):
        return {
            "type": "user_cohort.enrolled",
            "email": email,
            "course": {"name": COURSE},
            "cohort": {"name": COHORT},
            "user": {"first_name": "Emil", "last_name": "Lee"},
        }

    def test_step_lock_query_runs_on_postgres(self):
        """The exact locking read ``_run_step`` issues must execute."""
        with transaction.atomic():
            list(
                MavenEnrollmentEvent.objects.select_for_update(of=("self",))
                .select_related("user")
                .all()[:1]
            )

    @patch("integrations.services.maven.EmailService")
    def test_enrolled_webhook_grants_override_and_returns_json(self, email_service):
        response = self._post(self._enrolled_payload("Emil.Lee@stern.nyu.edu"))

        # Before the fix this raised NotSupportedError out of the view and
        # Django rendered its HTML error page, so a JSON body is itself the
        # contract under test: the handler completed and reported an outcome.
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"status": "onboarded"})

        user = User.objects.get(email="emil.lee@stern.nyu.edu")
        self.assertEqual(user.first_name, "Emil")

        override = TierOverride.objects.get(user=user, is_active=True)
        self.assertEqual(override.override_tier, self.main)

        occurrence = MavenEnrollmentEvent.objects.get(user=user)
        self.assertEqual(
            occurrence.override_status, MavenEnrollmentEvent.STEP_SUCCEEDED
        )
        self.assertEqual(occurrence.override_attempts, 1)
        self.assertEqual(occurrence.override_error, "")
        self.assertEqual(occurrence.course, COURSE)
        self.assertEqual(occurrence.cohort, COHORT)
        self.assertTrue(
            CommunityAuditLog.objects.filter(
                user=user, action="maven_enrollment_override"
            ).exists()
        )
        self.assertEqual(
            email_service.return_value.send.call_args.args[1], "maven_welcome"
        )

    def test_removal_webhook_runs_its_step(self):
        """``_handle_removed`` reaches ``_run_step`` through the same lock."""
        with patch(
            "community.services.staff_notifications.notify_maven_cohort_removal"
        ):
            response = self._post(
                {
                    "type": "user_cohort.removed",
                    "email": "never-seen@test.com",
                    "course": {"name": COURSE},
                    "cohort": {"name": COHORT},
                }
            )

        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"status": "removal_notified"})
        occurrence = MavenEnrollmentEvent.objects.get(email="never-seen@test.com")
        # The occurrence has no user at all — the nullable FK the outer join
        # comes from — and the step still has to run.
        self.assertIsNone(occurrence.user_id)
        self.assertEqual(occurrence.removal_attempts, 1)
        self.assertNotEqual(
            occurrence.removal_status, MavenEnrollmentEvent.STEP_PENDING
        )

    @patch("integrations.services.maven.EmailService")
    def test_redelivery_resumes_the_existing_occurrence(self, email_service):
        """A crashed delivery is resumable: attempts were never burned."""
        payload = self._enrolled_payload("resume@test.com")

        occurrence = MavenEnrollmentEvent.objects.create(
            dedupe_key="stalled-1574",
            identity_hash=self._identity(payload),
            user=User.objects.create_user(email="resume@test.com", password="x"),
            email="resume@test.com",
            course=COURSE,
            cohort=COHORT,
            course_key=COURSE.lower(),
            cohort_key=COHORT.lower(),
            event_type="user_cohort.enrolled",
            outcome=MavenEnrollmentEvent.OUTCOME_ONBOARDED,
            welcome_eligible=True,
            account_created=True,
            override_status=MavenEnrollmentEvent.STEP_PENDING,
            override_attempts=0,
            notification_status=MavenEnrollmentEvent.STEP_PENDING,
            slack_status=MavenEnrollmentEvent.STEP_PENDING,
            welcome_status=MavenEnrollmentEvent.STEP_PENDING,
        )

        response = self._post(payload)

        # The redelivery resumes the SAME occurrence and reports it complete
        # rather than onboarding a second time.
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"status": "already_processed"})
        self.assertEqual(MavenEnrollmentEvent.objects.count(), 1)
        occurrence.refresh_from_db()
        self.assertEqual(
            occurrence.override_status, MavenEnrollmentEvent.STEP_SUCCEEDED
        )
        self.assertEqual(occurrence.override_attempts, 1)
        self.assertTrue(
            TierOverride.objects.filter(user=occurrence.user, is_active=True).exists()
        )

    @patch("integrations.services.maven.EmailService")
    def test_retry_job_resumes_pending_occurrences(self, email_service):
        """``retry_maven_enrollment_steps`` must not raise on PostgreSQL."""
        from jobs.tasks.cleanup import retry_maven_enrollment_steps

        user = User.objects.create_user(email="retry@test.com", password="x")
        occurrence = MavenEnrollmentEvent.objects.create(
            dedupe_key="stalled-1574-retry",
            identity_hash="a" * 64,
            user=user,
            email="retry@test.com",
            course=COURSE,
            cohort=COHORT,
            event_type="user_cohort.enrolled",
            outcome=MavenEnrollmentEvent.OUTCOME_ONBOARDED,
            welcome_eligible=True,
            override_status=MavenEnrollmentEvent.STEP_PENDING,
            notification_status=MavenEnrollmentEvent.STEP_PENDING,
            slack_status=MavenEnrollmentEvent.STEP_PENDING,
            welcome_status=MavenEnrollmentEvent.STEP_PENDING,
        )

        result = retry_maven_enrollment_steps()

        self.assertEqual(result["processed"], 1)
        occurrence.refresh_from_db()
        self.assertEqual(
            occurrence.override_status, MavenEnrollmentEvent.STEP_SUCCEEDED
        )
        self.assertTrue(
            TierOverride.objects.filter(user=user, is_active=True).exists()
        )

    @staticmethod
    def _identity(payload):
        from integrations.services.maven import _identity

        return _identity(payload["email"], COURSE.lower(), COHORT.lower())
