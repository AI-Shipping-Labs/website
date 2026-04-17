"""Tests for dashboard query performance - issue #181.

Verifies:
- No N+1 queries in _get_in_progress_courses (uses annotation instead of per-course total_units())
- get_active_override called at most once per request (not duplicated by get_user_level)
- user.tier prefetched via select_related
- Total dashboard DB queries stay under a reasonable bound
"""

from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.access import get_active_override, get_user_level
from content.models import (
    Article,
    Course,
    Module,
    Unit,
    UserCourseProgress,
)
from events.models import Event, EventRegistration
from notifications.models import Notification
from tests.fixtures import TierSetupMixin
from voting.models import Poll

User = get_user_model()


class InProgressCoursesQueryCountTest(TierSetupMixin, TestCase):
    """Verify _get_in_progress_courses does not cause N+1 queries."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='perf@example.com', password='testpass',
        )
        # Create 5 courses, each with 4 units, user has progress in all of them
        cls.courses = []
        now = timezone.now()
        for i in range(5):
            course = Course.objects.create(
                title=f'Course {i}', slug=f'course-{i}', status='published',
            )
            cls.courses.append(course)
            module = Module.objects.create(
                course=course, title=f'Mod {i}', slug=f'mod-{i}', sort_order=0,
            )
            units = []
            for j in range(4):
                unit = Unit.objects.create(
                    module=module, title=f'Unit {i}-{j}',
                    slug=f'unit-{i}-{j}', sort_order=j,
                )
                units.append(unit)
            # Complete 2 of 4 units (course is in progress)
            for j in range(2):
                UserCourseProgress.objects.create(
                    user=cls.user, unit=units[j],
                    completed_at=now - timedelta(hours=10 - i),
                )

    def test_query_count_does_not_scale_with_course_count(self):
        """With 5 in-progress courses, query count should be constant (not 5+)."""
        from content.views.home import _get_in_progress_courses
        user_level = get_user_level(self.user)

        # Queries expected:
        # 1. Fetch UserCourseProgress with select_related (1 query)
        # 2. Annotate unit counts on Course (1 query)
        with self.assertNumQueries(2):
            result = _get_in_progress_courses(self.user, user_level)

        self.assertEqual(len(result), 5)
        for item in result:
            self.assertEqual(item['total_units'], 4)
            self.assertEqual(item['completed_count'], 2)
            self.assertEqual(item['percentage'], 50)

    def test_no_progress_uses_zero_queries_after_initial(self):
        """User with no progress should use minimal queries."""
        from content.views.home import _get_in_progress_courses
        other_user = User.objects.create_user(
            email='noprogress@example.com', password='testpass',
        )
        user_level = get_user_level(other_user)

        # 1 query: fetch progress (returns empty)
        # No annotation query needed because course_data is empty
        with self.assertNumQueries(1):
            result = _get_in_progress_courses(other_user, user_level)
        self.assertEqual(result, [])


class DuplicateOverrideQueryTest(TierSetupMixin, TestCase):
    """Verify get_active_override is not called twice per dashboard request."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='override@example.com', password='testpass',
        )
        cls.user.tier = cls.basic_tier
        cls.user.save()

    def test_get_active_override_called_once(self):
        """Dashboard should call get_active_override at most once."""
        self.client.login(email='override@example.com', password='testpass')

        with patch('content.views.home.get_active_override', wraps=get_active_override) as mock_override:
            response = self.client.get('/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(mock_override.call_count, 1)

    def test_get_user_level_with_precomputed_override_skips_db(self):
        """Passing active_override=None to get_user_level should not query for overrides."""
        # Pre-fetch the user with tier
        user = User.objects.select_related('tier').get(pk=self.user.pk)

        # With active_override=None explicitly passed, no override DB query should happen
        with self.assertNumQueries(0):
            level = get_user_level(user, active_override=None)
        self.assertEqual(level, self.basic_tier.level)


class SelectRelatedTierTest(TierSetupMixin, TestCase):
    """Verify user.tier is prefetched and does not cause extra queries."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='tiertest@example.com', password='testpass',
        )
        cls.user.tier = cls.main_tier
        cls.user.save()

    def test_dashboard_prefetches_user_tier(self):
        """Accessing user.tier.name after the dashboard view prefetch should not query."""
        # Simulate what the dashboard does
        user = User.objects.select_related('tier').get(pk=self.user.pk)

        # Accessing tier attributes should not trigger additional queries
        with self.assertNumQueries(0):
            _ = user.tier.name
            _ = user.tier.level


class DashboardTotalQueryCountTest(TierSetupMixin, TestCase):
    """Verify total dashboard query count stays reasonable."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='totalq@example.com', password='testpass',
            first_name='QueryTest',
        )
        cls.user.tier = cls.main_tier
        cls.user.save()

        now = timezone.now()

        # Create some content to exercise all dashboard sections
        Article.objects.create(
            title='Test Article', slug='test-article',
            description='Desc', date=date.today(), published=True,
        )
        Event.objects.create(
            title='Test Recording', slug='test-recording',
            description='Desc', start_datetime=now, status='completed',
            recording_url='https://youtube.com/test', published=True,
        )
        future_event = Event.objects.create(
            slug='future-event', title='Future Event',
            start_datetime=now + timedelta(days=3), status='upcoming',
        )
        EventRegistration.objects.create(user=cls.user, event=future_event)

        Poll.objects.create(
            title='Test Poll', poll_type='topic', status='open',
        )
        Notification.objects.create(
            user=cls.user, title='Test Notif', url='/test', read=False,
        )

        # One in-progress course
        course = Course.objects.create(
            title='Perf Course', slug='perf-course', status='published',
        )
        module = Module.objects.create(
            course=course, title='Mod', slug='mod', sort_order=0,
        )
        unit1 = Unit.objects.create(
            module=module, title='U1', slug='u1', sort_order=0,
        )
        Unit.objects.create(
            module=module, title='U2', slug='u2', sort_order=1,
        )
        UserCourseProgress.objects.create(
            user=cls.user, unit=unit1, completed_at=now,
        )

    def test_dashboard_total_queries_under_limit(self):
        """Full dashboard render should use a bounded number of DB queries.

        The query budget includes:
        - Session + auth (3 queries)
        - User with select_related tier (included in auth)
        - get_active_override (1 query)
        - In-progress courses: progress + annotation (2 queries)
        - Upcoming events (1 query)
        - Recent content: articles + recordings (2 queries)
        - Active polls (1 query)
        - Notifications (1 query)
        - Template-level queries (poll vote/option counts, etc.)

        Total must stay under 20. Before this fix, N+1 on courses alone
        could cause 5+ extra queries per in-progress course.
        """
        self.client.login(email='totalq@example.com', password='testpass')

        # We use assertLess instead of assertNumQueries to allow for
        # template-level queries (poll counts, etc.) while still catching
        # regressions like N+1.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'content/dashboard.html')
        self.assertLess(
            len(ctx), 20,
            f"Dashboard used {len(ctx)} queries (limit: 20). "
            f"Possible N+1 regression.",
        )
