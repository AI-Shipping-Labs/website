"""Tests for Logged-in User Home Dashboard - issue #104.

Covers:
- Anonymous users see the public marketing homepage (no change)
- Authenticated users see the personalized dashboard at /
- Welcome banner with user name and tier badge
- Continue learning section with in-progress courses
- Upcoming events section with registered events
- Recent content section with accessible articles/recordings
- Active polls section
- Quick actions section (community link for Main+ only)
- Empty states for all sections
"""

from datetime import date, timedelta
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import Resolver404, resolve
from django.utils import timezone

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
from events.models import Event, EventRegistration
from notifications.models import Notification
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

    def test_authenticated_user_sees_welcome_banner(self):
        self.client.login(email='test@example.com', password='testpass123')
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Welcome back', content)
        self.assertIn('Alice', content)


# ============================================================
# Welcome Banner
# ============================================================


class WelcomeBannerTest(TierSetupMixin, TestCase):
    """Test the welcome banner section of the dashboard."""

    def test_shows_first_name(self):
        User.objects.create_user(
            email='alice@example.com', password='testpass',
            first_name='Alice',
        )
        self.client.login(email='alice@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Welcome back, Alice')

    def test_shows_welcome_without_first_name(self):
        User.objects.create_user(
            email='noname@example.com', password='testpass',
        )
        self.client.login(email='noname@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Welcome back')
        # Should not have a trailing comma without a name
        self.assertNotContains(response, 'Welcome back,')

    def test_shows_tier_badge_free(self):
        User.objects.create_user(
            email='free@example.com', password='testpass',
        )
        self.client.login(email='free@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Free')

    def test_shows_tier_badge_main(self):
        user = User.objects.create_user(
            email='main@example.com', password='testpass',
        )
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@example.com', password='testpass')
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Main', content)

    def test_shows_tier_badge_premium(self):
        user = User.objects.create_user(
            email='prem@example.com', password='testpass',
        )
        user.tier = self.premium_tier
        user.save()
        self.client.login(email='prem@example.com', password='testpass')
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Premium', content)

    def test_has_account_link(self):
        User.objects.create_user(
            email='acct@example.com', password='testpass',
        )
        self.client.login(email='acct@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Account')

    def test_has_upgrade_link(self):
        User.objects.create_user(
            email='upgrade@example.com', password='testpass',
        )
        self.client.login(email='upgrade@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Upgrade')


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
        self.assertIn('No courses or workshops in progress yet', content)
        self.assertIn('Browse Courses', content)
        self.assertIn('Browse Workshops', content)

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
        self.assertIn('No courses or workshops in progress yet', content)

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

    def test_continue_button_aria_label_names_module_and_unit(self):
        # Aria label gives screen-reader users the destination unit name.
        self._enroll(self.user, self.course)
        now = timezone.now()
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0],
            completed_at=now,
        )
        response = self.client.get('/')
        # Next unfinished unit is units[1] = "Unit 2" in "Module 1".
        expected_label = 'aria-label="Continue with Module 1 — Unit 2"'
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
            '2 more started items hidden here',
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
        self.assertIn('No upcoming events', content)
        self.assertIn('Browse Events', content)

    def test_shows_registered_upcoming_events(self):
        future = timezone.now() + timedelta(days=3)
        event = Event.objects.create(
            slug='workshop-1', title='AI Workshop',
            start_datetime=future, status='upcoming',
        )
        EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')
        self.assertContains(response, 'AI Workshop')

    def test_shows_event_date(self):
        future = timezone.now() + timedelta(days=3)
        event = Event.objects.create(
            slug='dated-event', title='Dated Event',
            start_datetime=future, status='upcoming',
        )
        EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')
        # formatted_start returns something like "March 15, 2026 at 14:00 UTC"
        self.assertContains(response, 'UTC')

    def test_does_not_show_past_events(self):
        past = timezone.now() - timedelta(days=3)
        event = Event.objects.create(
            slug='past-event', title='Past Event',
            start_datetime=past, status='completed',
        )
        EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')
        self.assertNotContains(response, 'Past Event')

    def test_does_not_show_unregistered_events(self):
        future = timezone.now() + timedelta(days=3)
        Event.objects.create(
            slug='other-event', title='Other Event',
            start_datetime=future, status='upcoming',
        )
        response = self.client.get('/')
        self.assertNotContains(response, 'Other Event')

    def test_max_3_events(self):
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
        # Should show first 3 (soonest)
        self.assertIn('Event 0', content)
        self.assertIn('Event 1', content)
        self.assertIn('Event 2', content)
        self.assertNotIn('Event 3', content)
        self.assertNotIn('Event 4', content)


