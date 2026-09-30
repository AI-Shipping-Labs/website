"""Maven removals that arrive with different raw keys than the enrollment.

Production cohort 4 enrolled as ``'AI Engineering Buildcamp: From RAG to
Agents'`` / ``'4/11'`` and was removed as ``from-rag-to-agents`` / ``4``.
The differing ``identity_hash`` made the removal record a separate row and
leave the enrollment active (occurrences 8 and 17 for user 5196).
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from community.models import CommunityAuditLog
from content.models import Cohort, CohortEnrollment, Course, CourseAccess
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import handle_maven_event, reapply_removal
from integrations.services.maven_matching import (
    REMOVAL_MATCH_AUDIT_ACTION,
    SPLIT_REPAIR_AUDIT_ACTION,
    split_pairs,
)

User = get_user_model()

NOTIFY = "community.services.staff_notifications.notify_maven_cohort_removal"
COURSE_TITLE = "AI Engineering Buildcamp: From RAG to Agents"
COURSE_KEY = "from-rag-to-agents"
LEGACY_COURSE_KEY = COURSE_TITLE.lower()
ACTIVE = MavenEnrollmentEvent.LIFECYCLE_ACTIVE
REMOVED = MavenEnrollmentEvent.LIFECYCLE_REMOVED


class SplitFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title=COURSE_TITLE, slug="buildcamp-split", maven_course_key=COURSE_KEY,
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course, external_key="4", name="Cohort 4",
            start_date="2026-09-07", end_date="2026-11-11",
        )

    def setUp(self):
        self.user = User.objects.create_user(email="student@example.com")
        CourseAccess.objects.create(user=self.user, course=self.course, access_type="granted")
        CohortEnrollment.objects.create(cohort=self.cohort, user=self.user)

    def _occurrence(self, pk_hint, *, lifecycle, course_key, cohort_key, age_days, course="", cohort=""):
        row = MavenEnrollmentEvent.objects.create(
            dedupe_key=f"split-{pk_hint}", identity_hash=f"hash-{pk_hint}",
            user=self.user, email=self.user.email, course=course or course_key,
            cohort=cohort or cohort_key, course_key=course_key, cohort_key=cohort_key,
            lifecycle=lifecycle, event_type="user_cohort.enrolled",
            removal_status=MavenEnrollmentEvent.STEP_SKIPPED,
        )
        created = timezone.now() - timedelta(days=age_days)
        removed_at = None
        if lifecycle == REMOVED:
            removed_at = created
        MavenEnrollmentEvent.objects.filter(pk=row.pk).update(created_at=created, removed_at=removed_at)
        row.refresh_from_db()
        return row

    def _legacy_enrollment(self, *, age_days=23):
        return self._occurrence(
            "enrolled", lifecycle=ACTIVE, course_key=LEGACY_COURSE_KEY,
            cohort_key="4/11", course=COURSE_TITLE, age_days=age_days,
        )

    def _split_removal(self, *, age_days=16):
        return self._occurrence(
            "removed", lifecycle=REMOVED, course_key=COURSE_KEY, cohort_key="4", age_days=age_days,
        )

    def _has_course_access(self):
        return CourseAccess.objects.filter(user=self.user, course=self.course).exists()


@patch(NOTIFY)
class RemovalMatchesEnrollmentByResolutionTest(SplitFixtureMixin, TestCase):
    def _remove(self, cohort="4"):
        return handle_maven_event({
            "event": "user_cohort.removed", "email": "Student@Example.com",
            "course": COURSE_KEY, "cohort": cohort,
        })

    def test_removal_with_new_keys_closes_the_legacy_enrollment(self, notify):
        enrolled = self._legacy_enrollment()

        result = self._remove()

        self.assertEqual(result.occurrence_id, enrolled.pk)
        self.assertEqual(MavenEnrollmentEvent.objects.count(), 1)
        enrolled.refresh_from_db()
        self.assertEqual(enrolled.lifecycle, REMOVED)
        self.assertEqual((enrolled.course_key, enrolled.cohort_key), (COURSE_KEY, "4"))
        self.assertFalse(self._has_course_access())
        self.assertFalse(CohortEnrollment.objects.filter(user=self.user).exists())
        self.assertTrue(CommunityAuditLog.objects.filter(action=REMOVAL_MATCH_AUDIT_ACTION).exists())

    def test_redelivered_removal_does_not_create_a_row(self, notify):
        self._legacy_enrollment()
        self._remove()

        result = self._remove()

        self.assertEqual(result.status, "already_processed")
        self.assertEqual(MavenEnrollmentEvent.objects.count(), 1)

    def test_removal_from_another_cohort_leaves_the_enrollment_active(self, notify):
        enrolled = self._legacy_enrollment()

        self._remove(cohort="5")

        enrolled.refresh_from_db()
        self.assertEqual(enrolled.lifecycle, ACTIVE)
        self.assertEqual(MavenEnrollmentEvent.objects.count(), 2)


@patch(NOTIFY)
class ReapplyRepairsSplitPairTest(SplitFixtureMixin, TestCase):
    def test_reapply_closes_the_active_sibling_and_revokes_course_access(self, notify):
        enrolled = self._legacy_enrollment()
        removed = self._split_removal()

        _result, actions = reapply_removal(removed)

        enrolled.refresh_from_db()
        self.assertEqual(enrolled.lifecycle, REMOVED)
        self.assertEqual(enrolled.removed_at, removed.removed_at)
        self.assertIn(f"Closed split active occurrence {enrolled.pk}.", actions)
        audit = CommunityAuditLog.objects.get(action=SPLIT_REPAIR_AUDIT_ACTION)
        self.assertIn(f"occurrence={enrolled.pk}", audit.details)
        self.assertFalse(self._has_course_access())

    def test_repeated_reapply_is_idempotent(self, notify):
        self._legacy_enrollment()
        removed = self._split_removal()
        reapply_removal(removed)

        result, actions = reapply_removal(removed)

        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertEqual(CommunityAuditLog.objects.filter(action=SPLIT_REPAIR_AUDIT_ACTION).count(), 1)
        self.assertFalse(any(action.startswith("Closed split") for action in actions))

    def test_re_enrollment_after_the_removal_is_not_a_sibling(self, notify):
        removed = self._split_removal()
        re_enrolled = self._occurrence(
            "again", lifecycle=ACTIVE, course_key=COURSE_KEY, cohort_key="4", age_days=1,
        )

        reapply_removal(removed)

        re_enrolled.refresh_from_db()
        self.assertEqual(re_enrolled.lifecycle, ACTIVE)
        self.assertTrue(self._has_course_access())


class SplitPairsApiTest(SplitFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(email="ops@example.com", is_staff=True)
        cls.token = Token.objects.create(user=cls.staff, name="ops")

    def _get(self, **headers):
        return self.client.get("/api/integrations/maven/occurrences/split-pairs", **headers)

    def test_lists_split_pair_but_not_other_cohorts_or_later_re_enrollment(self):
        enrolled = self._legacy_enrollment()
        removed = self._split_removal()
        self._occurrence("other", lifecycle=ACTIVE, course_key=COURSE_KEY, cohort_key="5", age_days=20)
        self._occurrence("again", lifecycle=ACTIVE, course_key=COURSE_KEY, cohort_key="4", age_days=1)

        response = self._get(HTTP_AUTHORIZATION=f"Token {self.token.key}")

        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["pairs"][0]["active"]["id"], enrolled.pk)
        self.assertEqual(body["pairs"][0]["removed"]["id"], removed.pk)

    def test_repaired_pair_is_no_longer_listed(self):
        self._legacy_enrollment()
        removed = self._split_removal()

        with patch(NOTIFY):
            reapply_removal(removed)

        self.assertEqual(split_pairs(), [])

    def test_requires_staff_token(self):
        self.assertEqual(self._get().status_code, 401)

