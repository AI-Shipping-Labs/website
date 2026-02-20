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
- Notifications section
- Empty states for all sections
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from content.access import LEVEL_OPEN, LEVEL_BASIC, LEVEL_MAIN, LEVEL_PREMIUM
from content.models import (
    Article, Recording, Course, Module, Unit, UserCourseProgress,
)
from events.models import Event, EventRegistration
from notifications.models import Notification
from voting.models import Poll, PollOption

User = get_user_model()


class TierSetupMixin:
    """Mixin that creates the four standard tiers."""

    @classmethod
    def setUpTestData(cls):
        from payments.models import Tier
        cls.free_tier, _ = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )
        cls.basic_tier, _ = Tier.objects.get_or_create(
            slug='basic', defaults={'name': 'Basic', 'level': 10},
        )
        cls.main_tier, _ = Tier.objects.get_or_create(
            slug='main', defaults={'name': 'Main', 'level': 20},
        )
        cls.premium_tier, _ = Tier.objects.get_or_create(
            slug='premium', defaults={'name': 'Premium', 'level': 30},
        )


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
        user = User.objects.create_user(
            email='alice@example.com', password='testpass',
            first_name='Alice',
        )
        self.client.login(email='alice@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Welcome back, Alice')

    def test_shows_welcome_without_first_name(self):
        user = User.objects.create_user(
            email='noname@example.com', password='testpass',
        )
        self.client.login(email='noname@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Welcome back')
        # Should not have a trailing comma without a name
        self.assertNotContains(response, 'Welcome back,')

    def test_shows_tier_badge_free(self):
        user = User.objects.create_user(
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
        user = User.objects.create_user(
            email='acct@example.com', password='testpass',
        )
        self.client.login(email='acct@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Account')

    def test_has_upgrade_link(self):
        user = User.objects.create_user(
            email='upgrade@example.com', password='testpass',
        )
        self.client.login(email='upgrade@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Upgrade')


# ============================================================
# Continue Learning
# ============================================================


class ContinueLearningTest(TierSetupMixin, TestCase):
    """Test the continue learning section."""

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
            course=self.course, title='Module 1', sort_order=1,
        )
        self.units = []
        for i in range(4):
            unit = Unit.objects.create(
                module=self.module, title=f'Unit {i+1}', sort_order=i,
            )
            self.units.append(unit)

    def test_empty_state_when_no_courses_in_progress(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('No courses in progress yet', content)
        self.assertIn('Browse Courses', content)

    def test_shows_in_progress_course(self):
        # Complete 2 of 4 units
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
        now = timezone.now()
        for i, unit in enumerate(self.units):
            UserCourseProgress.objects.create(
                user=self.user, unit=unit,
                completed_at=now - timedelta(hours=4-i),
            )
        response = self.client.get('/')
        content = response.content.decode()
        self.assertNotIn('AI Basics', content)
        self.assertIn('No courses in progress yet', content)

    def test_most_recently_accessed_first(self):
        # Create a second course
        course2 = Course.objects.create(
            title='ML Advanced', slug='ml-advanced', status='published',
        )
        module2 = Module.objects.create(
            course=course2, title='Module 2', sort_order=1,
        )
        unit_a = Unit.objects.create(
            module=module2, title='Adv Unit 1', sort_order=0,
        )
        unit_b = Unit.objects.create(
            module=module2, title='Adv Unit 2', sort_order=1,
        )

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
        Recording.objects.create(
            title='New Recording', slug='new-recording',
            description='Recording desc', date=date.today(),
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
        Recording.objects.create(
            title='Newer Recording', slug='newer-recording',
            description='Desc', date=date.today(),
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
        poll = Poll.objects.create(
            title='Favorite Framework', description='Vote here',
            poll_type='topic', status='open',
        )
        response = self.client.get('/')
        self.assertContains(response, 'Favorite Framework')

    def test_does_not_show_closed_poll(self):
        poll = Poll.objects.create(
            title='Old Poll', poll_type='topic', status='closed',
        )
        response = self.client.get('/')
        self.assertNotContains(response, 'Old Poll')

    def test_does_not_show_polls_above_user_level(self):
        # Premium poll for Main user
        poll = Poll.objects.create(
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
        poll = Poll.objects.create(
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
        user = User.objects.create_user(
            email='free@example.com', password='testpass',
        )
        self.client.login(email='free@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Browse Courses')
        self.assertContains(response, 'View Recordings')
        self.assertContains(response, 'Submit Project')

    def test_free_user_does_not_see_community(self):
        user = User.objects.create_user(
            email='free2@example.com', password='testpass',
        )
        self.client.login(email='free2@example.com', password='testpass')
        response = self.client.get('/')
        # Community should not appear for free users
        content = response.content.decode()
        # Check for the specific quick action Community card
        self.assertNotIn('Connect with other builders', content)

    def test_main_user_sees_community(self):
        user = User.objects.create_user(
            email='main@example.com', password='testpass',
        )
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Connect with other builders')


# ============================================================
# Notifications
# ============================================================


class NotificationsTest(TierSetupMixin, TestCase):
    """Test the notifications section."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='notif@example.com', password='testpass',
        )
        self.client.login(email='notif@example.com', password='testpass')

    def test_empty_state_when_no_notifications(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('No new notifications', content)

    def test_shows_unread_notifications(self):
        Notification.objects.create(
            user=self.user, title='New Article Published',
            body='Check it out', url='/blog/new',
            read=False,
        )
        response = self.client.get('/')
        self.assertContains(response, 'New Article Published')

    def test_does_not_show_read_notifications(self):
        Notification.objects.create(
            user=self.user, title='Already Read',
            url='/blog/old', read=True,
        )
        response = self.client.get('/')
        self.assertNotContains(response, 'Already Read')

    def test_max_5_notifications(self):
        for i in range(8):
            Notification.objects.create(
                user=self.user, title=f'Notification {i}',
                url=f'/n/{i}', read=False,
            )
        response = self.client.get('/')
        content = response.content.decode()
        # Should show at most 5
        notif_count = sum(
            1 for i in range(8) if f'Notification {i}' in content
        )
        self.assertEqual(notif_count, 5)

    def test_has_view_all_link(self):
        response = self.client.get('/')
        self.assertContains(response, '/notifications')


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
        self.assertIn('Notifications', content)

    def test_dashboard_extends_base(self):
        response = self.client.get('/')
        # base.html includes Tailwind CDN
        self.assertContains(response, 'tailwindcss')
