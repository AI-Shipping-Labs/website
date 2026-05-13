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

from content.access import LEVEL_MAIN, get_active_override, get_user_level
from content.models import (
    Article,
    Course,
    CourseAccess,
    Enrollment,
    Module,
    Unit,
    UserCourseProgress,
)
from events.models import Event, EventRegistration
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
        # Create 5 courses, each with 4 units, user is enrolled and has
        # completed 2 of 4 units in each (course is in progress).
        cls.courses = []
        now = timezone.now()
        for i in range(5):
            course = Course.objects.create(
                title=f'Course {i}', slug=f'course-{i}', status='published',
            )
            cls.courses.append(course)
            Enrollment.objects.create(user=cls.user, course=course)
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
            for j in range(2):
                UserCourseProgress.objects.create(
                    user=cls.user, unit=units[j],
                    completed_at=now - timedelta(hours=10 - i),
                )

    def test_query_count_does_not_scale_with_course_count(self):
        """With 5 enrolled-in-progress courses, query count is constant (not 5+).

        Issue #236: queries are sourced from Enrollment now, not inferred
        from progress rows. Issue #346 added a batched CourseAccess query
        to replace the per-course can_access() N+1. Total constant queries:
          1. Active enrollments + course (select_related)
          2. Per-course total unit counts (annotate)
          3. Per-course completed unit ids (UserCourseProgress)
          4. All units across enrolled courses (resolve next_unit in Python)
          5. Individual CourseAccess grants for enrolled courses
        """
        from content.views.home import _get_in_progress_courses
        user_level = get_user_level(self.user)

        with self.assertNumQueries(5):
            result = _get_in_progress_courses(self.user, user_level)

        self.assertEqual(len(result), 5)
        for item in result:
            self.assertEqual(item['total_units'], 4)
            self.assertEqual(item['completed_count'], 2)
            self.assertEqual(item['percentage'], 50)
            self.assertIsNotNone(item['next_unit'])

    def test_next_unit_resolution_does_not_scale_with_course_count(self):
        """Adding more in-progress courses must not increase the query count."""
        from content.views.home import _get_in_progress_courses
        user_level = get_user_level(self.user)

        # Add 5 more enrolled in-progress courses (10 total) and assert
        # the query count stays at 5 — proving the dashboard query is
        # not N+1.
        now = timezone.now()
        for i in range(5, 10):
            course = Course.objects.create(
                title=f'Course {i}', slug=f'course-{i}', status='published',
            )
            Enrollment.objects.create(user=self.user, course=course)
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
            for j in range(2):
                UserCourseProgress.objects.create(
                    user=self.user, unit=units[j],
                    completed_at=now - timedelta(hours=10 - i),
                )

        with self.assertNumQueries(5):
            result = _get_in_progress_courses(self.user, user_level)
        self.assertEqual(len(result), 10)
        for item in result:
            self.assertIsNotNone(item['next_unit'])

    def test_no_enrollments_uses_one_query(self):
        """User with no enrollments uses a single query and short-circuits."""
        from content.views.home import _get_in_progress_courses
        other_user = User.objects.create_user(
            email='noprogress@example.com', password='testpass',
        )
        user_level = get_user_level(other_user)

        # 1 query: fetch enrollments (returns empty), then short-circuit
        with self.assertNumQueries(1):
            result = _get_in_progress_courses(other_user, user_level)
        self.assertEqual(result, [])


