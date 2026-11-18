"""Tests for the homework and project-attempt state endpoints.

``GET|PATCH /api/courses/<slug>/homework/<id>`` and
``GET|PATCH /api/courses/<slug>/projects/<id>``.
"""

import json
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from content.models import Cohort, Course, CourseProject, Homework
from content.models.homework import HomeworkState
from content.models.peer_review import ProjectState

User = get_user_model()


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@example.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="orin-cli")
        cls.member = User.objects.create_user(email="member@example.com", password="pw")
        cls.course = Course.objects.create(
            title="AI Buildcamp", slug="ai-buildcamp", status="published",
            peer_review_enabled=True,
        )
        cls.other_course = Course.objects.create(
            title="Other", slug="other-course", status="published",
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course, name="Cohort 4", external_key="4", mode="cohort",
            is_active=True, start_date=date(2026, 9, 14), end_date=date(2026, 11, 24),
        )
        cls.other_cohort = Cohort.objects.create(
            course=cls.other_course, name="Other 1", external_key="1", mode="cohort",
            is_active=True, start_date=date(2026, 9, 14), end_date=date(2026, 11, 24),
        )
        cls.homework = Homework.objects.create(
            cohort=cls.cohort, slug="week-2-homework", title="Week 2 homework",
            state=HomeworkState.OPEN,
        )
        cls.foreign_homework = Homework.objects.create(
            cohort=cls.other_cohort, slug="week-1", title="Week 1",
            state=HomeworkState.OPEN,
        )
        now = timezone.now()
        cls.project = CourseProject.objects.create(
            course=cls.course, cohort=cls.cohort, slug="attempt-1",
            title="Project Attempt 1",
            submission_due_at=now - timedelta(hours=2),
            review_due_at=now + timedelta(days=7),
            state=ProjectState.COLLECTING_SUBMISSIONS,
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _patch(self, url, body, **extra):
        return self.client.patch(
            url, data=json.dumps(body), content_type="application/json",
            **{**self._auth(), **extra},
        )


class HomeworkStateApiTest(_Base):
    def _url(self, homework=None, course="ai-buildcamp"):
        homework = homework or self.homework
        return f"/api/courses/{course}/homework/{homework.pk}"

    def test_requires_staff_token(self):
        self.assertEqual(self.client.get(self._url()).status_code, 401)
        self.client.force_login(self.member)
        response = self.client.patch(
            self._url(), data='{"state": "CL"}', content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.homework.refresh_from_db()
        self.assertEqual(self.homework.state, HomeworkState.OPEN)

    def test_get_returns_state(self):
        data = self.client.get(self._url(), **self._auth()).json()
        self.assertEqual(data["id"], self.homework.pk)
        self.assertEqual(data["state"], "OP")
        self.assertEqual(data["state_label"], "Open")
        self.assertEqual(data["cohort"], "4")
        self.assertTrue(data["accepting_submissions"])

    def test_close_by_code_persists_and_stops_submissions(self):
        response = self._patch(self._url(), {"state": "CL"})
        data = response.json()
        self.assertEqual(
            (data["previous_state"], data["state"], data["changed"]), ("OP", "CL", True),
        )
        self.assertFalse(data["accepting_submissions"])
        self.homework.refresh_from_db()
        self.assertEqual(self.homework.state, HomeworkState.CLOSED)
        self.assertFalse(self.homework.is_accepting_submissions)

    def test_names_are_case_insensitive_and_any_transition_is_allowed(self):
        # CMP parity: no transition table -- OP -> SC -> OP -> CL all apply.
        for value, code in (("Scored", "SC"), ("open", "OP"), ("CLOSED", "CL")):
            data = self._patch(self._url(), {"state": value}).json()
            self.assertEqual(data["state"], code)
        self.homework.refresh_from_db()
        self.assertEqual(self.homework.state, HomeworkState.CLOSED)

    def test_same_state_is_noop(self):
        data = self._patch(self._url(), {"state": "open"}).json()
        self.assertEqual(
            (data["previous_state"], data["state"], data["changed"]), ("OP", "OP", False),
        )

    def test_invalid_state_is_400_with_valid_states(self):
        response = self._patch(self._url(), {"state": "done"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_homework_state")
        self.assertEqual(response.json()["valid_states"], ["CL", "OP", "SC"])
        self.homework.refresh_from_db()
        self.assertEqual(self.homework.state, HomeworkState.OPEN)

    def test_missing_state_is_400(self):
        response = self._patch(self._url(), {"status": "CL"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "missing_state")

    def test_non_object_body_is_400(self):
        response = self._patch(self._url(), ["CL"])
        self.assertEqual(response.status_code, 400)

    def test_unknown_course_and_homework_are_404(self):
        self.assertEqual(
            self._patch(self._url(course="nope"), {"state": "CL"}).json()["code"],
            "unknown_course",
        )
        missing = f"/api/courses/ai-buildcamp/homework/{self.homework.pk + 999}"
        self.assertEqual(self._patch(missing, {"state": "CL"}).json()["code"], "unknown_homework")

    def test_homework_of_another_course_is_404_and_untouched(self):
        response = self._patch(self._url(homework=self.foreign_homework), {"state": "CL"})
        self.assertEqual(response.status_code, 404)
        self.foreign_homework.refresh_from_db()
        self.assertEqual(self.foreign_homework.state, HomeworkState.OPEN)

    def test_other_methods_are_405(self):
        response = self.client.post(self._url(), data="{}", content_type="application/json", **self._auth())
        self.assertEqual(response.status_code, 405)

    def test_state_change_sends_no_mail(self):
        mail.outbox.clear()
        self._patch(self._url(), {"state": "CL"})
        self.assertEqual(mail.outbox, [])


class ProjectStateApiTest(_Base):
    def _url(self, project=None, course="ai-buildcamp"):
        project = project or self.project
        return f"/api/courses/{course}/projects/{project.pk}"

    def test_requires_staff_token(self):
        self.assertEqual(self.client.get(self._url()).status_code, 401)

    def test_get_returns_state(self):
        data = self.client.get(self._url(), **self._auth()).json()
        self.assertEqual(data["slug"], "attempt-1")
        self.assertEqual(data["state"], "CS")
        self.assertEqual(data["state_label"], "Collecting submissions")
        self.assertFalse(data["self_paced"])

    def test_close_project(self):
        data = self._patch(self._url(), {"state": "closed"}).json()
        self.assertEqual(
            (data["previous_state"], data["state"], data["changed"]), ("CS", "CL", True),
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.state, ProjectState.CLOSED)

    def test_complete_requires_peer_reviewing(self):
        response = self._patch(self._url(), {"state": "CO"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "invalid_transition")
        self.project.refresh_from_db()
        self.assertEqual(self.project.state, ProjectState.COLLECTING_SUBMISSIONS)

        self._patch(self._url(), {"state": "peer_reviewing"})
        data = self._patch(self._url(), {"state": "completed"}).json()
        self.assertEqual((data["previous_state"], data["state"]), ("PR", "CO"))

    def test_invalid_state_is_400_with_valid_states(self):
        response = self._patch(self._url(), {"state": "scored"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_project_state")
        self.assertEqual(response.json()["valid_states"], ["CL", "CS", "PR", "CO"])

    def test_project_of_another_course_is_404(self):
        response = self._patch(self._url(course="other-course"), {"state": "CL"})
        self.assertEqual(response.json()["code"], "unknown_project")
        self.project.refresh_from_db()
        self.assertEqual(self.project.state, ProjectState.COLLECTING_SUBMISSIONS)

    def test_self_paced_attempt_is_409(self):
        self_paced_cohort = Cohort.objects.create(
            course=self.course, name="Self-paced", external_key="sp", mode="self_paced",
            is_active=True,
        )
        project = CourseProject.objects.create(
            course=self.course, cohort=self_paced_cohort, slug="self-paced", title="Self-paced",
        )
        response = self._patch(self._url(project=project), {"state": "CL"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "invalid_transition")
