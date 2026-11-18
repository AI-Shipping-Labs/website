"""Tests for the member peer-review API endpoints."""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import MemberAPIKey, Token
from content.models import Cohort, Course, CourseProject, PeerReview, ProjectSubmission
from content.models.peer_review import ProjectState

User = get_user_model()

CRITERIA = [
    {
        "id": "readme",
        "title": "README quality",
        "description": "Clear setup instructions.",
        "type": "scale",
        "options": [
            {"points": 0, "text": "Missing"},
            {"points": 1, "text": "Basic"},
            {"points": 2, "text": "Clear"},
        ],
    },
    {
        "id": "extras",
        "title": "Bonus items",
        "description": "Optional extras.",
        "type": "checklist",
        "options": [
            {"points": 1, "text": "Tests included"},
            {"points": 2, "text": "Deployed live"},
        ],
    },
]


@tag("core")
class MemberPeerReviewsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reviewer = User.objects.create_user(
            email="reviewer@test.com", password="pw",
        )
        cls.author = User.objects.create_user(
            email="author@test.com", password="pw",
        )
        cls.other_reviewer = User.objects.create_user(
            email="other-reviewer@test.com", password="pw",
        )
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.non_staff = User.objects.create_user(
            email="nonstaff@test.com", password="pw", is_staff=True,
        )

        _, cls.member_key = MemberAPIKey.create_for_user(
            user=cls.reviewer, name="reviewer key",
        )
        _, cls.readonly_key = MemberAPIKey.create_for_user(
            user=cls.reviewer, name="read only", scopes=["peer_reviews:read"],
        )
        _, cls.other_domain_key = MemberAPIKey.create_for_user(
            user=cls.reviewer, name="plans only", scopes=["plans:read"],
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="admin")
        cls.non_staff_token = Token.objects.create(
            user=cls.non_staff, name="not-staff",
        )
        User.objects.filter(pk=cls.non_staff.pk).update(is_staff=False)

        cls.course = Course.objects.create(
            slug="ai-engineering",
            title="AI Engineering",
            status="published",
            peer_review_enabled=True,
            project_passing_score=2,
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course,
            external_key="1",
            name="Cohort 1",
            mode="cohort",
        )
        now = timezone.now()
        cls.project = CourseProject.objects.create(
            course=cls.course,
            cohort=cls.cohort,
            slug="project-1",
            title="Project 1",
            submission_due_at=now - timedelta(days=2),
            review_due_at=now + timedelta(days=5),
            state=ProjectState.PEER_REVIEWING,
            evaluation_criteria=CRITERIA,
        )
        cls.submission = ProjectSubmission.objects.create(
            user=cls.author,
            course=cls.course,
            course_project=cls.project,
            cohort=cls.cohort,
            project_url="https://github.com/example/repo",
            commit_id="abc1234",
            description="My project",
            learning_in_public_links=["https://example.com/post"],
            time_spent=4.5,
            status="in_review",
        )
        cls.review = PeerReview.objects.create(
            submission=cls.submission,
            reviewer=cls.reviewer,
        )
        cls.other_review = PeerReview.objects.create(
            submission=cls.submission,
            reviewer=cls.other_reviewer,
        )
        cls.staff_review = PeerReview.objects.create(
            submission=cls.submission,
            reviewer=cls.staff,
        )

    def _auth(self, key):
        return {"HTTP_AUTHORIZATION": f"Token {key}"}

    def test_list_returns_only_own_assigned_reviews_without_author_pii(self):
        response = self.client.get(
            "/member-api/v1/peer-reviews",
            **self._auth(self.member_key),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["pagination"]["total"], 1)
        item = body["peer_reviews"][0]
        self.assertEqual(item["id"], self.review.pk)
        self.assertEqual(item["course"]["slug"], "ai-engineering")
        self.assertEqual(item["project"]["slug"], "project-1")
        self.assertEqual(
            item["submission"]["project_url"],
            "https://github.com/example/repo",
        )
        self.assertEqual(item["submission"]["commit_id"], "abc1234")
        self.assertTrue(item["has_criteria"])
        self.assertNotIn("author@test.com", json.dumps(body))
        self.assertNotIn("user_id", item["submission"])

    def test_list_filters_by_course_project_status_and_validates_params(self):
        ok = self.client.get(
            "/member-api/v1/peer-reviews?course=ai-engineering"
            "&project=project-1&status=pending",
            **self._auth(self.member_key),
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(len(ok.json()["peer_reviews"]), 1)

        completed = self.client.get(
            "/member-api/v1/peer-reviews?status=completed",
            **self._auth(self.member_key),
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["peer_reviews"], [])

        bad_status = self.client.get(
            "/member-api/v1/peer-reviews?status=bogus",
            **self._auth(self.member_key),
        )
        self.assertEqual(bad_status.status_code, 422)
        self.assertEqual(bad_status.json()["code"], "invalid_query_param")

        bad_page = self.client.get(
            "/member-api/v1/peer-reviews?page=0",
            **self._auth(self.member_key),
        )
        self.assertEqual(bad_page.status_code, 422)

    def test_detail_returns_submission_and_criteria_and_hides_others(self):
        response = self.client.get(
            f"/member-api/v1/peer-reviews/{self.review.pk}",
            **self._auth(self.member_key),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], self.review.pk)
        self.assertEqual(data["passing_score"], 2)
        self.assertEqual(data["criteria_max_points"], {"core": 2, "bonus": 3})
        self.assertEqual(len(data["criteria"]), 2)
        self.assertEqual(data["criteria"][0]["id"], "readme")
        self.assertEqual(data["criteria"][0]["options"][2]["index"], 2)
        self.assertEqual(data["criteria"][0]["options"][2]["points"], 2)

        forbidden = self.client.get(
            f"/member-api/v1/peer-reviews/{self.other_review.pk}",
            **self._auth(self.member_key),
        )
        self.assertEqual(forbidden.status_code, 404)
        self.assertEqual(forbidden.json()["code"], "peer_review_not_found")

    def test_submit_draft_and_update_review_with_criteria(self):
        draft_resp = self.client.patch(
            f"/member-api/v1/peer-reviews/{self.review.pk}",
            data=json.dumps({
                "draft": True,
                "criteria_scores": {"readme": 1},
                "feedback": "WIP note",
            }),
            content_type="application/json",
            **self._auth(self.member_key),
        )
        self.assertEqual(draft_resp.status_code, 200)
        self.assertFalse(draft_resp.json()["is_complete"])
        self.assertEqual(draft_resp.json()["total_points"], 1)

        submit_resp = self.client.put(
            f"/member-api/v1/peer-reviews/{self.review.pk}",
            data=json.dumps({
                "criteria_scores": {"readme": 2, "extras": [0, 1]},
                "feedback": "Great work!",
            }),
            content_type="application/json",
            **self._auth(self.member_key),
        )
        self.assertEqual(submit_resp.status_code, 200)
        body = submit_resp.json()
        self.assertTrue(body["is_complete"])
        self.assertIsNotNone(body["completed_at"])
        self.assertEqual(body["total_points"], 5)
        self.assertEqual(body["feedback"], "Great work!")

        update_resp = self.client.patch(
            f"/member-api/v1/peer-reviews/{self.review.pk}",
            data=json.dumps({"feedback": "Updated feedback"}),
            content_type="application/json",
            **self._auth(self.member_key),
        )
        self.assertEqual(update_resp.status_code, 200)
        self.assertEqual(update_resp.json()["feedback"], "Updated feedback")
        self.assertEqual(update_resp.json()["total_points"], 5)

    def test_submit_marks_submission_review_complete_when_all_done(self):
        self.other_review.is_complete = True
        self.other_review.criteria_scores = {"readme": 2, "extras": []}
        self.other_review.save()
        self.staff_review.is_complete = True
        self.staff_review.criteria_scores = {"readme": 2, "extras": []}
        self.staff_review.save()

        resp = self.client.put(
            f"/member-api/v1/peer-reviews/{self.review.pk}",
            data=json.dumps({"criteria_scores": {"readme": 2}}),
            content_type="application/json",
            **self._auth(self.member_key),
        )
        self.assertEqual(resp.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, "review_complete")

    def test_validation_and_closed_window_errors(self):
        missing = self.client.put(
            f"/member-api/v1/peer-reviews/{self.review.pk}",
            data=json.dumps({"criteria_scores": {}}),
            content_type="application/json",
            **self._auth(self.member_key),
        )
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(missing.json()["code"], "validation_error")

        unknown = self.client.put(
            f"/member-api/v1/peer-reviews/{self.review.pk}",
            data=json.dumps({"criteria_scores": {"readme": 1, "nope": 0}}),
            content_type="application/json",
            **self._auth(self.member_key),
        )
        self.assertEqual(unknown.status_code, 422)

        CourseProject.objects.filter(pk=self.project.pk).update(
            state=ProjectState.COMPLETED,
        )
        closed = self.client.put(
            f"/member-api/v1/peer-reviews/{self.review.pk}",
            data=json.dumps({"criteria_scores": {"readme": 2}}),
            content_type="application/json",
            **self._auth(self.member_key),
        )
        self.assertEqual(closed.status_code, 409)
        self.assertEqual(closed.json()["code"], "review_window_closed")

    def test_scopes_and_admin_api_key_access(self):
        no_read = self.client.get(
            "/member-api/v1/peer-reviews",
            **self._auth(self.other_domain_key),
        )
        self.assertEqual(no_read.status_code, 401)
        self.assertEqual(no_read.json()["code"], "insufficient_scope")

        no_write = self.client.put(
            f"/member-api/v1/peer-reviews/{self.review.pk}",
            data=json.dumps({"criteria_scores": {"readme": 1}}),
            content_type="application/json",
            **self._auth(self.readonly_key),
        )
        self.assertEqual(no_write.status_code, 401)
        self.assertEqual(no_write.json()["code"], "insufficient_scope")

        non_staff = self.client.get(
            "/member-api/v1/peer-reviews",
            **self._auth(self.non_staff_token.key),
        )
        self.assertEqual(non_staff.status_code, 401)
        self.assertEqual(non_staff.json()["code"], "invalid_member_api_key")

        admin_list = self.client.get(
            "/member-api/v1/peer-reviews",
            **self._auth(self.staff_token.key),
        )
        self.assertEqual(admin_list.status_code, 200)
        self.assertEqual(
            admin_list.json()["peer_reviews"][0]["id"],
            self.staff_review.pk,
        )

        admin_submit = self.client.put(
            f"/member-api/v1/peer-reviews/{self.staff_review.pk}",
            data=json.dumps({
                "criteria_scores": {"readme": 2, "extras": [0]},
                "feedback": "Admin review",
            }),
            content_type="application/json",
            **self._auth(self.staff_token.key),
        )
        self.assertEqual(admin_submit.status_code, 200)
        self.assertTrue(admin_submit.json()["is_complete"])
        self.assertEqual(admin_submit.json()["total_points"], 3)

    def test_legacy_review_without_criteria(self):
        legacy_sub = ProjectSubmission.objects.create(
            user=self.other_reviewer,
            course=self.course,
            project_url="https://github.com/example/legacy",
            status="in_review",
        )
        legacy_review = PeerReview.objects.create(
            submission=legacy_sub,
            reviewer=self.reviewer,
        )
        bad = self.client.put(
            f"/member-api/v1/peer-reviews/{legacy_review.pk}",
            data=json.dumps({"score": 4}),
            content_type="application/json",
            **self._auth(self.member_key),
        )
        self.assertEqual(bad.status_code, 422)

        ok = self.client.put(
            f"/member-api/v1/peer-reviews/{legacy_review.pk}",
            data=json.dumps({"score": 5, "feedback": "Solid legacy project"}),
            content_type="application/json",
            **self._auth(self.member_key),
        )
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["is_complete"])
        self.assertEqual(ok.json()["score"], 5)

        resubmit = self.client.put(
            f"/member-api/v1/peer-reviews/{legacy_review.pk}",
            data=json.dumps({"score": 4, "feedback": "Edit"}),
            content_type="application/json",
            **self._auth(self.member_key),
        )
        self.assertEqual(resubmit.status_code, 409)
        self.assertEqual(resubmit.json()["code"], "review_already_submitted")
