"""Tests for Logged-in User Home Dashboard - issue #104.

Covers:
- Anonymous users see the public marketing homepage (no change)
- Authenticated users see the personalized dashboard at /
- Continue learning with in-progress courses and workshops
- The actionable For you feed, including articles and active polls
- Browse destinations that keep the dashboard useful with an empty feed
- Commitment-first onboarding, sprint, event, and activation zones
"""

import json
import re
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import Resolver404, resolve
from django.utils import timezone
from freezegun import freeze_time

from accounts.models import TierOverride
from bookclub.models import BOOK_STATUS_CURRENT, Book
from content.access import LEVEL_PREMIUM
from content.models import (
    Article,
    Course,
    CourseAccess,
    Enrollment,
    Module,
    Unit,
    UserContentCompletion,
    UserCourseProgress,
    Workshop,
    WorkshopPage,
)
from content.models.completion import CONTENT_TYPE_WORKSHOP_PAGE
from events.models import Event, EventRegistration, EventSeries
from notifications.models import Notification
from plans.models import Plan, Sprint, SprintEnrollment
from tests.fixtures import TierSetupMixin
from voting.models import Poll

User = get_user_model()


# ============================================================
# Anonymous vs Authenticated Routing
# ============================================================


class HomepageRoutingTest(TierSetupMixin, TestCase):
    """Test that / routes to the correct template based on auth status."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email='test@example.com', password='testpass123',
            first_name='Alice',
        )

    def test_anonymous_user_sees_public_homepage(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'home.html')

    def test_anonymous_user_sees_hero_section(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Turn AI ideas into', content)

    def test_authenticated_user_sees_dashboard(self):
        self.client.login(email='test@example.com', password='testpass123')
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'content/dashboard.html')

    def test_authenticated_user_does_not_see_public_homepage(self):
        self.client.login(email='test@example.com', password='testpass123')
        response = self.client.get('/')
        self.assertTemplateNotUsed(response, 'home.html')

    def test_authenticated_user_sees_dashboard_content_with_header(self):
        self.client.login(email='test@example.com', password='testpass123')
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('data-testid="dashboard-heading"', content)
        self.assertIn('data-testid="dashboard-commitment-zones"', content)
        self.assertIn('data-testid="dashboard-home-feed"', content)
        self.assertIn('data-testid="dashboard-feed-destinations"', content)
        self.assertIn('For you', content)
        self.assertIn('Welcome back, Alice', content)
        self.assertNotIn('id="join-free"', content)
        self.assertNotIn('id="register-form"', content)


# ============================================================
# Dashboard Header
# ============================================================


class DashboardHeaderTest(TierSetupMixin, TestCase):
    """The dashboard renders a compact identity header and tier pill."""

    def _login_user(self, email, *, tier=None, first_name=''):
        user = User.objects.create_user(
            email=email, password='testpass', first_name=first_name,
            tier=tier,
        )
        self.client.login(email=email, password='testpass')
        return user

    def test_shows_first_name_greeting_and_one_h1(self):
        self._login_user(
            email='alice@example.com',
            first_name='Alice', tier=self.free_tier,
        )
        response = self.client.get('/')
        content = response.content.decode()
        self.assertContains(response, 'data-testid="dashboard-header"')
        self.assertContains(response, 'Welcome back, Alice')
        self.assertEqual(len(re.findall(r'<h1\b', content)), 1)

    def test_shows_generic_greeting_without_first_name(self):
        self._login_user('noname@example.com', tier=self.free_tier)
        response = self.client.get('/')
        self.assertContains(response, 'Welcome back')
        self.assertNotContains(response, 'Welcome back,')

    def test_tier_pill_renders_for_all_standard_tiers(self):
        for tier in [
            self.free_tier,
            self.basic_tier,
            self.main_tier,
            self.premium_tier,
        ]:
            with self.subTest(tier=tier.slug):
                self.client.logout()
                self._login_user(f'{tier.slug}@example.com', tier=tier)
                response = self.client.get('/')
                self.assertContains(response, 'data-testid="dashboard-tier-pill"')
                self.assertContains(response, tier.name)

    def test_tier_pill_defaults_to_free_without_explicit_tier(self):
        self._login_user('notier@example.com')
        response = self.client.get('/')
        self.assertContains(response, 'data-testid="dashboard-tier-pill"')
        self.assertContains(response, 'Free')

    def test_active_override_tier_is_marked_as_trial(self):
        user = self._login_user('override@example.com', tier=self.free_tier)
        TierOverride.objects.create(
            user=user,
            original_tier=self.free_tier,
            override_tier=self.main_tier,
            expires_at=timezone.now() + timedelta(days=7),
        )
        response = self.client.get('/')
        self.assertContains(response, 'data-testid="dashboard-tier-pill"')
        self.assertContains(response, 'Main trial')

    def test_account_link_remains_available_in_header(self):
        self._login_user('acct@example.com')
        response = self.client.get('/')
        self.assertContains(response, 'Account')

    def test_old_upgrade_welcome_card_is_not_rendered(self):
        self._login_user('upgrade@example.com', tier=self.free_tier)
        response = self.client.get('/')
        self.assertNotContains(response, 'Upgrade')


# ============================================================
# Continue Learning
# ============================================================


class ContinueLearningTest(TierSetupMixin, TestCase):
    """Test the continue learning section.

    Issue #236 made the dashboard query Enrollment rows instead of
    inferring "in progress" from completed-unit counts. These tests
    create explicit Enrollments via ``_enroll`` (matches what the
    Enroll button + auto-enroll-on-complete hook would do in
    production).
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='learner@example.com', password='testpass',
        )
        self.client.login(email='learner@example.com', password='testpass')

        # Create a course with 4 units
        self.course = Course.objects.create(
            title='AI Basics', slug='ai-basics', status='published',
        )
        self.module = Module.objects.create(
            course=self.course, title='Module 1', slug='module-1', sort_order=1,
        )
        self.units = []
        for i in range(4):
            unit = Unit.objects.create(
                module=self.module, title=f'Unit {i+1}', slug=f'unit-{i+1}', sort_order=i,
            )
            self.units.append(unit)

    def _enroll(self, user, course):
        """Create an active Enrollment for (user, course).

        The dashboard now queries Enrollment rows; production code
        creates one when the user clicks Enroll or marks the first
        lesson complete.
        """
        return Enrollment.objects.create(user=user, course=course)

    def test_empty_state_when_no_courses_in_progress(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertEqual(response.context['in_progress_learning'], [])
        self.assertNotIn(
            'data-testid="dashboard-continue-learning-section"', content,
        )
        self.assertNotIn('No courses or workshops in progress yet', content)

    def test_shows_in_progress_course(self):
        # Enroll + complete 2 of 4 units
        self._enroll(self.user, self.course)
        now = timezone.now()
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0],
            completed_at=now - timedelta(hours=2),
        )
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[1],
            completed_at=now - timedelta(hours=1),
        )

        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('AI Basics', content)
        self.assertIn('2/4 units completed', content)
        self.assertIn('Continue', content)

    def test_shows_progress_percentage(self):
        self._enroll(self.user, self.course)
        now = timezone.now()
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0],
            completed_at=now,
        )
        response = self.client.get('/')
        content = response.content.decode()
        # 1 of 4 = 25%
        self.assertIn('25%', content)

    def test_shows_last_accessed_unit(self):
        self._enroll(self.user, self.course)
        now = timezone.now()
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0],
            completed_at=now - timedelta(hours=2),
        )
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[1],
            completed_at=now,
        )
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Last: Unit 2', content)

    def test_fully_completed_course_not_shown(self):
        self._enroll(self.user, self.course)
        now = timezone.now()
        for i, unit in enumerate(self.units):
            UserCourseProgress.objects.create(
                user=self.user, unit=unit,
                completed_at=now - timedelta(hours=4-i),
            )
        response = self.client.get('/')
        content = response.content.decode()
        self.assertNotIn('AI Basics', content)
        self.assertNotIn(
            'data-testid="dashboard-continue-learning-section"', content,
        )

    def test_continue_button_links_to_next_unfinished_unit(self):
        # Complete units 1-3 of 4 → Continue should link to unit 4.
        self._enroll(self.user, self.course)
        now = timezone.now()
        for i in range(3):
            UserCourseProgress.objects.create(
                user=self.user, unit=self.units[i],
                completed_at=now - timedelta(hours=3 - i),
            )

        response = self.client.get('/')
        item = response.context['in_progress_courses'][0]
        self.assertEqual(item['next_unit'], self.units[3])
        # The button uses the next-unit URL, not the course URL.
        self.assertContains(response, self.units[3].get_absolute_url())

    def test_continue_button_links_to_first_skipped_unit(self):
        # Complete units 1, 3 (skip unit 2) → Continue should link to unit 2.
        self._enroll(self.user, self.course)
        now = timezone.now()
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0],
            completed_at=now - timedelta(hours=2),
        )
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[2],
            completed_at=now - timedelta(hours=1),
        )

        response = self.client.get('/')
        item = response.context['in_progress_courses'][0]
        self.assertEqual(item['next_unit'], self.units[1])
        self.assertContains(response, self.units[1].get_absolute_url())

    def test_continue_card_aria_label_names_course(self):
        # The whole learning row is one link with the course as its name.
        self._enroll(self.user, self.course)
        now = timezone.now()
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0],
            completed_at=now,
        )
        response = self.client.get('/')
        expected_label = 'aria-label="AI Basics"'
        self.assertContains(response, expected_label)

    def test_fully_completed_course_stays_filtered_from_in_progress(self):
        # When all units are completed the course should not appear in the
        # in-progress list — this is the existing behavior, asserted here
        # so the next-unit work doesn't accidentally re-include it.
        self._enroll(self.user, self.course)
        now = timezone.now()
        for i, unit in enumerate(self.units):
            UserCourseProgress.objects.create(
                user=self.user, unit=unit,
                completed_at=now - timedelta(hours=4 - i),
            )
        response = self.client.get('/')
        self.assertEqual(response.context['in_progress_courses'], [])
        self.assertNotContains(response, 'AI Basics')

    def test_completed_course_excluded_while_partial_course_is_shown(self):
        completed_course = Course.objects.create(
            title='AI Agents Buildcamp',
            slug='ai-agents-buildcamp',
            status='published',
        )
        completed_module = Module.objects.create(
            course=completed_course,
            title='Buildcamp Module',
            slug='buildcamp-module',
            sort_order=1,
        )
        completed_units = [
            Unit.objects.create(
                module=completed_module,
                title=f'Buildcamp Unit {i + 1}',
                slug=f'buildcamp-unit-{i + 1}',
                sort_order=i,
            )
            for i in range(10)
        ]
        self._enroll(self.user, completed_course)

        partial_course = Course.objects.create(
            title='Python Fundamentals',
            slug='python-fundamentals',
            status='published',
        )
        partial_module = Module.objects.create(
            course=partial_course,
            title='Python Module',
            slug='python-module',
            sort_order=1,
        )
        partial_units = [
            Unit.objects.create(
                module=partial_module,
                title=f'Python Unit {i + 1}',
                slug=f'python-unit-{i + 1}',
                sort_order=i,
            )
            for i in range(5)
        ]
        self._enroll(self.user, partial_course)

        now = timezone.now()
        for i, unit in enumerate(completed_units):
            UserCourseProgress.objects.create(
                user=self.user,
                unit=unit,
                completed_at=now - timedelta(days=1, hours=10 - i),
            )
        for i, unit in enumerate(partial_units[:2]):
            UserCourseProgress.objects.create(
                user=self.user,
                unit=unit,
                completed_at=now - timedelta(hours=2 - i),
            )

        response = self.client.get('/')
        self.assertNotContains(response, 'AI Agents Buildcamp')
        self.assertContains(response, 'Python Fundamentals')
        self.assertContains(response, '2/5 units completed')
        self.assertContains(response, 'style="width: 40%"')

    def test_most_recently_accessed_first(self):
        # Create a second course
        course2 = Course.objects.create(
            title='ML Advanced', slug='ml-advanced', status='published',
        )
        module2 = Module.objects.create(
            course=course2, title='Module 2', slug='module-2', sort_order=1,
        )
        unit_a = Unit.objects.create(
            module=module2, title='Adv Unit 1', slug='adv-unit-1', sort_order=0,
        )
        Unit.objects.create(
            module=module2, title='Adv Unit 2', slug='adv-unit-2', sort_order=1,
        )

        self._enroll(self.user, self.course)
        self._enroll(self.user, course2)

        now = timezone.now()
        # AI Basics: accessed 2 hours ago
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0],
            completed_at=now - timedelta(hours=2),
        )
        # ML Advanced: accessed 1 hour ago (more recent)
        UserCourseProgress.objects.create(
            user=self.user, unit=unit_a,
            completed_at=now - timedelta(hours=1),
        )

        response = self.client.get('/')
        content = response.content.decode()
        # ML Advanced should appear before AI Basics
        pos_ml = content.index('ML Advanced')
        pos_ai = content.index('AI Basics')
        self.assertLess(pos_ml, pos_ai)

    def test_continue_learning_limited_to_three_most_recent_items(self):
        now = timezone.now()
        for i in range(5):
            course = Course.objects.create(
                title=f'Recent Course {i}', slug=f'recent-course-{i}',
                status='published',
            )
            module = Module.objects.create(
                course=course, title=f'Module {i}', slug=f'module-{i}',
                sort_order=1,
            )
            completed_unit = Unit.objects.create(
                module=module, title=f'Done {i}', slug=f'done-{i}',
                sort_order=1,
            )
            Unit.objects.create(
                module=module, title=f'Next {i}', slug=f'next-{i}',
                sort_order=2,
            )
            self._enroll(self.user, course)
            UserCourseProgress.objects.create(
                user=self.user,
                unit=completed_unit,
                completed_at=now - timedelta(hours=i),
            )

        response = self.client.get('/')
        content = response.content.decode()

        self.assertEqual(len(response.context['in_progress_learning']), 3)
        self.assertEqual(response.context['hidden_learning_count'], 2)
        self.assertIn('Recent Course 0', content)
        self.assertIn('Recent Course 2', content)
        self.assertNotIn('Recent Course 3', content)
        self.assertContains(
            response,
            '2 more started items.',
        )
        self.assertContains(
            response,
            'data-testid="continue-learning-more"',
        )

    def test_continue_learning_mixes_courses_and_workshops_by_recent_activity(self):
        self._enroll(self.user, self.course)
        UserCourseProgress.objects.create(
            user=self.user,
            unit=self.units[0],
            completed_at=timezone.now() - timedelta(days=1),
        )
        workshop = Workshop.objects.create(
            title='Prompt Workshop',
            slug='prompt-workshop',
            status='published',
            date=date.today(),
            pages_required_level=0,
            recording_required_level=0,
        )
        page_1 = WorkshopPage.objects.create(
            workshop=workshop, title='Setup', slug='setup', sort_order=1,
        )
        WorkshopPage.objects.create(
            workshop=workshop, title='Build', slug='build', sort_order=2,
        )
        UserContentCompletion.objects.create(
            user=self.user,
            content_type=CONTENT_TYPE_WORKSHOP_PAGE,
            object_id=page_1.id,
            completed_at=timezone.now(),
        )

        response = self.client.get('/')
        content = response.content.decode()

        self.assertContains(response, 'Prompt Workshop')
        self.assertContains(response, '1/2 pages completed')
        self.assertLess(
            content.index('Prompt Workshop'),
            content.index('AI Basics'),
        )
        self.assertNotContains(response, 'View all courses')


