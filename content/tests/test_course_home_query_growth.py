"""Query-count regression guards for course Home and its tabs.

Course Home used to build every tab's context and resolve homework state
once per homework per builder, so a ten-homework cohort paid ~180 SQL
queries on every tab -- including Projects and Announcements, whose panels
never render homework state. These tests pin both halves of the fix: the
tab context is built per tab, and homework state is read in one batch, so
adding homework rows must not grow any tab's query count.
"""

import datetime
import uuid

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import User
from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    Enrollment,
    Module,
    Unit,
)
from content.models.homework import AnswerType, Homework, Question, QuestionType


class CourseHomeQueryGrowthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="course-query-growth@example.com", password="testpass",
            email_verified=True,
        )
        cls.course = Course.objects.create(
            title="Query growth course", slug="query-growth",
            status="published", required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title="Week 1", slug="week-1",
            sort_order=1, available_after_days=0,
        )
        Unit.objects.create(
            module=cls.module, title="First lesson", slug="first-lesson",
            sort_order=1,
        )
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name="Cohort 4", external_key="4",
            start_date=today - datetime.timedelta(days=2),
            end_date=today + datetime.timedelta(days=60),
            is_active=True,
        )
        CohortEnrollment.objects.create(user=cls.user, cohort=cls.cohort)
        Enrollment.objects.create(user=cls.user, course=cls.course)
        cls._add_homework(0)

    @classmethod
    def _add_homework(cls, index):
        content_id = uuid.uuid4()
        Unit.objects.create(
            module=cls.module,
            title=f"Homework {index}",
            slug=f"homework-{index}",
            sort_order=100 + index,
            kind="homework",
            content_id=content_id,
            homework="Write an answer.",
        )
        homework = Homework.objects.create(
            cohort=cls.cohort,
            slug=f"homework-{index}",
            title=f"Homework {index}",
            content_id=content_id,
            due_date=timezone.now() + datetime.timedelta(days=3 + index),
            stepper_enabled=True,
        )
        Question.objects.create(
            homework=homework,
            source_question_id=f"q-{index}",
            text="What did you learn?",
            question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.ANY,
        )

    def _content_id(self, index):
        return Unit.objects.get(slug=f"homework-{index}").content_id

    def _tab_paths(self):
        return {
            "home": "/courses/query-growth/home?cohort=4",
            "syllabus": "/courses/query-growth/home/syllabus?cohort=4",
            "homework": "/courses/query-growth/home/homework?cohort=4",
            "projects": "/courses/query-growth/home/projects?cohort=4",
            "announcements": (
                "/courses/query-growth/home/announcements?cohort=4"
            ),
        }

    def _queries(self, path):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        return len(captured.captured_queries)

    def test_tab_query_counts_do_not_grow_with_homework_count(self):
        self.client.force_login(self.user)
        base = {
            label: self._queries(path)
            for label, path in self._tab_paths().items()
        }
        for index in range(1, 10):
            self._add_homework(index)

        for label, path in self._tab_paths().items():
            with self.subTest(tab=label):
                self.assertLessEqual(
                    self._queries(path), base[label] + 2,
                    f"{label} tab query count grew with the homework count",
                )

    def test_home_homework_state_is_batched_not_per_homework(self):
        """Ten extra homework rows add no per-homework reads to Home."""
        self.client.force_login(self.user)
        one_homework = self._queries(self._tab_paths()["home"])
        for index in range(1, 11):
            self._add_homework(index)
        eleven_homework = self._queries(self._tab_paths()["home"])
        self.assertLessEqual(
            eleven_homework, one_homework + 2,
            "home tab still resolves homework state per homework",
        )

    def test_syllabus_tab_does_not_build_homework_deliverable_state(self):
        """The syllabus tab renders annotated rows, not deliverable cards:
        its query count must stay flat regardless of homework count."""
        self.client.force_login(self.user)
        with self.assertNumQueries(self._queries(self._tab_paths()["syllabus"])):
            self.client.get(self._tab_paths()["syllabus"])

    def test_loader_fallback_prefers_the_latest_started_cohort(self):
        """No enrolled cohort: the batched fallback mirrors
        ``resolve_homework_for_unit`` -- the latest-started course cohort
        that has the homework wins, not an arbitrary row."""
        from content.services.current_module import HomeworkStateLoader

        expected = Homework.objects.get(cohort=self.cohort, slug="homework-0")
        earlier = Cohort.objects.create(
            course=self.course, name="Older cohort", external_key="older",
            start_date=timezone.localdate() - datetime.timedelta(days=40),
            end_date=timezone.localdate() - datetime.timedelta(days=5),
        )
        Homework.objects.create(
            cohort=earlier, slug="homework-0", title="Older homework",
            content_id=expected.content_id,
        )
        unit = Unit.objects.get(slug="homework-0")
        other_user = User.objects.create_user(
            email="no-enrollment@example.com", password="testpass",
            email_verified=True,
        )

        loader = HomeworkStateLoader(other_user, None, self.course)
        state = loader.state_for(unit)

        self.assertFalse(state["submitted"])
        self.assertEqual(state["questions"], 1)
        self.assertEqual(
            loader._homework_by_content_id[expected.content_id].pk,
            expected.pk,
        )
