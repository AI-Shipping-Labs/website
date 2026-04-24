"""Tests for Workshop content type (issue #295).

Covers:
- Workshop.clean() rejects recording gate < pages gate.
- Workshop.save() enforces the same invariant (belt-and-braces).
- Split gating: user_can_access_pages / user_can_access_recording across
  tier levels (0, 10, 20, 30).
- Admin smoke test: the Workshop changelist is reachable.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase

from content.models import Workshop, WorkshopPage
from tests.fixtures import TierSetupMixin

User = get_user_model()


class WorkshopModelValidationTest(TierSetupMixin, TestCase):
    """Gate-ordering invariant on Workshop."""

    def _make_workshop(self, **kwargs):
        defaults = {
            'slug': 'clean-test',
            'title': 'Clean Test',
            'date': date(2026, 4, 21),
            'pages_required_level': 10,
            'recording_required_level': 20,
        }
        defaults.update(kwargs)
        return Workshop(**defaults)

    def test_clean_accepts_equal_levels(self):
        ws = self._make_workshop(
            pages_required_level=10, recording_required_level=10,
        )
        ws.clean()  # no exception

    def test_clean_accepts_recording_stricter_than_pages(self):
        ws = self._make_workshop(
            pages_required_level=10, recording_required_level=30,
        )
        ws.clean()  # no exception

    def test_clean_rejects_recording_looser_than_pages(self):
        ws = self._make_workshop(
            pages_required_level=20, recording_required_level=10,
        )
        with self.assertRaises(ValidationError) as ctx:
            ws.clean()
        self.assertIn('recording_required_level', ctx.exception.error_dict)

    def test_save_rejects_inverted_gates(self):
        """save() enforces the invariant even when clean() is skipped."""
        ws = self._make_workshop(
            pages_required_level=30, recording_required_level=20,
        )
        with self.assertRaises(ValidationError):
            ws.save()
        self.assertFalse(
            Workshop.objects.filter(slug='clean-test').exists(),
            'Workshop with inverted gates must not be persisted.',
        )

    def test_save_renders_description_markdown(self):
        ws = Workshop.objects.create(
            slug='md-test',
            title='Markdown Test',
            date=date(2026, 4, 21),
            description='Hello **bold** world',
            pages_required_level=0,
            recording_required_level=0,
        )
        self.assertIn('<strong>bold</strong>', ws.description_html)

    def test_save_normalizes_tags(self):
        ws = Workshop.objects.create(
            slug='tags-test',
            title='Tags Test',
            date=date(2026, 4, 21),
            tags=['Machine Learning', 'Python 3.12'],
            pages_required_level=0,
            recording_required_level=0,
        )
        self.assertEqual(ws.tags, ['machine-learning', 'python-312'])


class WorkshopSplitGatingTest(TierSetupMixin, TestCase):
    """Verify the split-level access helpers across tier levels."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = Workshop.objects.create(
            slug='gating',
            title='Gating',
            date=date(2026, 4, 21),
            pages_required_level=10,  # Basic
            recording_required_level=20,  # Main
        )
        cls.user_free = User.objects.create_user(
            email='free@example.com', password='pw', tier=cls.free_tier,
        )
        cls.user_basic = User.objects.create_user(
            email='basic@example.com', password='pw', tier=cls.basic_tier,
        )
        cls.user_main = User.objects.create_user(
            email='main@example.com', password='pw', tier=cls.main_tier,
        )
        cls.user_premium = User.objects.create_user(
            email='premium@example.com', password='pw', tier=cls.premium_tier,
        )

    def test_free_user_fails_both_gates(self):
        self.assertFalse(self.workshop.user_can_access_pages(self.user_free))
        self.assertFalse(
            self.workshop.user_can_access_recording(self.user_free),
        )

    def test_basic_user_passes_pages_fails_recording(self):
        self.assertTrue(self.workshop.user_can_access_pages(self.user_basic))
        self.assertFalse(
            self.workshop.user_can_access_recording(self.user_basic),
        )

    def test_main_user_passes_both(self):
        self.assertTrue(self.workshop.user_can_access_pages(self.user_main))
        self.assertTrue(
            self.workshop.user_can_access_recording(self.user_main),
        )

    def test_premium_user_passes_both(self):
        self.assertTrue(self.workshop.user_can_access_pages(self.user_premium))
        self.assertTrue(
            self.workshop.user_can_access_recording(self.user_premium),
        )

    def test_staff_bypasses_gates(self):
        """Staff/superusers pass both gates via get_user_level."""
        staff = User.objects.create_user(
            email='staff@example.com', password='pw',
            tier=self.free_tier, is_staff=True,
        )
        self.assertTrue(self.workshop.user_can_access_pages(staff))
        self.assertTrue(self.workshop.user_can_access_recording(staff))


class WorkshopAdminSmokeTest(TierSetupMixin, TestCase):
    """Admin changelist loads with a seeded workshop in the list."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin_user = User.objects.create_superuser(
            email='admin@example.com', password='pw',
        )
        cls.workshop = Workshop.objects.create(
            slug='admin-smoke',
            title='Admin Smoke Workshop',
            date=date(2026, 4, 21),
            pages_required_level=0,
            recording_required_level=0,
        )

    def test_workshop_changelist_loads(self):
        client = Client()
        client.force_login(self.admin_user)
        response = client.get('/admin/content/workshop/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Admin Smoke Workshop')

    def test_workshop_page_changelist_loads(self):
        WorkshopPage.objects.create(
            workshop=self.workshop,
            slug='overview',
            title='Overview',
            sort_order=1,
            body='Welcome.',
        )
        client = Client()
        client.force_login(self.admin_user)
        response = client.get('/admin/content/workshoppage/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Overview')