class ContinueLearningCourseAccessTest(TierSetupMixin, TestCase):
    """Issue #275 — Continue Learning honors per-user CourseAccess grants.

    Previously, _get_in_progress_courses filtered by tier level only,
    hiding premium courses from free users even when an admin had
    granted explicit CourseAccess. The fix swaps the tier check for
    can_access(user, course) which also consults CourseAccess.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='granted@example.com', password='testpass',
        )
        cls.user.tier = cls.free_tier
        cls.user.save()

        # Premium-gated course with one unit so it can be "in progress"
        cls.course = Course.objects.create(
            title='Premium Course', slug='premium-course',
            status='published', required_level=LEVEL_PREMIUM,
        )
        module = Module.objects.create(
            course=cls.course, title='Module 1', slug='module-1', sort_order=1,
        )
        Unit.objects.create(
            module=module, title='Unit 1', slug='unit-1', sort_order=0,
        )

    def setUp(self):
        # Each test gets a fresh enrollment so they remain independent.
        Enrollment.objects.create(user=self.user, course=self.course)

    def test_in_progress_includes_granted_premium_course_for_free_user(self):
        # Free-tier user with a CourseAccess grant should see the course
        # in their Continue Learning widget.
        CourseAccess.objects.create(
            user=self.user, course=self.course, access_type='granted',
        )

        from content.views.home import _get_in_progress_courses
        result = _get_in_progress_courses(self.user, user_level=0)

        course_ids = [item['course'].id for item in result]
        self.assertIn(self.course.id, course_ids)

    def test_in_progress_excludes_premium_without_grant_or_tier(self):
        # Same enrollment, but no CourseAccess and no qualifying tier —
        # the course must stay hidden from the widget.
        from content.views.home import _get_in_progress_courses
        result = _get_in_progress_courses(self.user, user_level=0)

        course_ids = [item['course'].id for item in result]
        self.assertNotIn(self.course.id, course_ids)


# ============================================================
# Upcoming Events
# ============================================================


class UpcomingEventsTest(TierSetupMixin, TestCase):
    """Test the upcoming events section."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='eventuser@example.com', password='testpass',
        )
        self.client.login(email='eventuser@example.com', password='testpass')

    def test_empty_state_when_no_events(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertEqual(response.context['upcoming_events'], [])
        self.assertEqual(response.context['dashboard_upcoming_events'], [])
        self.assertNotIn('data-testid="dashboard-commitment-list"', content)
        self.assertNotIn('No upcoming events', content)

    # Pinned mid-week (issue #1462): ``_get_this_week_events`` truncates the
    # weekly zone at Sunday 23:59:59, so a relative ``now + 3 hours`` fixture
    # falls into next week when the suite runs late on a Sunday UTC.
    @freeze_time('2026-08-12T10:00:00Z')
    def test_shows_registered_upcoming_events(self):
        future = timezone.now() + timedelta(hours=3)
        event = Event.objects.create(
            slug='workshop-1', title='AI Workshop',
            start_datetime=future, status='upcoming',
        )
        EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')
        self.assertContains(response, 'AI Workshop')

    # Pinned mid-week for the same reason as the test above (issue #1462).
    @freeze_time('2026-08-12T10:00:00Z')
    def test_shows_event_date(self):
        future = timezone.now() + timedelta(hours=3)
        event = Event.objects.create(
            slug='dated-event', title='Dated Event',
            start_datetime=future, status='upcoming',
        )
        EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')
        # The dashboard fallback keeps UTC explicit when no preference exists.
        self.assertContains(response, 'UTC')

    @freeze_time('2026-06-17T12:00:00Z')
    def test_event_date_uses_valid_preferred_timezone_with_weekday(self):
        self.user.preferred_timezone = 'Europe/Berlin'
        self.user.save(update_fields=['preferred_timezone'])
        event = Event.objects.create(
            slug='berlin-dashboard-event',
            title='Berlin Dashboard Event',
            start_datetime=datetime(2026, 6, 19, 16, 0, tzinfo=UTC),
            status='upcoming',
        )
        EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')

        self.assertContains(
            response,
            'Fri, Jun 19, 2026, 18:00 Europe/Berlin',
        )
        self.assertEqual(
            response.context['upcoming_events'][0].dashboard_formatted_start,
            'Fri, Jun 19, 2026, 18:00 Europe/Berlin',
        )
        self.assertNotContains(response, 'June 19, 2026 at 16:00 UTC')

    @freeze_time('2026-06-17T12:00:00Z')
    def test_event_date_falls_back_to_explicit_utc_without_valid_timezone(self):
        self.user.preferred_timezone = 'Not/AZone'
        self.user.save(update_fields=['preferred_timezone'])
        event = Event.objects.create(
            slug='utc-dashboard-event',
            title='UTC Dashboard Event',
            start_datetime=datetime(2026, 6, 19, 16, 0, tzinfo=UTC),
            status='upcoming',
        )
        EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')

        self.assertContains(response, 'Fri, Jun 19, 2026, 16:00 UTC')
        self.assertNotContains(response, 'Europe/Berlin')

    def test_does_not_show_past_events(self):
        past = timezone.now() - timedelta(days=3)
        event = Event.objects.create(
            slug='past-event', title='Past Event',
            start_datetime=past, status='completed',
        )
        EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')
        self.assertNotContains(response, 'Past Event')

    def test_unregistered_event_is_not_in_your_week(self):
        future = timezone.now() + timedelta(hours=3)
        Event.objects.create(
            slug='other-event', title='Other Event',
            start_datetime=future, status='upcoming', published=True,
        )
        response = self.client.get('/')
        self.assertEqual(response.context['dashboard_upcoming_events'], [])
        self.assertContains(response, 'Other Event')
        self.assertContains(response, 'data-feed-kind="event"')

    @freeze_time('2026-08-11T10:00:00Z')
    def test_shows_all_registered_events_through_end_of_week(self):
        now = timezone.now()
        for i in range(5):
            event = Event.objects.create(
                slug=f'event-{i}', title=f'Event {i}',
                start_datetime=now + timedelta(days=i+1),
                status='upcoming',
            )
            EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')
        content = response.content.decode()
        self.assertEqual(
            [event.title for event in response.context['dashboard_upcoming_events']],
            [f'Event {i}' for i in range(5)],
        )
        for i in range(5):
            self.assertIn(f'Event {i}', content)

    @freeze_time('2026-08-11T10:00:00Z')
    def test_collapses_registered_series_to_earliest_occurrence(self):
        now = timezone.now()
        series = EventSeries.objects.create(
            name='LLM Zoomcamp 2026 office hours',
            slug='llm-zoomcamp-2026-office-hours',
            start_time=time(18, 0),
        )
        occurrences = []
        for i in range(3):
            event = Event.objects.create(
                slug=f'llm-office-hours-{i + 1}',
                title=f'LLM Office Hours Session {i + 1}',
                start_datetime=now + timedelta(days=i + 1),
                status='upcoming',
                event_series=series,
                series_position=i + 1,
            )
            EventRegistration.objects.create(user=self.user, event=event)
            occurrences.append(event)

        response = self.client.get('/')

        rows = response.context['dashboard_upcoming_events']
        self.assertEqual([event.pk for event in rows], [occurrences[0].pk])
        self.assertContains(response, 'LLM Office Hours Session 1')
        self.assertNotContains(response, 'LLM Office Hours Session 2')
        self.assertNotContains(response, 'LLM Office Hours Session 3')
        self.assertContains(response, 'Event series')
        self.assertContains(response, '2 more sessions')

    @freeze_time('2026-06-17T12:00:00Z')
    def test_collapsed_series_date_uses_preferred_timezone_with_weekday(self):
        self.user.preferred_timezone = 'Europe/Berlin'
        self.user.save(update_fields=['preferred_timezone'])
        series = EventSeries.objects.create(
            name='LLM Zoomcamp 2026 office hours',
            slug='llm-zoomcamp-series-timezone',
            start_time=time(18, 0),
        )
        starts = [
            datetime(2026, 6, 19, 16, 0, tzinfo=UTC),
            datetime(2026, 6, 26, 16, 0, tzinfo=UTC),
        ]
        for index, start_datetime in enumerate(starts, start=1):
            event = Event.objects.create(
                slug=f'llm-series-timezone-{index}',
                title=f'LLM Series Timezone Session {index}',
                start_datetime=start_datetime,
                status='upcoming',
                event_series=series,
                series_position=index,
            )
            EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')

        self.assertContains(response, 'LLM Series Timezone Session 1')
        self.assertNotContains(response, 'LLM Series Timezone Session 2')
        self.assertContains(
            response,
            'Fri, Jun 19, 2026, 18:00 Europe/Berlin',
        )

    @freeze_time('2026-08-10T10:00:00Z')
    def test_shows_entire_week_after_collapsing_series(self):
        now = timezone.now()
        series = EventSeries.objects.create(
            name='Dashboard Series', slug='dashboard-series',
            start_time=time(18, 0),
        )
        for i in range(3):
            event = Event.objects.create(
                slug=f'dashboard-series-{i + 1}',
                title=f'Dashboard Series Session {i + 1}',
                start_datetime=now + timedelta(days=i + 1),
                status='upcoming',
                event_series=series,
                series_position=i + 1,
            )
            EventRegistration.objects.create(user=self.user, event=event)
        standalone_1 = Event.objects.create(
            slug='standalone-after-series-1',
            title='Standalone After Series 1',
            start_datetime=now + timedelta(days=4),
            status='upcoming',
        )
        standalone_2 = Event.objects.create(
            slug='standalone-after-series-2',
            title='Standalone After Series 2',
            start_datetime=now + timedelta(days=5),
            status='upcoming',
        )
        standalone_3 = Event.objects.create(
            slug='standalone-after-series-3',
            title='Standalone After Series 3',
            start_datetime=now + timedelta(days=6),
            status='upcoming',
        )
        for event in [standalone_1, standalone_2, standalone_3]:
            EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')
        titles = [
            event.title
            for event in response.context['dashboard_upcoming_events']
        ]

        self.assertEqual(
            titles,
            [
                'Dashboard Series Session 1',
                'Standalone After Series 1',
                'Standalone After Series 2',
                'Standalone After Series 3',
            ],
        )
        self.assertNotContains(response, 'Dashboard Series Session 2')
        self.assertContains(response, 'Standalone After Series 3')

    def _assert_single_registered_series_occurrence(self):
        now = timezone.now()
        event_start = now + timedelta(days=6 - now.weekday())
        self.assertGreater(event_start, now)
        self.assertEqual(event_start.weekday(), 6)

        series = EventSeries.objects.create(
            name='One Session Series', slug='one-session-series',
            start_time=time(18, 0),
        )
        event = Event.objects.create(
            slug='one-session-series-event',
            title='One Session Series Event',
            start_datetime=event_start,
            status='upcoming',
            event_series=series,
            series_position=1,
        )
        EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')

        self.assertContains(response, 'One Session Series Event')
        self.assertContains(response, 'Event series')
        self.assertNotContains(
            response,
            '1 more session',
        )

    @freeze_time('2026-08-13T10:00:00Z')
    def test_single_registered_series_occurrence_has_marker_without_see_more(self):
        self._assert_single_registered_series_occurrence()

    @freeze_time('2026-08-14T10:00:00Z')
    def test_single_registered_series_occurrence_stays_in_week_on_friday(self):
        self._assert_single_registered_series_occurrence()


# ============================================================
# Weekly zone boundary contract (issue #1462)
# ============================================================


class WeeklyZoneBoundaryTest(TierSetupMixin, TestCase):
    """Pin the "Your week" boundary on every weekday.

    ``_get_this_week_events`` (``content/views/home.py``) truncates
    ``dashboard_upcoming_events`` at that week's Sunday 23:59:59 in the
    member's timezone, and
    ``templates/content/_dashboard_commitment_zones.html`` renders the
    "View all events" link only when that list is non-empty. Relative
    fixtures such as ``now + 2 days`` therefore change meaning with the
    wall-clock weekday, which is how issue #1462's Playwright failure
    only reproduced on Saturdays and Sundays. These tests walk all seven
    weekdays under a frozen clock so the boundary is asserted from both
    sides, deterministically and without a browser.
    """

    # Monday 2026-08-10 through Sunday 2026-08-16, one ISO week.
    FROZEN_WEEKDAYS = [
        ('Monday', '2026-08-10T10:00:00Z'),
        ('Tuesday', '2026-08-11T10:00:00Z'),
        ('Wednesday', '2026-08-12T10:00:00Z'),
        ('Thursday', '2026-08-13T10:00:00Z'),
        ('Friday', '2026-08-14T10:00:00Z'),
        ('Saturday', '2026-08-15T10:00:00Z'),
        ('Sunday', '2026-08-16T10:00:00Z'),
    ]
    # Last moment inside that week's rendered window.
    IN_WEEK_START = datetime(2026, 8, 16, 23, 0, tzinfo=UTC)
    # First moment after that week's Sunday 23:59:59.999999.
    NEXT_WEEK_START = datetime(2026, 8, 17, 0, 30, tzinfo=UTC)

    def setUp(self):
        self.user = User.objects.create_user(
            email='weekboundary@example.com', password='testpass',
        )
        self.client.login(
            email='weekboundary@example.com', password='testpass',
        )

    def _register_event(self, start_datetime):
        Event.objects.all().delete()
        event = Event.objects.create(
            slug='week-boundary-event',
            title='Week Boundary Event',
            start_datetime=start_datetime,
            status='upcoming',
        )
        EventRegistration.objects.create(user=self.user, event=event)
        return event

    def test_in_week_event_renders_view_all_events_on_every_weekday(self):
        for weekday, frozen_at in self.FROZEN_WEEKDAYS:
            with self.subTest(weekday=weekday), freeze_time(frozen_at):
                self.assertLess(timezone.now(), self.IN_WEEK_START)
                event = self._register_event(self.IN_WEEK_START)

                response = self.client.get('/')
                content = response.content.decode()

                self.assertEqual(
                    [row.pk for row in response.context[
                        'dashboard_upcoming_events'
                    ]],
                    [event.pk],
                )
                link = re.search(
                    r'<a href="(?P<href>[^"]+)"[^>]*>\s*View all events',
                    content,
                )
                self.assertIsNotNone(
                    link, 'The "View all events" link should be rendered.',
                )
                self.assertEqual(link.group('href'), '/events')

    def test_next_week_event_hides_view_all_events_on_every_weekday(self):
        for weekday, frozen_at in self.FROZEN_WEEKDAYS:
            with self.subTest(weekday=weekday), freeze_time(frozen_at):
                self.assertLess(timezone.now(), self.NEXT_WEEK_START)
                self._register_event(self.NEXT_WEEK_START)

                response = self.client.get('/')
                content = response.content.decode()

                self.assertEqual(
                    response.context['dashboard_upcoming_events'], [],
                )
                self.assertNotIn('View all events', content)
                # The member is not stranded: the home feed still offers
                # an /events destination.
                self.assertIsNotNone(
                    re.search(
                        r'<a href="/events"[^>]*'
                        r'data-testid="dashboard-feed-destination"[^>]*>\s*'
                        r'Events',
                        content,
                    ),
                    'The feed destinations should still link to /events.',
                )


# ============================================================
# Article feed entries
# ============================================================


class RecentContentTest(TierSetupMixin, TestCase):
    """Test article entries in the unified For you feed."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='reader@example.com', password='testpass',
        )
        self.client.login(email='reader@example.com', password='testpass')

    def test_empty_state_when_no_content(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertEqual(response.context['recent_content'], [])
        self.assertNotIn('No content available yet', content)
        self.assertNotIn('data-testid="recent-content-card"', content)

    def test_shows_published_articles(self):
        Article.objects.create(
            title='New Article', slug='new-article',
            description='Article desc', date=date.today(),
            published=True,
        )
        response = self.client.get('/')
        self.assertContains(response, 'New Article')

    def test_recordings_are_not_mixed_into_articles(self):
        Event.objects.create(
            title='New Recording', slug='new-recording',
            description='Recording desc', start_datetime=timezone.now(), status='completed', recording_url='https://youtube.com/watch?v=test',
            published=True,
        )
        response = self.client.get('/')
        self.assertNotContains(response, 'New Recording')

    def test_free_user_sees_gated_content_as_upgrade_opportunity(self):
        Article.objects.create(
            title='Premium Article', slug='premium-article',
            description='Premium desc', date=date.today(),
            published=True, required_level=LEVEL_PREMIUM,
        )
        response = self.client.get('/')
        self.assertContains(response, 'Premium Article')
        self.assertContains(response, 'data-feed-locked="true"')
        self.assertContains(response, 'Unlock with Premium')

    def test_shows_gated_content_for_premium_user(self):
        self.user.tier = self.premium_tier
        self.user.save()
        Article.objects.create(
            title='Premium Article', slug='premium-article',
            description='Premium desc', date=date.today(),
            published=True, required_level=LEVEL_PREMIUM,
        )
        response = self.client.get('/')
        self.assertContains(response, 'Premium Article')

    def test_max_3_articles(self):
        for i in range(8):
            Article.objects.create(
                title=f'Article {i}', slug=f'article-{i}',
                description=f'Desc {i}',
                date=date.today() - timedelta(days=i),
                published=True,
            )
        response = self.client.get('/')
        content = response.content.decode()
        # The article source contributes only the three most recent entries.
        self.assertIn('Article 0', content)
        self.assertIn('Article 2', content)
        self.assertNotIn('Article 3', content)

    def test_articles_are_sorted_by_date(self):
        Article.objects.create(
            title='Older Article', slug='older-article',
            description='Desc', date=date.today() - timedelta(days=5),
            published=True,
        )
        Article.objects.create(
            title='Newer Article', slug='newer-article',
            description='Desc', date=date.today(),
            published=True,
        )
        response = self.client.get('/')
        content = response.content.decode()
        pos_newer = content.index('Newer Article')
        pos_article = content.index('Older Article')
        self.assertLess(pos_newer, pos_article)


# ============================================================
# Poll feed entries
# ============================================================


class ActivePollsTest(TierSetupMixin, TestCase):
    """Test active poll entries in the unified For you feed."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='voter@example.com', password='testpass',
        )
        self.user.tier = self.main_tier
        self.user.save()
        self.client.login(email='voter@example.com', password='testpass')

    def test_empty_state_when_no_polls(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertEqual(response.context['active_polls'], [])
        self.assertNotIn('No active polls right now', content)
        self.assertNotIn('data-feed-kind="poll"', content)
        self.assertIn('data-testid="dashboard-home-feed"', content)
        self.assertIn('data-testid="dashboard-feed-destinations"', content)
        self.assertIn('href="/vote"', content)

    def test_shows_open_poll(self):
        Poll.objects.create(
            title='Favorite Framework', description='Vote here',
            poll_type='topic', status='open',
        )
        response = self.client.get('/')
        self.assertContains(response, 'Favorite Framework')
        self.assertContains(response, 'data-feed-kind="poll"')
        self.assertContains(response, 'href="/vote/')

    def test_does_not_show_closed_poll(self):
        Poll.objects.create(
            title='Old Poll', poll_type='topic', status='closed',
        )
        response = self.client.get('/')
        self.assertNotContains(response, 'Old Poll')

    def test_does_not_show_polls_above_user_level(self):
        # Premium poll for Main user
        Poll.objects.create(
            title='Premium Only Poll',
            poll_type='course',  # This sets required_level to LEVEL_PREMIUM
            status='open',
        )
        response = self.client.get('/')
        self.assertNotContains(response, 'Premium Only Poll')

    def test_max_2_polls(self):
        for i in range(4):
            Poll.objects.create(
                title=f'Poll {i}', poll_type='topic', status='open',
            )
        response = self.client.get('/')
        content = response.content.decode()
        # Two polls are available to downstream surfaces, while the home feed
        # stays social and varied by rendering only the newest one.
        self.assertEqual(len(response.context['active_polls']), 2)
        poll_count = sum(1 for i in range(4) if f'Poll {i}' in content)
        self.assertEqual(poll_count, 1)

    def test_does_not_show_expired_poll(self):
        past = timezone.now() - timedelta(days=1)
        Poll.objects.create(
            title='Expired Poll', poll_type='topic',
            status='open', closes_at=past,
        )
        response = self.client.get('/')
        self.assertNotContains(response, 'Expired Poll')


# ============================================================
# Quick Actions
# ============================================================


class QuickActionsTest(TierSetupMixin, TestCase):
    """Test the quick actions section."""

    def test_free_user_sees_browse_courses(self):
        Article.objects.create(
            title='Feed article', slug='feed-article',
            description='A useful article', date=date.today(), published=True,
        )
        User.objects.create_user(
            email='free@example.com', password='testpass',
        )
        self.client.login(email='free@example.com', password='testpass')
        response = self.client.get('/')
        self.assertEqual(
            [action['title'] for action in response.context['quick_actions']],
            ['Courses', 'Workshops', 'Events'],
        )
        for destination in [
            'Courses', 'Workshops', 'Events', 'Articles',
            'Sprints', 'Book Club', 'Polls',
        ]:
            self.assertContains(response, destination)
        destinations = re.search(
            r'<nav[^>]+data-testid="dashboard-feed-destinations".*?</nav>',
            response.content.decode(),
            re.DOTALL,
        )
        self.assertIsNotNone(destinations)
        self.assertNotIn('>Resources<', destinations.group(0))
        self.assertNotIn('>Projects<', destinations.group(0))

    def test_free_user_does_not_see_community(self):
        User.objects.create_user(
            email='free2@example.com', password='testpass',
        )
        self.client.login(email='free2@example.com', password='testpass')
        response = self.client.get('/')
        # Community should not appear for free users
        content = response.content.decode()
        # Check for the specific quick action Community card
        self.assertNotIn('Connect with other builders', content)

    def test_main_user_sees_activity_discovery(self):
        Article.objects.create(
            title='Main feed article', slug='main-feed-article',
            description='A useful article', date=date.today(), published=True,
        )
        user = User.objects.create_user(
            email='main@example.com', password='testpass',
        )
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, '>Sprints <')
        self.assertContains(response, 'href="/sprints"')

    def test_quick_action_urls_resolve_to_existing_routes(self):
        User.objects.create_user(
            email='routes@example.com', password='testpass',
        )
        self.client.login(email='routes@example.com', password='testpass')
        response = self.client.get('/')

        for action in response.context['quick_actions']:
            path = urlparse(action['url']).path
            try:
                resolve(path)
            except Resolver404 as exc:
                self.fail(f"{action['title']} links to missing route {path}: {exc}")

    def test_quick_actions_stay_scannable(self):
        user = User.objects.create_user(
            email='actions-main@example.com', password='testpass',
        )
        user.tier = self.main_tier
        user.save()
        self.client.login(email='actions-main@example.com', password='testpass')
        response = self.client.get('/')
        self.assertLessEqual(len(response.context['quick_actions']), 6)


