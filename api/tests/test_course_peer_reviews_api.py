"""Tests for course peer-review API endpoints and course_id exposure."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from content.models import (
    Cohort,
    Course,
    CourseProject,
    PeerReview,
    ProjectSubmission,
)
from content.models.peer_review import ProjectState
from notifications.models import Notification

User = get_user_model()


class CoursePeerReviewApiTest(TestCase):
    """Covers /api/courses/<slug>/peer-reviews and /assignments."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@example.com",
            password="pw",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="test-cli")
        cls.non_staff = User.objects.create_user(
            email="user@example.com",
            password="pw",
        )

        cls.course = Course.objects.create(
            title="AI Buildcamp",
            slug="ai-buildcamp",
            status="published",
            peer_review_enabled=True,
            peer_review_count=2,
            peer_review_deadline_days=7,
        )
        cls.draft_course = Course.objects.create(
            title="Draft Course",
            slug="draft-course",
            status="draft",
            peer_review_enabled=False,
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course,
            name="Cohort 4",
            external_key="4",
            mode="cohort",
            is_active=True,
            start_date=date(2026, 9, 14),
            end_date=date(2026, 11, 24),
        )
        now = timezone.now()
        cls.project = CourseProject.objects.create(
            course=cls.course,
            cohort=cls.cohort,
            slug="attempt-1",
            title="Project Attempt 1",
            submission_due_at=now - timedelta(hours=2),
            review_due_at=now + timedelta(days=7),
            state=ProjectState.COLLECTING_SUBMISSIONS,
        )
        cls.students = []
        for idx in range(4):
            student = User.objects.create_user(
                email=f"student{idx}@example.com",
                password="pw",
            )
            cls.students.append(student)
            ProjectSubmission.objects.create(
                user=student,
                course=cls.course,
                course_project=cls.project,
                cohort=cls.cohort,
                project_url=f"https://github.com/example/proj-{idx}",
                status="submitted",
            )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def test_requires_staff_token(self):
        self.assertEqual(
            self.client.get("/api/courses/ai-buildcamp/peer-reviews").status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                "/api/courses/ai-buildcamp/peer-reviews",
                HTTP_AUTHORIZATION="Token bad",
            ).status_code,
            401,
        )
        self.client.force_login(self.non_staff)
        self.assertEqual(
            self.client.get("/api/courses/ai-buildcamp/peer-reviews").status_code,
            401,
        )
        self.client.logout()
        self.assertEqual(
            self.client.post("/api/courses/ai-buildcamp/peer-reviews/assignments").status_code,
            401,
        )

    def test_cohorts_endpoint_exposes_course_id(self):
        resp = self.client.get("/api/courses/ai-buildcamp/cohorts", **self._auth())
        data = resp.json()
        self.assertEqual(data["course_id"], self.course.pk)
        self.assertEqual(data["course"], "ai-buildcamp")

    def test_peer_reviews_read_and_assign_are_idempotent_with_no_email(self):
        read_before = self.client.get(
            "/api/courses/ai-buildcamp/peer-reviews?cohort=4&project=attempt-1",
            **self._auth(),
        )
        before_data = read_before.json()
        self.assertEqual(before_data["course_id"], self.course.pk)
        project_before = before_data["projects"][0]
        self.assertEqual(project_before["state"], "CS")
        self.assertEqual(project_before["submissions_count"], 4)
        self.assertEqual(project_before["assignments_count"], 0)
        self.assertEqual(project_before["completed_reviews_count"], 0)
        self.assertIsNotNone(project_before["deadline"])

        mail.outbox.clear()
        assign_1 = self.client.post(
            "/api/courses/ai-buildcamp/peer-reviews/assignments",
            data='{"cohort": "4", "project": "attempt-1"}',
            content_type="application/json",
            **self._auth(),
        )
        body_1 = assign_1.json()
        self.assertEqual(body_1["course_id"], self.course.pk)
        self.assertEqual(body_1["batched"], 4)
        self.assertEqual(body_1["reviews_assigned"], 8)
        self.assertEqual(body_1["projects"][0]["state"], "PR")
        self.assertEqual(body_1["projects"][0]["state_label"], "Peer reviewing")
        self.assertEqual(body_1["projects"][0]["assignments_count"], 8)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            Notification.objects.filter(user__in=self.students).count(),
            4,
        )

        # Complete one review and verify read-back reflects completed_reviews_count.
        review = PeerReview.objects.filter(submission__course_project=self.project).first()
        review.is_complete = True
        review.save(update_fields=["is_complete"])

        read_after = self.client.get(
            "/api/courses/ai-buildcamp/peer-reviews?cohort=4&project=attempt-1",
            **self._auth(),
        )
        project_after = read_after.json()["projects"][0]
        self.assertEqual(project_after["state"], "PR")
        self.assertEqual(project_after["assignments_count"], 8)
        self.assertEqual(project_after["completed_reviews_count"], 1)
        self.assertEqual(project_after["pending_reviews_count"], 7)

        # Second call must be completely idempotent: no new reviews, no new emails/notifications.
        assign_2 = self.client.post(
            "/api/courses/ai-buildcamp/peer-reviews/assignments",
            data='{"cohort": "4", "project": "attempt-1"}',
            content_type="application/json",
            **self._auth(),
        )
        body_2 = assign_2.json()
        self.assertEqual(body_2["batched"], 0)
        self.assertEqual(body_2["reviews_assigned"], 0)
        self.assertEqual(PeerReview.objects.filter(submission__course_project=self.project).count(), 8)
        self.assertEqual(
            Notification.objects.filter(user__in=self.students).count(),
            4,
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_error_responses_for_disabled_and_unknown_targets(self):
        disabled = self.client.post(
            "/api/courses/draft-course/peer-reviews/assignments",
            **self._auth(),
        )
        self.assertEqual(disabled.status_code, 422)
        self.assertEqual(disabled.json()["code"], "peer_review_disabled")

        bad_cohort = self.client.get(
            "/api/courses/ai-buildcamp/peer-reviews?cohort=999",
            **self._auth(),
        )
        self.assertEqual(bad_cohort.status_code, 404)
        self.assertEqual(bad_cohort.json()["code"], "unknown_cohort")

        bad_project = self.client.post(
            "/api/courses/ai-buildcamp/peer-reviews/assignments",
            data='{"project": "nope"}',
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(bad_project.status_code, 404)
        self.assertEqual(bad_project.json()["code"], "unknown_project")