# ============================================================
# Recent Content
# ============================================================


class RecentContentTest(TierSetupMixin, TestCase):
    """Test the recent content section."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='reader@example.com', password='testpass',
        )
        self.client.login(email='reader@example.com', password='testpass')

    def test_empty_state_when_no_content(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('No content available yet', content)
        self.assertIn('Browse Blog', content)

    def test_shows_published_articles(self):
        Article.objects.create(
            title='New Article', slug='new-article',
            description='Article desc', date=date.today(),
            published=True,
        )
        response = self.client.get('/')
        self.assertContains(response, 'New Article')

    def test_shows_published_recordings(self):
        Event.objects.create(
            title='New Recording', slug='new-recording',
            description='Recording desc', start_datetime=timezone.now(), status='completed', recording_url='https://youtube.com/watch?v=test',
            published=True,
        )
        response = self.client.get('/')
        self.assertContains(response, 'New Recording')

    def test_does_not_show_gated_content_for_free_user(self):
        Article.objects.create(
            title='Premium Article', slug='premium-article',
            description='Premium desc', date=date.today(),
            published=True, required_level=LEVEL_PREMIUM,
        )
        response = self.client.get('/')
        self.assertNotContains(response, 'Premium Article')

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

    def test_max_5_items(self):
        for i in range(8):
            Article.objects.create(
                title=f'Article {i}', slug=f'article-{i}',
                description=f'Desc {i}',
                date=date.today() - timedelta(days=i),
                published=True,
            )
        response = self.client.get('/')
        content = response.content.decode()
        # Should show only 5 most recent
        self.assertIn('Article 0', content)
        self.assertIn('Article 4', content)
        self.assertNotIn('Article 5', content)

    def test_mixed_articles_and_recordings_sorted_by_date(self):
        Article.objects.create(
            title='Older Article', slug='older-article',
            description='Desc', date=date.today() - timedelta(days=5),
            published=True,
        )
        Event.objects.create(
            title='Newer Recording', slug='newer-recording',
            description='Desc', start_datetime=timezone.now(), status='completed', recording_url='https://youtube.com/watch?v=test',
            published=True,
        )
        response = self.client.get('/')
        content = response.content.decode()
        pos_recording = content.index('Newer Recording')
        pos_article = content.index('Older Article')
        self.assertLess(pos_recording, pos_article)


# ============================================================
# Active Polls
# ============================================================


class ActivePollsTest(TierSetupMixin, TestCase):
    """Test the active polls section."""

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
        self.assertIn('No active polls right now', content)
        self.assertIn('View past polls', content)

    def test_shows_open_poll(self):
        Poll.objects.create(
            title='Favorite Framework', description='Vote here',
            poll_type='topic', status='open',
        )
        response = self.client.get('/')
        self.assertContains(response, 'Favorite Framework')

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
        # Should show at most 2 polls
        poll_count = sum(1 for i in range(4) if f'Poll {i}' in content)
        self.assertEqual(poll_count, 2)

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
        User.objects.create_user(
            email='free@example.com', password='testpass',
        )
        self.client.login(email='free@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Browse Courses')
        self.assertContains(response, 'Browse Workshops')
        self.assertContains(response, 'Resources')
        self.assertContains(response, 'Events &amp; Recordings')
        self.assertContains(response, 'Projects')
        self.assertContains(response, 'Activities')

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
        user = User.objects.create_user(
            email='main@example.com', password='testpass',
        )
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Discover sprints and community activities')

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
        self.assertContains(response, 'All rights reserved')

    def test_dashboard_has_all_sections(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Continue Learning', content)
        self.assertIn('Upcoming Events', content)
        self.assertIn('Recent Content', content)
        self.assertIn('Active Polls', content)
        self.assertIn('Quick Actions', content)

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
        self.assertContains(response, 'Quick Actions')

    def test_dashboard_extends_base(self):
        response = self.client.get('/')
        # base.html includes Tailwind CDN
        self.assertContains(response, 'tailwindcss')


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
        self.assertIn('Join our Slack community', content)
        self.assertIn('Join Slack', content)

    def test_premium_user_without_slack_sees_join_card(self):
        """Premium tier users without slack_user_id see the join prompt."""
        self._create_user('premium@test.com', tier=self.premium_tier)
        self.client.login(email='premium@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Join our Slack community', content)

    def test_join_button_links_to_slack_invite_url(self):
        """The Join Slack button links to the configured SLACK_INVITE_URL."""
        self._create_user('main-link@test.com', tier=self.main_tier)
        self.client.login(email='main-link@test.com', password='testpass')
        invite_url = 'https://join.slack.com/t/aishippinglabs/shared_invite/abc123'
        with self.settings(SLACK_INVITE_URL=invite_url):
            response = self.client.get('/')
        content = response.content.decode()
        self.assertIn(invite_url, content)

    def test_join_button_opens_in_new_tab(self):
        """The Join Slack link has target=_blank and rel=noopener."""
        self._create_user('main-tab@test.com', tier=self.main_tier)
        self.client.login(email='main-tab@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('target="_blank"', content)
        self.assertIn('rel="noopener"', content)

    def test_slack_connected_replaces_join_card(self):
        """Once slack_member is True, show connected status instead of join."""
        # Issue #358: gate changed from slack_user_id to slack_member.
        self._create_user(
            'main-connected@test.com', tier=self.main_tier,
            slack_user_id='U12345', slack_member=True,
        )
        self.client.login(email='main-connected@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        content = response.content.decode()
        self.assertNotIn('Join our Slack community', content)
        self.assertIn('Connected to Slack', content)
        self.assertIn('AI Shipping Labs community workspace', content)

    def test_free_user_sees_no_slack_section(self):
        """Free tier users do not see any Slack-related content."""
        self._create_user('free@test.com', tier=self.free_tier)
        self.client.login(email='free@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        content = response.content.decode()
        self.assertNotIn('Join our Slack community', content)
        self.assertNotIn('Connected to Slack', content)
        self.assertNotIn('Join Slack', content)

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
        self.assertNotIn('Join Slack', content)

    def test_dashboard_renders_normally_when_slack_url_empty(self):
        """The rest of the dashboard renders without errors when SLACK_INVITE_URL is empty."""
        self._create_user('main-normal@test.com', tier=self.main_tier)
        self.client.login(email='main-normal@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL=''):
            response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Continue Learning', content)
        self.assertIn('Welcome back', content)

    def test_auto_linking_note_shown(self):
        """The join card includes a note about automatic linking."""
        self._create_user('main-note@test.com', tier=self.main_tier)
        self.client.login(email='main-note@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('up to an hour', content)

    def test_context_variables_show_slack_join(self):
        """The show_slack_join context variable is True for qualifying users."""
        self._create_user('main-ctx@test.com', tier=self.main_tier)
        self.client.login(email='main-ctx@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        self.assertTrue(response.context['show_slack_join'])
        self.assertFalse(response.context['slack_connected'])
        self.assertEqual(
            response.context['slack_invite_url'],
            'https://join.slack.com/test',
        )

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

    def test_slack_section_position_below_welcome_above_continue(self):
        """The Slack section appears between Welcome Banner and Continue Learning."""
        self._create_user('main-pos@test.com', tier=self.main_tier)
        self.client.login(email='main-pos@test.com', password='testpass')
        with self.settings(SLACK_INVITE_URL='https://join.slack.com/test'):
            response = self.client.get('/')
        content = response.content.decode()
        pos_welcome = content.index('Welcome back')
        pos_slack = content.index('Join our Slack community')
        pos_continue = content.index('Continue Learning')
        self.assertLess(pos_welcome, pos_slack)
        self.assertLess(pos_slack, pos_continue)