# ============================================================
# Free Activation Surface
# ============================================================


class FreeActivationDashboardTest(TierSetupMixin, TestCase):
    """Free members get guided first steps and paid value teaser."""

    def _login_user(self, user):
        self.client.login(email=user.email, password='testpass')

    def _create_ai_hero(self):
        course = Course.objects.create(
            title='AI Hero',
            slug='aihero',
            status='published',
        )
        module = Module.objects.create(
            course=course,
            title='Start',
            slug='start',
            sort_order=1,
        )
        unit = Unit.objects.create(
            module=module,
            title='First build',
            slug='first-build',
            sort_order=1,
        )
        return course, unit

    def _create_active_sprint(self, slug='free-sprint'):
        return Sprint.objects.create(
            name='Free Sprint',
            slug=slug,
            start_date=timezone.localdate(),
            duration_weeks=4,
            status='active',
            min_tier_level=0,
        )

    def test_brand_new_free_member_sees_checklist_before_unlock_more(self):
        user = User.objects.create_user(
            email='free-activation@test.com',
            password='testpass',
            tier=self.free_tier,
        )
        self._login_user(user)

        response = self.client.get('/')

        self.assertTrue(response.context['show_free_activation_checklist'])
        self.assertTrue(response.context['show_free_plan_teaser'])
        self.assertFalse(response.context['show_onboarding_prompt'])
        content = response.content.decode()
        self.assertIn('data-testid="free-activation-checklist"', content)
        self.assertNotIn('data-testid="free-activation-dismiss"', content)
        self.assertIn('data-testid="free-plan-teaser"', content)
        self.assertLess(
            content.index('data-testid="free-activation-checklist"'),
            content.index('data-testid="free-plan-teaser"'),
        )
        self.assertContains(response, 'href="/courses/aihero"')
        self.assertContains(response, 'href="/events"')
        self.assertContains(response, 'href="/sprints"')
        self.assertContains(response, 'href="/membership"')
        self.assertContains(
            response, 'What community members are doing this month',
        )

        for item in response.context['free_activation_checklist_items']:
            path = urlparse(item['url']).path
            try:
                resolve(path)
            except Resolver404 as exc:
                self.fail(f"{item['title']} links to missing route {path}: {exc}")

    def test_checklist_completion_reflects_existing_activity(self):
        user = User.objects.create_user(
            email='free-progress@test.com',
            password='testpass',
            tier=self.free_tier,
        )
        course, unit = self._create_ai_hero()
        Enrollment.objects.create(user=user, course=course)
        UserCourseProgress.objects.create(user=user, unit=unit, completed_at=timezone.now())
        event = Event.objects.create(
            title='Open Event',
            slug='open-event',
            start_datetime=timezone.now() + timedelta(days=3),
            status='upcoming',
            required_level=0,
        )
        EventRegistration.objects.create(user=user, event=event)
        self._login_user(user)

        response = self.client.get('/')

        items = {
            item['key']: item
            for item in response.context['free_activation_checklist_items']
        }
        self.assertTrue(items['ai-hero']['completed'])
        self.assertTrue(items['events']['completed'])
        self.assertFalse(items['sprints']['completed'])
        self.assertContains(
            response,
            'data-testid="free-activation-completed-action-ai-hero"',
        )
        self.assertContains(
            response,
            'data-testid="free-activation-completed-action-events"',
        )
        self.assertNotContains(response, '>Done<')

        sprint = self._create_active_sprint(slug='engaged-sprint')
        SprintEnrollment.objects.create(sprint=sprint, user=user)
        response = self.client.get('/')
        items = {
            item['key']: item
            for item in response.context['free_activation_checklist_items']
        }
        self.assertTrue(items['sprints']['completed'])
        self.assertTrue(response.context['activation_checklist_all_complete'])
        self.assertContains(
            response, 'data-testid="free-activation-dismiss"',
        )

    def test_member_can_skip_an_individual_checklist_row(self):
        user = User.objects.create_user(
            email='free-skip@test.com',
            password='testpass',
            tier=self.free_tier,
        )
        self._login_user(user)

        initial = self.client.get('/')
        self.assertContains(
            initial, 'data-testid="free-activation-skip-ai-hero"',
        )

        skipped = self.client.post(
            '/account/api/dismiss-card',
            data=json.dumps({'card': 'getting_started_skip_ai_hero'}),
            content_type='application/json',
        )
        self.assertEqual(skipped.status_code, 200)

        response = self.client.get('/')
        items = {
            item['key']: item
            for item in response.context['free_activation_checklist_items']
        }
        self.assertTrue(items['ai-hero']['completed'])
        self.assertTrue(items['ai-hero']['skipped'])
        self.assertFalse(items['events']['completed'])
        self.assertEqual(response.context['free_activation_completed_count'], 1)
        self.assertContains(
            response,
            'data-testid="free-activation-completed-action-ai-hero"',
        )
        self.assertNotContains(
            response, 'data-testid="free-activation-skip-ai-hero"',
        )
        self.assertContains(
            response, 'data-testid="free-activation-skip-events"',
        )

    def test_active_sprint_plan_completes_sprint_checklist_item(self):
        user = User.objects.create_user(
            email='free-plan@test.com',
            password='testpass',
            tier=self.free_tier,
        )
        sprint = self._create_active_sprint(slug='planned-sprint')
        Plan.objects.create(member=user, sprint=sprint)
        self._login_user(user)

        response = self.client.get('/')

        self.assertTrue(response.context['show_free_activation_checklist'])
        self.assertTrue(response.context['show_free_plan_teaser'])
        items = {
            item['key']: item
            for item in response.context['free_activation_checklist_items']
        }
        self.assertTrue(items['sprints']['completed'])
        self.assertContains(response, 'data-testid="free-activation-checklist"')
        self.assertContains(response, 'data-testid="free-plan-teaser"')

    def test_basic_members_start_paid_checklist_with_onboarding(self):
        user = User.objects.create_user(
            email='basic-activation@test.com',
            password='testpass',
            tier=self.basic_tier,
        )
        self._login_user(user)

        response = self.client.get('/')

        self.assertTrue(response.context['show_onboarding_prompt'])
        self.assertFalse(response.context['show_free_activation_checklist'])
        self.assertTrue(response.context['show_activation_checklist'])
        self.assertFalse(response.context['show_free_plan_teaser'])
        self.assertContains(response, 'data-testid="onboarding-prompt"')
        self.assertContains(response, 'data-testid="free-activation-checklist"')
        self.assertEqual(
            [
                item['key']
                for item in response.context['free_activation_checklist_items']
            ],
            ['onboarding', 'ai-hero', 'events', 'sprints'],
        )

    def test_paid_member_with_plan_still_sees_onboarding_prompt(self):
        user = User.objects.create_user(
            email='planned-onboarding@test.com',
            password='testpass',
            tier=self.main_tier,
        )
        sprint = self._create_active_sprint(slug='planned-onboarding')
        Plan.objects.create(member=user, sprint=sprint, shared_at=timezone.now())
        self._login_user(user)

        response = self.client.get('/')

        self.assertTrue(response.context['show_onboarding_prompt'])
        self.assertContains(response, 'data-testid="onboarding-prompt"')
        self.assertContains(response, 'data-testid="account-sprint-plan-card"')

    def test_completed_free_checklist_reappears_with_paid_tasks_after_upgrade(self):
        user = User.objects.create_user(
            email='upgrade-checklist@test.com',
            password='testpass',
            tier=self.free_tier,
            dashboard_dismissals=['free_activation_sprint_guide_seen'],
        )
        course, _unit = self._create_ai_hero()
        Enrollment.objects.create(user=user, course=course)
        event = Event.objects.create(
            title='Free checklist event',
            slug='free-checklist-event',
            start_datetime=timezone.now() + timedelta(days=2),
            status='upcoming',
            required_level=0,
        )
        EventRegistration.objects.create(user=user, event=event)
        self._login_user(user)

        completed = self.client.get('/')
        self.assertTrue(completed.context['activation_checklist_all_complete'])
        self.assertContains(
            completed, 'data-testid="free-activation-dismiss"',
        )

        dismissed = self.client.post(
            '/account/api/dismiss-card',
            data=json.dumps({'card': 'free_activation_checklist'}),
            content_type='application/json',
        )
        self.assertEqual(dismissed.status_code, 200)
        self.assertNotContains(
            self.client.get('/'), 'data-testid="free-activation-checklist"',
        )

        user.tier = self.main_tier
        user.save(update_fields=['tier'])
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            upgraded = self.client.get('/')

        self.assertTrue(upgraded.context['show_activation_checklist'])
        items = {
            item['key']: item
            for item in upgraded.context['free_activation_checklist_items']
        }
        self.assertEqual(
            list(items),
            ['onboarding', 'slack', 'ai-hero', 'events', 'sprints'],
        )
        self.assertFalse(items['onboarding']['completed'])
        self.assertFalse(items['slack']['completed'])
        self.assertTrue(items['ai-hero']['completed'])
        self.assertTrue(items['events']['completed'])
        self.assertTrue(items['sprints']['completed'])
        self.assertEqual(upgraded.context['free_activation_completed_count'], 3)
        self.assertEqual(upgraded.context['free_activation_total_count'], 5)
        self.assertContains(upgraded, 'data-testid="onboarding-prompt"')
        self.assertContains(upgraded, 'data-testid="dashboard-slack-callout"')
        self.assertNotContains(
            upgraded, 'data-testid="free-activation-dismiss"',
        )

    def test_free_user_with_active_paid_override_uses_paid_dashboard(self):
        user = User.objects.create_user(
            email='override-activation@test.com',
            password='testpass',
            tier=self.free_tier,
        )
        TierOverride.objects.create(
            user=user,
            original_tier=self.free_tier,
            override_tier=self.main_tier,
            expires_at=timezone.now() + timedelta(days=7),
            is_active=True,
        )
        self._login_user(user)

        response = self.client.get('/')

        self.assertFalse(response.context['show_free_activation_checklist'])
        self.assertFalse(response.context['show_free_plan_teaser'])
        self.assertTrue(response.context['show_activation_checklist'])
        self.assertContains(response, 'data-testid="free-activation-checklist"')
        self.assertNotContains(response, 'data-testid="free-plan-teaser"')


