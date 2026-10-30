"""Query-budget regression tests for logged-in course, workshop, and dashboard pages."""

import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    CourseAccess,
    Homework,
    Module,
    Question,
    Unit,
    UserCourseProgress,
    Workshop,
)
from events.models import Event, EventSeries
from tests.fixtures import TierSetupMixin


def _measured_auth_get(client, url):
    cache.clear()
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    return response, len(ctx.captured_queries)


def _add_course_week(course, cohort, series, week_index):
    now = timezone.now()
    top = Module.objects.create(
        course=course,
        title=f"Week {week_index}",
        slug=f"week-{week_index}",
        sort_order=week_index,
        available_after_days=(week_index - 1) * 7,
    )
    session_mod = Module.objects.create(
        course=course,
        parent=top,
        title=f"Session {week_index}",
        slug="session",
        sort_order=1,
    )
    Unit.objects.create(
        module=session_mod,
        title=f"Session {week_index}",
        slug=f"session-{week_index}",
        sort_order=1,
        kind="event",
        session_position=week_index,
        body="Session body " * 50,
    )
    Event.objects.create(
        title=f"Live Session {week_index}",
        slug=f"live-session-{course.slug}-{week_index}",
        event_series=series,
        series_position=week_index,
        start_datetime=now + timezone.timedelta(days=week_index),
        end_datetime=now + timezone.timedelta(days=week_index, hours=1),
        status="scheduled",
        published=True,
    )
    for topic_idx in range(1, 3):
        sub = Module.objects.create(
            course=course,
            parent=top,
            title=f"Topic {week_index}.{topic_idx}",
            slug=f"topic-{topic_idx}",
            sort_order=topic_idx + 1,
        )
        for lesson_idx in range(1, 3):
            Unit.objects.create(
                module=sub,
                title=f"Lesson {week_index}.{topic_idx}.{lesson_idx}",
                slug=f"lesson-{lesson_idx}",
                sort_order=lesson_idx,
                kind="lesson",
                body="Lesson markdown " * 100,
            )
    hw_mod = Module.objects.create(
        course=course,
        parent=top,
        title="Homework",
        slug="homework",
        sort_order=10,
    )
    hw_unit = Unit.objects.create(
        module=hw_mod,
        title=f"Homework {week_index}",
        slug=f"hw-{week_index}",
        sort_order=1,
        kind="homework",
        content_id=uuid.uuid4(),
        homework="Homework instructions " * 50,
    )
    hw = Homework.objects.create(
        cohort=cohort,
        content_id=hw_unit.content_id,
        title=f"Homework {week_index}",
        slug=f"hw-{course.slug}-{week_index}",
        due_date=now + timezone.timedelta(days=week_index * 7),
    )
    Question.objects.create(
        homework=hw,
        source_question_id=f"q-{week_index}-1",
        text="Explain your solution",
        question_type="free_form",
        answer_type="any",
    )
    return top


class CourseReaderPagesQueryBudgetTest(TierSetupMixin, TestCase):
    """Logged-in course and workshop pages keep constant query counts as curriculum grows."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            email="fast-course-reader@example.com",
            password="pw",
            email_verified=True,
        )
        cls.course = Course.objects.create(
            title="AI Buildcamp Budget",
            slug="ai-buildcamp",
            status="published",
            access_mode="entitlement",
            required_level=20,
            reader_navigation_scope="module",
        )
        CourseAccess.objects.create(
            user=cls.user,
            course=cls.course,
            access_type="granted",
        )
        cls.series = EventSeries.objects.create(
            name="Buildcamp Live",
            slug="buildcamp-live-budget",
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course,
            name="Cohort 1",
            external_key="cohort-1",
            mode="cohort",
            is_active=True,
            start_date=timezone.localdate() - timezone.timedelta(days=7),
            end_date=timezone.localdate() + timezone.timedelta(days=60),
            event_series=cls.series,
        )
        CohortEnrollment.objects.create(user=cls.user, cohort=cls.cohort)
        for week_idx in range(1, 3):
            _add_course_week(cls.course, cls.cohort, cls.series, week_idx)
        first_unit = Unit.objects.filter(
            module__course=cls.course, kind="lesson",
        ).first()
        UserCourseProgress.objects.create(
            user=cls.user,
            unit=first_unit,
            completed_at=timezone.now(),
        )
        cls.workshop = Workshop.objects.create(
            slug="fast-logged-in-ws",
            title="Fast Logged-in Workshop",
            status="published",
            date=timezone.localdate(),
            tags=[],
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def _assert_constant_across_extra_weeks(self, url, max_queries, marker):
        self.client.get(url)
        first_resp, small_count = _measured_auth_get(self.client, url)
        self.assertContains(first_resp, marker)
        for week_idx in range(3, 6):
            _add_course_week(self.course, self.cohort, self.series, week_idx)
        second_resp, large_count = _measured_auth_get(self.client, url)
        self.assertContains(second_resp, marker)
        self.assertEqual(
            small_count,
            large_count,
            msg=f"{url} query count grew with curriculum size ({small_count} -> {large_count})",
        )
        self.assertLessEqual(
            large_count,
            max_queries,
            msg=f"{url} took {large_count} queries (expected <= {max_queries})",
        )

    def test_course_overview_query_count_is_constant(self):
        self._assert_constant_across_extra_weeks(
            "/courses/ai-buildcamp?view=overview",
            max_queries=40,
            marker="AI Buildcamp Budget",
        )

    def test_course_home_query_count_is_constant(self):
        self._assert_constant_across_extra_weeks(
            "/courses/ai-buildcamp/home",
            max_queries=55,
            marker="AI Buildcamp Budget",
        )

    def test_course_syllabus_query_count_is_constant(self):
        self._assert_constant_across_extra_weeks(
            "/courses/ai-buildcamp/home/syllabus",
            max_queries=35,
            marker="Week 1",
        )

    def test_module_overview_query_count_is_constant(self):
        self._assert_constant_across_extra_weeks(
            "/courses/ai-buildcamp/week-1",
            max_queries=45,
            marker="Week 1",
        )

    def test_unit_reader_query_count_is_constant(self):
        self._assert_constant_across_extra_weeks(
            "/courses/ai-buildcamp/week-1/topic-1/lesson-1",
            max_queries=35,
            marker="Lesson 1.1.1",
        )

    def test_logged_in_dashboard_and_workshop_stay_within_budget(self):
        self.client.get("/")
        dash_resp, dash_queries = _measured_auth_get(self.client, "/")
        self.assertTemplateUsed(dash_resp, "content/dashboard.html")
        self.assertLessEqual(dash_queries, 50)
        ws_resp, ws_queries = _measured_auth_get(
            self.client, "/workshops/fast-logged-in-ws",
        )
        self.assertContains(ws_resp, "Fast Logged-in Workshop")
        self.assertLessEqual(ws_queries, 30)
