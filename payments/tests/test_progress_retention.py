"""Tests for retaining user progress across subscription cancellation and renewal.

Issue #118: Cancellation revokes access, not data. Progress records
(UserCourseProgress, EventRegistration, Project) must survive tier changes
in both directions.

Covers:
- handle_subscription_deleted updates only tier/subscription fields, no deletions
- UserCourseProgress records persist after cancellation
- EventRegistration records persist after cancellation
- Project records persist after cancellation with submitter still set
- Dashboard "Continue Learning" hides gated courses after cancellation
- Dashboard "Continue Learning" restores gated courses after re-subscription
- Completion percentages and unit counts match before/after re-subscription
- Last completed unit link is correct after re-subscription
- Downgrade from Premium to Basic: Basic courses visible, Premium hidden
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from content.access import LEVEL_BASIC, LEVEL_MAIN, LEVEL_OPEN, LEVEL_PREMIUM
from content.models import (
    Course,
    Enrollment,
    Module,
    Project,
    Unit,
    UserCourseProgress,
)
from events.models import Event, EventRegistration
from payments.services import handle_checkout_completed, handle_subscription_deleted
from tests.fixtures import TierSetupMixin

User = get_user_model()


# ============================================================
# Unit test: subscription deletion does not cascade-delete progress
# ============================================================


class SubscriptionDeletedProgressRetentionTest(TierSetupMixin, TestCase):
    """handle_subscription_deleted preserves all progress-related records."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='progress@test.com', password='testpass',
        )
        self.user.tier = self.premium_tier
        self.user.subscription_id = 'sub_progress_test'
        self.user.stripe_customer_id = 'cus_progress_test'
        self.user.billing_period_end = timezone.now() + timedelta(days=30)
        self.user.save(update_fields=[
            'tier', 'subscription_id', 'stripe_customer_id', 'billing_period_end',
        ])

        # Create a Premium course with 4 units
        self.course = Course.objects.create(
            title='Premium Course', slug='premium-course',
            status='published', required_level=LEVEL_PREMIUM,
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

        # Issue #236: auto-enroll on first lesson complete is wired in
        # the API layer; these tests create UserCourseProgress directly,
        # so we mirror the production invariant by creating the
        # Enrollment here too.
        Enrollment.objects.create(user=self.user, course=self.course)

        # Mark 3 units as completed
        now = timezone.now()
        for i in range(3):
            UserCourseProgress.objects.create(
                user=self.user, unit=self.units[i],
                completed_at=now - timedelta(hours=3 - i),
            )

        # Create an event registration
        self.event = Event.objects.create(
            slug='future-event', title='Future Event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        EventRegistration.objects.create(user=self.user, event=self.event)

        # Create a project with the user as submitter
        self.project = Project.objects.create(
            title='My Project', slug='my-project',
            date=date.today(), submitter=self.user,
            status='published', published=True,
        )

    def _fire_subscription_deleted(self):
        """Simulate the customer.subscription.deleted webhook."""
        handle_subscription_deleted({
            'id': 'sub_progress_test',
            'customer': 'cus_progress_test',
        })
        self.user.refresh_from_db()

    def test_tier_reverted_to_free(self):
        self._fire_subscription_deleted()
        self.assertEqual(self.user.tier.slug, 'free')

    def test_subscription_id_cleared(self):
        self._fire_subscription_deleted()
        self.assertEqual(self.user.subscription_id, '')

    def test_billing_period_end_cleared(self):
        self._fire_subscription_deleted()
        self.assertIsNone(self.user.billing_period_end)

    def test_pending_tier_cleared(self):
        self.user.pending_tier = self.basic_tier
        self.user.save(update_fields=['pending_tier'])
        self._fire_subscription_deleted()
        self.assertIsNone(self.user.pending_tier)

    def test_course_progress_records_preserved(self):
        self._fire_subscription_deleted()
        count = UserCourseProgress.objects.filter(user=self.user).count()
        self.assertEqual(count, 3)

    def test_course_progress_completed_at_values_preserved(self):
        # Record original completed_at values
        original_times = list(
            UserCourseProgress.objects.filter(user=self.user)
            .order_by('unit__sort_order')
            .values_list('completed_at', flat=True)
        )
        self._fire_subscription_deleted()
        after_times = list(
            UserCourseProgress.objects.filter(user=self.user)
            .order_by('unit__sort_order')
            .values_list('completed_at', flat=True)
        )
        self.assertEqual(original_times, after_times)

    def test_event_registration_preserved(self):
        self._fire_subscription_deleted()
        count = EventRegistration.objects.filter(user=self.user).count()
        self.assertEqual(count, 1)

    def test_project_submitter_preserved(self):
        self._fire_subscription_deleted()
        self.project.refresh_from_db()
        self.assertEqual(self.project.submitter, self.user)

    def test_project_still_exists(self):
        self._fire_subscription_deleted()
        self.assertTrue(
            Project.objects.filter(pk=self.project.pk).exists()
        )

    def test_handler_only_updates_four_fields(self):
        """Verify handle_subscription_deleted only touches tier, subscription_id,
        billing_period_end, and pending_tier -- nothing else on the user."""
        original_email = self.user.email
        original_stripe_customer_id = self.user.stripe_customer_id
        self._fire_subscription_deleted()
        self.assertEqual(self.user.email, original_email)
        self.assertEqual(self.user.stripe_customer_id, original_stripe_customer_id)


# ============================================================
# Unit test: re-subscription restores tier without touching progress
# ============================================================


class ResubscriptionProgressRetentionTest(TierSetupMixin, TestCase):
    """After re-subscription, tier is restored and progress records are intact."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='resub@test.com', password='testpass',
        )
        self.user.tier = self.premium_tier
        self.user.subscription_id = 'sub_resub_test'
        self.user.stripe_customer_id = 'cus_resub_test'
        self.user.save(update_fields=[
            'tier', 'subscription_id', 'stripe_customer_id',
        ])

        # Create a Premium course with 4 units
        self.course = Course.objects.create(
            title='Premium Course', slug='premium-course',
            status='published', required_level=LEVEL_PREMIUM,
        )
        module = Module.objects.create(
            course=self.course, title='Module 1', slug='module-1', sort_order=1,
        )
        self.units = []
        for i in range(4):
            unit = Unit.objects.create(
                module=module, title=f'Unit {i+1}', slug=f'unit-{i+1}', sort_order=i,
            )
            self.units.append(unit)

        # Issue #236: mirror production auto-enroll behaviour.
        Enrollment.objects.create(user=self.user, course=self.course)

        # Mark 3 units as completed
        now = timezone.now()
        for i in range(3):
            UserCourseProgress.objects.create(
                user=self.user, unit=self.units[i],
                completed_at=now - timedelta(hours=3 - i),
            )

        # Cancel subscription
        handle_subscription_deleted({
            'id': 'sub_resub_test',
            'customer': 'cus_resub_test',
        })
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier.slug, 'free')

    def _fire_checkout_completed(self, tier_slug='premium'):
        """Simulate a checkout.session.completed webhook for re-subscription."""
        handle_checkout_completed({
            'id': 'cs_resub',
            'customer': 'cus_resub_test',
            'customer_details': {'email': 'resub@test.com'},
            'subscription': 'sub_resub_new',
            'client_reference_id': str(self.user.pk),
            'metadata': {'tier_slug': tier_slug, 'user_id': str(self.user.pk)},
        })
        self.user.refresh_from_db()

    def test_tier_restored_to_premium(self):
        self._fire_checkout_completed('premium')
        self.assertEqual(self.user.tier.slug, 'premium')

    def test_pending_tier_cleared(self):
        self._fire_checkout_completed('premium')
        self.assertIsNone(self.user.pending_tier)

    def test_progress_records_unchanged(self):
        self._fire_checkout_completed('premium')
        count = UserCourseProgress.objects.filter(user=self.user).count()
        self.assertEqual(count, 3)

    def test_completed_at_values_unchanged(self):
        original_times = list(
            UserCourseProgress.objects.filter(user=self.user)
            .order_by('unit__sort_order')
            .values_list('completed_at', flat=True)
        )
        self._fire_checkout_completed('premium')
        after_times = list(
            UserCourseProgress.objects.filter(user=self.user)
            .order_by('unit__sort_order')
            .values_list('completed_at', flat=True)
        )
        self.assertEqual(original_times, after_times)


# ============================================================
# Dashboard: Continue Learning after cancellation and re-subscription
# ============================================================


class DashboardProgressCancellationTest(TierSetupMixin, TestCase):
    """Dashboard hides gated course progress after cancellation and restores it after re-sub."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='dashboard@test.com', password='testpass',
        )
        self.user.tier = self.premium_tier
        self.user.subscription_id = 'sub_dashboard_test'
        self.user.stripe_customer_id = 'cus_dashboard_test'
        self.user.save(update_fields=[
            'tier', 'subscription_id', 'stripe_customer_id',
        ])
        self.client = Client()
        self.client.login(email='dashboard@test.com', password='testpass')

        # Create a Premium course with 4 units
        self.course = Course.objects.create(
            title='Premium ML Course', slug='premium-ml',
            status='published', required_level=LEVEL_PREMIUM,
        )
        module = Module.objects.create(
            course=self.course, title='Module 1', slug='module-1', sort_order=1,
        )
        self.units = []
        for i in range(4):
            unit = Unit.objects.create(
                module=module, title=f'Unit {i+1}', slug=f'unit-{i+1}', sort_order=i,
            )
            self.units.append(unit)

        # Issue #236: mirror production auto-enroll behaviour.
        Enrollment.objects.create(user=self.user, course=self.course)

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

    def test_premium_user_sees_progress_before_cancellation(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Premium ML Course', content)
        self.assertIn('2/4 units completed', content)

    def test_cancelled_user_does_not_see_gated_course_progress(self):
        handle_subscription_deleted({
            'id': 'sub_dashboard_test',
            'customer': 'cus_dashboard_test',
        })
        response = self.client.get('/')
        content = response.content.decode()
        self.assertNotIn('Premium ML Course', content)
        self.assertIn('No courses in progress yet', content)

    def test_cancelled_user_progress_records_still_in_db(self):
        handle_subscription_deleted({
            'id': 'sub_dashboard_test',
            'customer': 'cus_dashboard_test',
        })
        count = UserCourseProgress.objects.filter(user=self.user).count()
        self.assertEqual(count, 2)

    def test_resubscribed_user_sees_progress_restored(self):
        # Cancel
        handle_subscription_deleted({
            'id': 'sub_dashboard_test',
            'customer': 'cus_dashboard_test',
        })
        # Re-subscribe
        handle_checkout_completed({
            'id': 'cs_resub_dashboard',
            'customer': 'cus_dashboard_test',
            'customer_details': {'email': 'dashboard@test.com'},
            'subscription': 'sub_dashboard_new',
            'client_reference_id': str(self.user.pk),
            'metadata': {'tier_slug': 'premium', 'user_id': str(self.user.pk)},
        })
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Premium ML Course', content)
        self.assertIn('2/4 units completed', content)

    def test_resubscribed_user_sees_correct_percentage(self):
        handle_subscription_deleted({
            'id': 'sub_dashboard_test',
            'customer': 'cus_dashboard_test',
        })
        handle_checkout_completed({
            'id': 'cs_resub_pct',
            'customer': 'cus_dashboard_test',
            'customer_details': {'email': 'dashboard@test.com'},
            'subscription': 'sub_dashboard_pct',
            'client_reference_id': str(self.user.pk),
            'metadata': {'tier_slug': 'premium', 'user_id': str(self.user.pk)},
        })
        response = self.client.get('/')
        content = response.content.decode()
        # 2/4 = 50%
        self.assertIn('50%', content)

    def test_resubscribed_user_sees_last_completed_unit(self):
        handle_subscription_deleted({
            'id': 'sub_dashboard_test',
            'customer': 'cus_dashboard_test',
        })
        handle_checkout_completed({
            'id': 'cs_resub_last',
            'customer': 'cus_dashboard_test',
            'customer_details': {'email': 'dashboard@test.com'},
            'subscription': 'sub_dashboard_last',
            'client_reference_id': str(self.user.pk),
            'metadata': {'tier_slug': 'premium', 'user_id': str(self.user.pk)},
        })
        response = self.client.get('/')
        content = response.content.decode()
        # Unit 2 was completed most recently
        self.assertIn('Last: Unit 2', content)

    def test_continue_link_points_to_course(self):
        handle_subscription_deleted({
            'id': 'sub_dashboard_test',
            'customer': 'cus_dashboard_test',
        })
        handle_checkout_completed({
            'id': 'cs_resub_link',
            'customer': 'cus_dashboard_test',
            'customer_details': {'email': 'dashboard@test.com'},
            'subscription': 'sub_dashboard_link',
            'client_reference_id': str(self.user.pk),
            'metadata': {'tier_slug': 'premium', 'user_id': str(self.user.pk)},
        })
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('/courses/premium-ml', content)


# ============================================================
# Dashboard: event registrations after cancellation/re-subscription
# ============================================================


class DashboardEventRegistrationRetentionTest(TierSetupMixin, TestCase):
    """Event registrations persist through cancellation and re-subscription."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='eventuser@test.com', password='testpass',
        )
        self.user.tier = self.main_tier
        self.user.subscription_id = 'sub_event_test'
        self.user.stripe_customer_id = 'cus_event_test'
        self.user.save(update_fields=[
            'tier', 'subscription_id', 'stripe_customer_id',
        ])

        self.event = Event.objects.create(
            slug='workshop', title='AI Workshop',
            start_datetime=timezone.now() + timedelta(days=5),
            status='upcoming',
        )
        EventRegistration.objects.create(user=self.user, event=self.event)

    def test_registration_persists_after_cancellation(self):
        handle_subscription_deleted({
            'id': 'sub_event_test',
            'customer': 'cus_event_test',
        })
        count = EventRegistration.objects.filter(user=self.user).count()
        self.assertEqual(count, 1)

    def test_upcoming_event_visible_after_resubscription(self):
        handle_subscription_deleted({
            'id': 'sub_event_test',
            'customer': 'cus_event_test',
        })
        handle_checkout_completed({
            'id': 'cs_event_resub',
            'customer': 'cus_event_test',
            'customer_details': {'email': 'eventuser@test.com'},
            'subscription': 'sub_event_new',
            'client_reference_id': str(self.user.pk),
            'metadata': {'tier_slug': 'main', 'user_id': str(self.user.pk)},
        })
        self.client.login(email='eventuser@test.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'AI Workshop')


# ============================================================
# Dashboard: project submissions after cancellation
# ============================================================


class ProjectSubmissionRetentionTest(TierSetupMixin, TestCase):
    """Project submissions persist through cancellation."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='builder@test.com', password='testpass',
        )
        self.user.tier = self.main_tier
        self.user.subscription_id = 'sub_project_test'
        self.user.stripe_customer_id = 'cus_project_test'
        self.user.save(update_fields=[
            'tier', 'subscription_id', 'stripe_customer_id',
        ])

        self.project = Project.objects.create(
            title='Builder Project', slug='builder-project',
            date=date.today(), submitter=self.user,
            status='published', published=True,
        )

    def test_project_persists_after_cancellation(self):
        handle_subscription_deleted({
            'id': 'sub_project_test',
            'customer': 'cus_project_test',
        })
        self.assertTrue(
            Project.objects.filter(pk=self.project.pk).exists()
        )

    def test_submitter_still_set_after_cancellation(self):
        handle_subscription_deleted({
            'id': 'sub_project_test',
            'customer': 'cus_project_test',
        })
        self.project.refresh_from_db()
        self.assertEqual(self.project.submitter, self.user)

    def test_project_visible_on_listing(self):
        handle_subscription_deleted({
            'id': 'sub_project_test',
            'customer': 'cus_project_test',
        })
        response = self.client.get('/projects')
        self.assertContains(response, 'Builder Project')


# ============================================================
# Downgrade from Premium to Basic: access-based filtering
# ============================================================


class DowngradeTierProgressFilteringTest(TierSetupMixin, TestCase):
    """Downgrade from Premium to Basic: Basic courses visible, Premium hidden on dashboard."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='partial@test.com', password='testpass',
        )
        self.user.tier = self.premium_tier
        self.user.subscription_id = 'sub_partial_test'
        self.user.stripe_customer_id = 'cus_partial_test'
        self.user.save(update_fields=[
            'tier', 'subscription_id', 'stripe_customer_id',
        ])
        self.client_http = Client()
        self.client_http.login(email='partial@test.com', password='testpass')

        # Create a Basic course (required_level=10) with 3 units
        self.basic_course = Course.objects.create(
            title='Basic Intro', slug='basic-intro',
            status='published', required_level=LEVEL_BASIC,
        )
        basic_module = Module.objects.create(
            course=self.basic_course, title='B Module 1', slug='b-module-1', sort_order=1,
        )
        self.basic_units = []
        for i in range(3):
            unit = Unit.objects.create(
                module=basic_module, title=f'Basic Unit {i+1}', slug=f'basic-unit-{i+1}', sort_order=i,
            )
            self.basic_units.append(unit)

        # Create a Premium course (required_level=30) with 3 units
        self.premium_course = Course.objects.create(
            title='Premium Deep Dive', slug='premium-deep',
            status='published', required_level=LEVEL_PREMIUM,
        )
        premium_module = Module.objects.create(
            course=self.premium_course, title='P Module 1', slug='p-module-1', sort_order=1,
        )
        self.premium_units = []
        for i in range(3):
            unit = Unit.objects.create(
                module=premium_module, title=f'Prem Unit {i+1}', slug=f'prem-unit-{i+1}', sort_order=i,
            )
            self.premium_units.append(unit)

        # Issue #236: mirror production auto-enroll behaviour.
        Enrollment.objects.create(user=self.user, course=self.basic_course)
        Enrollment.objects.create(user=self.user, course=self.premium_course)

        # User has progress in both courses
        now = timezone.now()
        UserCourseProgress.objects.create(
            user=self.user, unit=self.basic_units[0],
            completed_at=now - timedelta(hours=3),
        )
        UserCourseProgress.objects.create(
            user=self.user, unit=self.premium_units[0],
            completed_at=now - timedelta(hours=2),
        )
        UserCourseProgress.objects.create(
            user=self.user, unit=self.premium_units[1],
            completed_at=now - timedelta(hours=1),
        )

    def test_premium_user_sees_both_courses(self):
        response = self.client_http.get('/')
        content = response.content.decode()
        self.assertIn('Basic Intro', content)
        self.assertIn('Premium Deep Dive', content)

    def test_after_downgrade_basic_course_visible(self):
        # Cancel then re-subscribe at Basic level
        handle_subscription_deleted({
            'id': 'sub_partial_test',
            'customer': 'cus_partial_test',
        })
        handle_checkout_completed({
            'id': 'cs_partial_basic',
            'customer': 'cus_partial_test',
            'customer_details': {'email': 'partial@test.com'},
            'subscription': 'sub_partial_basic',
            'client_reference_id': str(self.user.pk),
            'metadata': {'tier_slug': 'basic', 'user_id': str(self.user.pk)},
        })
        response = self.client_http.get('/')
        content = response.content.decode()
        self.assertIn('Basic Intro', content)

    def test_after_downgrade_premium_course_hidden(self):
        handle_subscription_deleted({
            'id': 'sub_partial_test',
            'customer': 'cus_partial_test',
        })
        handle_checkout_completed({
            'id': 'cs_partial_basic2',
            'customer': 'cus_partial_test',
            'customer_details': {'email': 'partial@test.com'},
            'subscription': 'sub_partial_basic2',
            'client_reference_id': str(self.user.pk),
            'metadata': {'tier_slug': 'basic', 'user_id': str(self.user.pk)},
        })
        response = self.client_http.get('/')
        content = response.content.decode()
        self.assertNotIn('Premium Deep Dive', content)

    def test_premium_progress_records_preserved_in_db_after_downgrade(self):
        handle_subscription_deleted({
            'id': 'sub_partial_test',
            'customer': 'cus_partial_test',
        })
        handle_checkout_completed({
            'id': 'cs_partial_basic3',
            'customer': 'cus_partial_test',
            'customer_details': {'email': 'partial@test.com'},
            'subscription': 'sub_partial_basic3',
            'client_reference_id': str(self.user.pk),
            'metadata': {'tier_slug': 'basic', 'user_id': str(self.user.pk)},
        })
        # Both sets of progress records still exist
        total = UserCourseProgress.objects.filter(user=self.user).count()
        self.assertEqual(total, 3)  # 1 basic + 2 premium

    def test_premium_course_unit_page_shows_gating_after_downgrade(self):
        handle_subscription_deleted({
            'id': 'sub_partial_test',
            'customer': 'cus_partial_test',
        })
        handle_checkout_completed({
            'id': 'cs_partial_basic4',
            'customer': 'cus_partial_test',
            'customer_details': {'email': 'partial@test.com'},
            'subscription': 'sub_partial_basic4',
            'client_reference_id': str(self.user.pk),
            'metadata': {'tier_slug': 'basic', 'user_id': str(self.user.pk)},
        })
        # Navigate directly to a Premium course unit page
        url = '/courses/premium-deep/p-module-1/prem-unit-1'
        response = self.client_http.get(url)
        # Should return 403 with gating overlay
        self.assertEqual(response.status_code, 403)


# ============================================================
# Resume course from last completed unit after re-subscription
# ============================================================


class ResumeCourseAfterResubTest(TierSetupMixin, TestCase):
    """After re-subscription, user can resume from the last completed unit."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='resume@test.com', password='testpass',
        )
        self.user.tier = self.main_tier
        self.user.subscription_id = 'sub_resume_test'
        self.user.stripe_customer_id = 'cus_resume_test'
        self.user.save(update_fields=[
            'tier', 'subscription_id', 'stripe_customer_id',
        ])
        self.client_http = Client()
        self.client_http.login(email='resume@test.com', password='testpass')

        # Create a Main-level course with 5 units
        self.course = Course.objects.create(
            title='Main Course', slug='main-course',
            status='published', required_level=LEVEL_MAIN,
        )
        module = Module.objects.create(
            course=self.course, title='Module 1', slug='module-1', sort_order=1,
        )
        self.units = []
        for i in range(5):
            unit = Unit.objects.create(
                module=module, title=f'Unit {i+1}', slug=f'unit-{i+1}', sort_order=i,
            )
            self.units.append(unit)

        # Issue #236: mirror production auto-enroll behaviour.
        Enrollment.objects.create(user=self.user, course=self.course)

        # Complete units 1 and 2, with unit 2 completed most recently
        now = timezone.now()
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0],
            completed_at=now - timedelta(hours=2),
        )
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[1],
            completed_at=now - timedelta(hours=1),
        )

    def test_last_completed_unit_shown_after_resub(self):
        # Cancel
        handle_subscription_deleted({
            'id': 'sub_resume_test',
            'customer': 'cus_resume_test',
        })
        # Re-subscribe
        handle_checkout_completed({
            'id': 'cs_resume_resub',
            'customer': 'cus_resume_test',
            'customer_details': {'email': 'resume@test.com'},
            'subscription': 'sub_resume_new',
            'client_reference_id': str(self.user.pk),
            'metadata': {'tier_slug': 'main', 'user_id': str(self.user.pk)},
        })
        response = self.client_http.get('/')
        content = response.content.decode()
        self.assertIn('Last: Unit 2', content)

    def test_completion_count_matches_after_resub(self):
        handle_subscription_deleted({
            'id': 'sub_resume_test',
            'customer': 'cus_resume_test',
        })
        handle_checkout_completed({
            'id': 'cs_resume_count',
            'customer': 'cus_resume_test',
            'customer_details': {'email': 'resume@test.com'},
            'subscription': 'sub_resume_count',
            'client_reference_id': str(self.user.pk),
            'metadata': {'tier_slug': 'main', 'user_id': str(self.user.pk)},
        })
        response = self.client_http.get('/')
        content = response.content.decode()
        # 2 of 5 units completed
        self.assertIn('2/5 units completed', content)

    def test_percentage_matches_after_resub(self):
        handle_subscription_deleted({
            'id': 'sub_resume_test',
            'customer': 'cus_resume_test',
        })
        handle_checkout_completed({
            'id': 'cs_resume_pct',
            'customer': 'cus_resume_test',
            'customer_details': {'email': 'resume@test.com'},
            'subscription': 'sub_resume_pct',
            'client_reference_id': str(self.user.pk),
            'metadata': {'tier_slug': 'main', 'user_id': str(self.user.pk)},
        })
        response = self.client_http.get('/')
        content = response.content.decode()
        # 2/5 = 40%
        self.assertIn('40%', content)