# ============================================================
# Free Unlock Preview
# ============================================================


class FreeUnlockPreviewTest(TierSetupMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='free-unlock@test.com', password='testpass',
            tier=self.free_tier,
        )
        self.client.login(email=self.user.email, password='testpass')

    def test_current_book_uses_together_copy(self):
        Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=20,
            status=BOOK_STATUS_CURRENT, start_date=date.today(),
        )

        response = self.client.get('/')

        self.assertEqual(response.context['free_unlock_book_count'], 1)
        self.assertContains(response, 'Book Club:</span> We read 1 book together')

    def test_zero_current_books_omits_book_club_unlock_row(self):
        response = self.client.get('/')

        self.assertEqual(response.context['free_unlock_book_count'], 0)
        self.assertNotContains(response, 'Book Club:</span>')


# ============================================================
# Dashboard Template Structure
# ============================================================


class DashboardTemplateTest(TierSetupMixin, TestCase):
    """Test overall dashboard template structure."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='template@example.com', password='testpass',
            first_name='Template',
        )
        self.client.login(email='template@example.com', password='testpass')

    def test_dashboard_includes_header(self):
        response = self.client.get('/')
        self.assertContains(response, 'AI Shipping Labs')

    def test_dashboard_includes_footer(self):
        response = self.client.get('/')
        self.assertContains(response, 'AI Shipping Labs')
        self.assertContains(response, 'Version')

    def test_dashboard_has_baseline_sections(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('data-testid="dashboard-commitment-zones"', content)
        self.assertIn('data-testid="dashboard-getting-started"', content)
        self.assertIn('data-testid="dashboard-unlock-more"', content)

    def test_dashboard_body_has_no_duplicate_notifications_section(self):
        Notification.objects.create(
            user=self.user, title='Dashboard-only notification',
            body='This should only appear in notification surfaces.',
            url='/blog/new',
            read=False,
        )
        response = self.client.get('/')
        content = response.content.decode()

        self.assertNotIn('No new notifications', content)
        self.assertNotIn('Dashboard-only notification', content)
        self.assertNotIn('This should only appear in notification surfaces.', content)
        self.assertFalse(
            any('notifications' in context for context in response.context),
        )
        self.assertContains(response, 'data-testid="dashboard-commitment-zones"')

    def test_dashboard_extends_base(self):
        response = self.client.get('/')
        self.assertContains(response, '/static/css/tailwind.css')
        self.assertNotContains(response, 'cdn.tailwindcss.com')


# ============================================================
# Slack Community Section (issue #112)
# ============================================================


class SlackJoinPromptTest(TierSetupMixin, TestCase):
    """Test the Slack join prompt on the dashboard for Main+ users."""

    def _create_user(self, email, tier=None, slack_user_id='', slack_member=False):
        user = User.objects.create_user(email=email, password='testpass')
        if tier:
            user.tier = tier
            user.save()
        if slack_user_id:
            user.slack_user_id = slack_user_id
            user.save()
        if slack_member:
            user.slack_member = True
            user.save()
        return user

    def test_main_user_without_slack_sees_join_card(self):
        """Main tier users without slack_user_id see the join prompt."""
        self._create_user('main@test.com', tier=self.main_tier)
        self.client.login(email='main@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Join the member Slack workspace', content)
        self.assertIn('Join Slack', content)

    def test_premium_user_without_slack_sees_join_card(self):
        """Premium tier users without slack_user_id see the join prompt."""
        self._create_user('premium@test.com', tier=self.premium_tier)
        self.client.login(email='premium@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Join the member Slack workspace', content)

    def test_join_button_links_to_gated_endpoint_not_raw_invite(self):
        """Issue #953: the Join Slack button links to /community/slack and
        the raw SLACK_INVITE_URL never appears in the dashboard HTML."""
        self._create_user('main-link@test.com', tier=self.main_tier)
        self.client.login(email='main-link@test.com', password='testpass')
        invite_url = 'https://join.slack.com/t/aishippinglabs/shared_invite/abc123'
        with self.settings(SLACK_INVITE_URL=invite_url):
            response = self.client.get('/')
        anchor_match = re.search(
            r'<a[^>]*data-testid="slack-account-card-join"[^>]*>',
            response.content.decode(),
            re.DOTALL,
        )
        self.assertIsNotNone(anchor_match, 'Join Slack anchor must render')
        self.assertIn('href="/community/slack"', anchor_match.group(0))
        # The raw invite URL must not leak anywhere on the dashboard.
        self.assertNotContains(response, invite_url)

    def test_slack_connected_hides_card_on_dashboard(self):
        """Issue #729: when slack_member is True, the dashboard does NOT
        render the partial at all — neither the connected panel nor the
        join CTA. Issue #730 extended the same drop-when-connected
        treatment to /account/: connected members no longer see the
        redundant "Connected to Slack" panel there either."""
        # Issue #358: gate changed from slack_user_id to slack_member.
        self._create_user(
            'main-connected@test.com', tier=self.main_tier,
            slack_user_id='U12345', slack_member=True,
        )
        self.client.login(email='main-connected@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        # Dashboard: neither state renders.
        self.assertNotContains(response, 'Connected to Slack')
        self.assertNotContains(response, 'Join our Slack community')
        self.assertNotContains(response, 'data-testid="slack-account-card"')

        # Issue #730: /account/ also drops the slack card for connected
        # members — neither the connected panel nor the join CTA renders.
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            account_response = self.client.get('/account/')
        self.assertEqual(account_response.status_code, 200)
        self.assertNotContains(account_response, 'Connected to Slack')
        self.assertNotContains(account_response, 'Join our Slack community')
        self.assertNotContains(account_response, 'data-testid="slack-account-card"')

    def test_free_user_sees_no_slack_section(self):
        """Free tier users do not see any Slack-related content."""
        self._create_user('free@test.com', tier=self.free_tier)
        self.client.login(email='free@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        content = response.content.decode()
        self.assertNotIn('Join our Slack community', content)
        self.assertNotIn('Connected to Slack', content)
        # The dashboard Slack CTA card is absent. (The sitewide footer's
        # Join Slack link — #1356 — is unrelated and always present, so
        # the guard targets the dashboard card, not the raw label.)
        self.assertNotIn('data-testid="slack-account-card"', content)

    def test_basic_user_sees_no_slack_section(self):
        """Basic tier users do not see any Slack-related content."""
        self._create_user('basic@test.com', tier=self.basic_tier)
        self.client.login(email='basic@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        content = response.content.decode()
        self.assertNotIn('Join our Slack community', content)
        self.assertNotIn('Connected to Slack', content)

    def test_empty_slack_invite_url_hides_section(self):
        """When SLACK_INVITE_URL is empty, no Slack section is shown."""
        self._create_user('main-nourl@test.com', tier=self.main_tier)
        self.client.login(email='main-nourl@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL=''):
            response = self.client.get('/')
        content = response.content.decode()
        self.assertNotIn('Join our Slack community', content)
        # See note above: the dashboard Slack card is absent; the footer
        # Join Slack link (#1356) is unrelated and always present.
        self.assertNotIn('data-testid="slack-account-card"', content)

    def test_dashboard_renders_normally_when_slack_url_empty(self):
        """The rest of the dashboard renders without errors when SLACK_INVITE_URL is empty."""
        self._create_user('main-normal@test.com', tier=self.main_tier)
        self.client.login(email='main-normal@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL=''):
            response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('data-testid="dashboard-commitment-zones"', content)
        self.assertIn('Welcome back', content)

    def test_join_card_has_compact_explanatory_copy(self):
        """The join card explains the member workspace in one sentence."""
        self._create_user('main-note@test.com', tier=self.main_tier)
        self.client.login(email='main-note@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Ask questions and connect with other builders.', content)

    def test_context_variables_show_slack_join(self):
        """The show_slack_join context variable is True for qualifying users."""
        self._create_user('main-ctx@test.com', tier=self.main_tier)
        self.client.login(email='main-ctx@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        self.assertTrue(response.context['show_slack_join'])
        self.assertFalse(response.context['slack_connected'])
        # Issue #953: the context exposes the gated redirect path, not the
        # raw invite URL.
        self.assertEqual(
            response.context['slack_join_url'], '/community/slack',
        )
        self.assertNotIn('slack_invite_url', response.context)

    def test_context_variables_slack_connected(self):
        """The slack_connected context variable is True for verified members."""
        # Issue #358: gate changed from slack_user_id to slack_member.
        self._create_user(
            'main-ctx2@test.com', tier=self.main_tier,
            slack_user_id='U99999', slack_member=True,
        )
        self.client.login(email='main-ctx2@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        self.assertFalse(response.context['show_slack_join'])
        self.assertTrue(response.context['slack_connected'])

    def test_context_variables_free_user(self):
        """Free user has both show_slack_join and slack_connected as False."""
        self._create_user('free-ctx@test.com', tier=self.free_tier)
        self.client.login(email='free-ctx@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        self.assertFalse(response.context['show_slack_join'])
        self.assertFalse(response.context['slack_connected'])

    def test_slack_user_id_alone_does_not_hide_join_card(self):
        """Issue #358: having slack_user_id (e.g. from OAuth) without
        slack_member=True does NOT count as joined — the user can still
        have a Slack identity without being in our workspace."""
        self._create_user(
            'oauth-only@test.com', tier=self.main_tier,
            slack_user_id='U_OAUTH', slack_member=False,
        )
        self.client.login(email='oauth-only@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        # Join CTA still visible — slack_user_id alone is not workspace membership.
        self.assertTrue(response.context['show_slack_join'])
        self.assertFalse(response.context['slack_connected'])

    def test_slack_is_part_of_getting_started_before_continue(self):
        """The Slack task lives in Main member onboarding."""
        user = self._create_user('main-pos@test.com', tier=self.main_tier)
        course = Course.objects.create(
            title='Position Course', slug='position-course', status='published',
        )
        module = Module.objects.create(
            course=course, title='Position Module', slug='position-module',
            sort_order=1,
        )
        completed = Unit.objects.create(
            module=module, title='Completed Unit', slug='completed-unit',
            sort_order=1,
        )
        Unit.objects.create(
            module=module, title='Next Unit', slug='next-unit', sort_order=2,
        )
        Enrollment.objects.create(user=user, course=course)
        UserCourseProgress.objects.create(
            user=user, unit=completed, completed_at=timezone.now(),
        )
        self.client.login(email='main-pos@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        content = response.content.decode()
        pos_slack = content.index('Join the member Slack workspace')
        pos_continue = content.index('Continue learning')
        self.assertContains(response, 'data-testid="dashboard-getting-started"')
        self.assertLess(pos_slack, pos_continue)