class BelowTierWithCourseAccessQueryCountTest(TierSetupMixin, TestCase):
    """Issue #346: Basic-tier user enrolled in Main-tier courses must not
    cause an N+1 from per-course can_access() / CourseAccess.exists().

    Before the fix, the dashboard issued one extra `CourseAccess.exists()`
    query per enrolled course whose `required_level` exceeded the user's
    tier level. After the fix, all CourseAccess grants are fetched in a
    single batched query and membership-checked in Python, so the query
    count is constant and matches the happy-path test (5 queries).
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='below-tier@example.com', password='testpass',
        )
        cls.user.tier = cls.basic_tier
        cls.user.save()

        now = timezone.now()
        # Enroll in 5 courses requiring Main. User is on Basic, so the
        # only courses that should appear are the 2 with explicit
        # CourseAccess grants.
        cls.courses = []
        for i in range(5):
            course = Course.objects.create(
                title=f'Locked Course {i}', slug=f'locked-course-{i}',
                status='published', required_level=LEVEL_MAIN,
            )
            cls.courses.append(course)
            Enrollment.objects.create(user=cls.user, course=course)
            module = Module.objects.create(
                course=course, title=f'Mod {i}', slug=f'locked-mod-{i}',
                sort_order=0,
            )
            units = []
            for j in range(4):
                unit = Unit.objects.create(
                    module=module, title=f'Unit {i}-{j}',
                    slug=f'locked-unit-{i}-{j}', sort_order=j,
                )
                units.append(unit)
            for j in range(2):
                UserCourseProgress.objects.create(
                    user=cls.user, unit=units[j],
                    completed_at=now - timedelta(hours=10 - i),
                )

        # Grant individual access to 2 of the 5 locked courses.
        CourseAccess.objects.create(
            user=cls.user, course=cls.courses[0], access_type='purchased',
        )
        CourseAccess.objects.create(
            user=cls.user, course=cls.courses[1], access_type='granted',
        )

    def test_query_count_constant_when_below_tier(self):
        """With 5 enrolled but only 2 accessible via CourseAccess, query
        count must equal the happy-path 5 (no N+1 from can_access).
        """
        from content.views.home import _get_in_progress_courses
        user_level = get_user_level(self.user)

        with self.assertNumQueries(5):
            result = _get_in_progress_courses(self.user, user_level)

        # Only the 2 courses with CourseAccess grants surface; the other
        # 3 are filtered out because the user's Basic tier is below the
        # course's required Main level.
        self.assertEqual(len(result), 2)
        returned_ids = {item['course'].id for item in result}
        self.assertEqual(
            returned_ids,
            {self.courses[0].id, self.courses[1].id},
        )

    def test_query_count_does_not_scale_with_locked_enrollments(self):
        """Adding more locked-but-accessed enrollments must not increase
        the query count. This is the core N+1 guard for issue #346.
        """
        from content.views.home import _get_in_progress_courses

        # Add 5 more Main-tier enrollments (10 total) — none with
        # CourseAccess. Pre-fix this would add 5 extra .exists() queries
        # to the previous test's count.
        for i in range(5, 10):
            course = Course.objects.create(
                title=f'Locked Course {i}', slug=f'locked-course-{i}',
                status='published', required_level=LEVEL_MAIN,
            )
            Enrollment.objects.create(user=self.user, course=course)
            module = Module.objects.create(
                course=course, title=f'Mod {i}', slug=f'locked-mod-{i}',
                sort_order=0,
            )
            for j in range(4):
                Unit.objects.create(
                    module=module, title=f'Unit {i}-{j}',
                    slug=f'locked-unit-{i}-{j}', sort_order=j,
                )

        user_level = get_user_level(self.user)
        with self.assertNumQueries(5):
            result = _get_in_progress_courses(self.user, user_level)

        # Still only 2 results (the originally-granted courses).
        self.assertEqual(len(result), 2)


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
        # One in-progress course (enrolled, with 1 of 2 units complete)
        course = Course.objects.create(
            title='Perf Course', slug='perf-course', status='published',
        )
        Enrollment.objects.create(user=cls.user, course=course)
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
        - In-progress courses: enrollments + unit-counts + progress +
          units + CourseAccess grants (5 queries; CourseAccess added in #346)
        - Upcoming events (1 query)
        - Recent content: articles + recordings (2 queries)
        - Active polls (1 query)
        - Template-level queries (poll vote/option counts, etc.)

        Total must stay under 25. Before #181 fix, N+1 on courses alone
        could cause 5+ extra queries per in-progress course; #346 added
        a single batched CourseAccess query to eliminate a second N+1.
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
            len(ctx), 25,
            f"Dashboard used {len(ctx)} queries (limit: 25). "
            f"Possible N+1 regression.",
        )
