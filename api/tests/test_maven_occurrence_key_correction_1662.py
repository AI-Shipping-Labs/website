"""Staff key-correction endpoint for Maven occurrences (issue #1662).

Maven renamed its payload identifiers mid-cohort (2026-09-08): early
occurrences carry the full course title and a "4/11" cohort key while
later ones carry the slug keys that course.yaml declares. The correction
endpoint lets staff point an occurrence's stored keys at the resolvable
values and then force-retry the enrollment step -- the roster-replay
path for members whose grant was skipped while the keys were unknown.
"""

import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from accounts.models import Token
from community.models import CommunityAuditLog
from content.models import Cohort, CohortEnrollment, Course, CourseAccess
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import _identity, retry_occurrence_step

User = get_user_model()
URL = "/api/integrations/maven/occurrences/{pk}/keys"


class MavenOccurrenceKeyCorrectionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="keyfix-staff@example.com", password="pw", is_staff=True
        )
        cls.member = User.objects.create_user(
            email="keyfix-member@example.com", password="pw"
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="keyfix")
        cls.non_staff_token = Token(
            key="keyfix-member-token",
            user=cls.member,
            name="keyfix-member",
        )
        Token.objects.bulk_create([cls.non_staff_token])
        cls.student = User.objects.create_user(
            email="legacy-enrollee@example.com", password="pw"
        )

    def setUp(self):
        self.client = Client()

    def auth(self, key=None):
        return {"HTTP_AUTHORIZATION": f"Token {key or self.staff_token.key}"}

    def _occurrence(self, **fields):
        defaults = {
            "dedupe_key": "legacy-keys",
            "identity_hash": "legacy-keys",
            "user": self.student,
            "email": self.student.email,
            "course": "Buildcamp",
            "cohort": "Cohort 4",
            "course_key": "ai engineering buildcamp: from rag to agents",
            "cohort_key": "4/11",
            "event_type": "user_cohort.enrolled",
            "lifecycle": MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            "outcome": MavenEnrollmentEvent.OUTCOME_ONBOARDED,
            "enrollment_status": MavenEnrollmentEvent.STEP_FAILED,
        }
        defaults.update(fields)
        return MavenEnrollmentEvent.objects.create(**defaults)

    def test_corrects_keys_recomputes_identity_and_audits(self):
        occurrence = self._occurrence()

        response = self.client.patch(
            URL.format(pk=occurrence.pk),
            data=json.dumps(
                {"course_key": "from-rag-to-agents", "cohort_key": "4"}
            ),
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 200)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.course_key, "from-rag-to-agents")
        self.assertEqual(occurrence.cohort_key, "4")
        self.assertEqual(
            occurrence.identity_hash,
            _identity(occurrence.email, "from-rag-to-agents", "4"),
        )
        # The dedupe key is built from display labels, not resolution keys,
        # so correcting keys must never disturb it.
        self.assertEqual(occurrence.dedupe_key, "legacy-keys")
        self.assertTrue(
            CommunityAuditLog.objects.filter(
                action="maven_occurrence_keys_corrected",
                user=self.student,
            ).exists()
        )

    def test_corrected_keys_make_enrollment_retry_grant_access(self):
        course = Course.objects.create(
            title="Buildcamp", slug="buildcamp-keys",
            maven_course_key="from-rag-to-agents",
        )
        cohort = Cohort.objects.create(
            course=course, external_key="4",
            name="Cohort 4", start_date="2026-09-21", end_date="2026-11-22",
        )
        occurrence = self._occurrence()
        self.client.patch(
            URL.format(pk=occurrence.pk),
            data=json.dumps(
                {"course_key": "from-rag-to-agents", "cohort_key": "4"}
            ),
            content_type="application/json",
            **self.auth(),
        )

        result = retry_occurrence_step(occurrence, "enrollment")

        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertTrue(
            CourseAccess.objects.filter(user=self.student, course=course).exists()
        )
        self.assertTrue(
            CohortEnrollment.objects.filter(
                user=self.student, cohort=cohort
            ).exists()
        )

    def test_blank_or_missing_keys_are_rejected(self):
        occurrence = self._occurrence()

        for body in (
            {},
            {"course_key": "from-rag-to-agents"},
            {"course_key": "from-rag-to-agents", "cohort_key": "   "},
        ):
            response = self.client.patch(
                URL.format(pk=occurrence.pk),
                data=json.dumps(body),
                content_type="application/json",
                **self.auth(),
            )
            self.assertEqual(response.status_code, 422, body)

        occurrence.refresh_from_db()
        self.assertEqual(
            occurrence.course_key, "ai engineering buildcamp: from rag to agents"
        )

    def test_non_object_body_and_unknown_occurrence(self):
        response = self.client.patch(
            URL.format(pk=1),
            data=json.dumps({"course_key": "a", "cohort_key": "b"}),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 404)

        occurrence = self._occurrence()
        response = self.client.patch(
            URL.format(pk=occurrence.pk),
            data='["not", "an", "object"]',
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 422)

    def test_requires_staff_token(self):
        occurrence = self._occurrence()
        response = self.client.patch(
            URL.format(pk=occurrence.pk),
            data=json.dumps({"course_key": "a", "cohort_key": "b"}),
            content_type="application/json",
            **self.auth(self.non_staff_token.key),
        )
        self.assertIn(response.status_code, (401, 403))