# ============================================================
# Open (free) course progress visible regardless of tier
# ============================================================


class OpenCourseProgressAlwaysVisibleTest(TierSetupMixin, TestCase):
    """Progress in open (required_level=0) courses remains visible after cancellation."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='free_course@test.com', password='testpass',
        )
        self.user.tier = self.main_tier
        self.user.subscription_id = 'sub_free_course'
        self.user.stripe_customer_id = 'cus_free_course'
        self.user.save(update_fields=[
            'tier', 'subscription_id', 'stripe_customer_id',
        ])
        self.client_http = Client()
        self.client_http.login(email='free_course@test.com', password='testpass')

        # Create a free course
        self.course = Course.objects.create(
            title='Free Course', slug='free-course',
            status='published', required_level=LEVEL_OPEN,
        )
        module = Module.objects.create(
            course=self.course, title='Module 1', slug='module-1', sort_order=1,
        )
        self.units = []
        for i in range(3):
            unit = Unit.objects.create(
                module=module, title=f'Free Unit {i+1}', slug=f'free-unit-{i+1}', sort_order=i,
            )
            self.units.append(unit)

        # Issue #236: mirror production auto-enroll behaviour.
        Enrollment.objects.create(user=self.user, course=self.course)

        # Complete 1 unit
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0],
            completed_at=timezone.now(),
        )

    def test_free_course_visible_after_cancellation(self):
        handle_subscription_deleted({
            'id': 'sub_free_course',
            'customer': 'cus_free_course',
        })
        response = self.client_http.get('/')
        content = response.content.decode()
        self.assertIn('Free Course', content)
        self.assertIn('1/3 units completed', content)
