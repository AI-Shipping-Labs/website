"""Tests for the Studio bulk-enroll page (issue #443)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from payments.models import Tier
from plans.models import Sprint, SprintEnrollment

User = get_user_model()


def _make_user(email, *, tier_slug='premium'):
    user = User.objects.create_user(email=email, password='pw')
    user.tier = Tier.objects.get(slug=tier_slug)
    user.save(update_fields=['tier'])
    return user


class BulkEnrollAccessTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            min_tier_level=30,
        )

    def test_anonymous_redirects_to_login(self):
        response = self.client.get(
            f'/studio/sprints/{self.sprint.pk}/enroll',
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_non_staff_returns_403(self):
        member = User.objects.create_user(email='m@test.com', password='pw')
        self.client.force_login(member)
        response = self.client.get(
            f'/studio/sprints/{self.sprint.pk}/enroll',
        )
        self.assertEqual(response.status_code, 403)


class BulkEnrollClassificationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            min_tier_level=30,
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.premium = _make_user('premium@test.com', tier_slug='premium')
        cls.main = _make_user('main@test.com', tier_slug='main')
        cls.free = _make_user('free@test.com', tier_slug='free')
        # Pre-enroll premium so we exercise the "already enrolled"
        # bucket too.
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.premium)

    def setUp(self):
        self.client.force_login(self.staff)

    def test_get_renders_form(self):
        response = self.client.get(
            f'/studio/sprints/{self.sprint.pk}/enroll',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="bulk-enroll-emails"')

    def test_post_classifies_into_four_buckets(self):
        before = SprintEnrollment.objects.filter(sprint=self.sprint).count()
        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/enroll',
            data={
                'emails': (
                    'premium@test.com, main@test.com, '
                    'free@test.com\nunknown@nope.com'
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        results = response.context['results']
        self.assertEqual(results['enrolled'], ['main@test.com', 'free@test.com'])
        self.assertEqual(results['already_enrolled'], ['premium@test.com'])
        self.assertEqual(
            sorted(results['under_tier']),
            sorted(['main@test.com', 'free@test.com']),
        )
        self.assertEqual(results['unknown_emails'], ['unknown@nope.com'])
        # 2 new rows.
        self.assertEqual(
            SprintEnrollment.objects.filter(sprint=self.sprint).count(),
            before + 2,
        )

    def test_under_tier_rows_record_enrolled_by(self):
        self.client.post(
            f'/studio/sprints/{self.sprint.pk}/enroll',
            data={'emails': 'main@test.com'},
        )
        enrollment = SprintEnrollment.objects.get(
            sprint=self.sprint, user=self.main,
        )
        self.assertEqual(enrollment.enrolled_by_id, self.staff.pk)

    def test_unknown_email_creates_no_enrollment(self):
        before = SprintEnrollment.objects.filter(sprint=self.sprint).count()
        self.client.post(
            f'/studio/sprints/{self.sprint.pk}/enroll',
            data={'emails': 'noexists@nope.com'},
        )
        self.assertEqual(
            SprintEnrollment.objects.filter(sprint=self.sprint).count(),
            before,
        )

    def test_results_page_renders_four_result_blocks(self):
        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/enroll',
            data={
                'emails': (
                    'premium@test.com,main@test.com,'
                    'free@test.com,unknown@nope.com'
                ),
            },
        )
        self.assertContains(response, 'data-testid="bulk-enroll-result-enrolled"')
        self.assertContains(response, 'data-testid="bulk-enroll-result-already"')
        self.assertContains(response, 'data-testid="bulk-enroll-result-under-tier"')
        self.assertContains(response, 'data-testid="bulk-enroll-result-unknown"')


class StudioSprintDetailEnrollLinkTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_detail_shows_enroll_link_and_enrollment_count(self):
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-bulk-enroll-link"')
        self.assertContains(response, 'data-testid="sprint-enrollment-count"')


class StudioSprintCreateMinTierTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_create_defaults_to_premium_min_tier(self):
        response = self.client.post(
            '/studio/sprints/new',
            data={
                'name': 'Test', 'slug': 'test-sprint',
                'start_date': '2026-09-01', 'duration_weeks': '4',
                'status': 'draft',
                # No min_tier_level supplied -> defaults to 30 (Premium).
            },
        )
        self.assertEqual(response.status_code, 302)
        sprint = Sprint.objects.get(slug='test-sprint')
        self.assertEqual(sprint.min_tier_level, 30)

    def test_create_accepts_explicit_min_tier(self):
        response = self.client.post(
            '/studio/sprints/new',
            data={
                'name': 'Open', 'slug': 'open-pilot',
                'start_date': '2026-09-01', 'duration_weeks': '4',
                'status': 'draft',
                'min_tier_level': '0',
            },
        )
        self.assertEqual(response.status_code, 302)
        sprint = Sprint.objects.get(slug='open-pilot')
        self.assertEqual(sprint.min_tier_level, 0)
