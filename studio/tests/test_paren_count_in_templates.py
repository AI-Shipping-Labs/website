"""Integration tests for the ``paren_count`` rule in Studio templates
(issue #597).

These tests render the actual Studio pages and assert that:

- When the relevant counter is zero, the rendered HTML does NOT contain
  the empty ``Label (0)`` suffix or ``Label ()`` artifact.
- When the counter is positive, the suffix appears as ``Label (N)``.
- The label text itself is always present, so existing locators that
  match by label do not break.

Each scenario maps to one of the surfaces called out in the spec body:

- ``templates/studio/crm/list.html`` filter chips.
- ``templates/studio/courses/peer_reviews.html`` filter chips.

The user-import and utm-campaign-import result pages are tested
separately when straightforward to set up; their ``Errors`` /
``Warnings`` headings are already wrapped in ``{% if errors %}`` /
``{% if warnings %}`` so the zero branch never renders them at all.
That older safeguard is verified by the empty-state assertions below.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from crm.models import CRMRecord
from plans.models import Sprint

User = get_user_model()


class CrmListChipsTest(TestCase):
    """The CRM list filter chips show a paren count only when > 0."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='paren-staff@test.com', password='pw', is_staff=True,
        )
        cls.member1 = User.objects.create_user(
            email='paren-m1@test.com', password='pw',
        )
        cls.member2 = User.objects.create_user(
            email='paren-m2@test.com', password='pw',
        )

    def setUp(self):
        CRMRecord.objects.all().delete()
        self.client.login(email='paren-staff@test.com', password='pw')

    def test_archived_chip_has_no_paren_when_zero(self):
        # Two active records, zero archived.
        CRMRecord.objects.create(
            user=self.member1, created_by=self.staff, status='active',
        )
        CRMRecord.objects.create(
            user=self.member2, created_by=self.staff, status='active',
        )
        response = self.client.get('/studio/crm/')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        # Active chip shows "(2)".
        self.assertIn('data-testid="crm-filter-active"', html)
        self.assertIn('Active (2)', html)

        # All chip shows "(2)".
        self.assertIn('All (2)', html)

        # Archived chip does NOT show "(0)" — just the label.
        self.assertIn('data-testid="crm-filter-archived"', html)
        self.assertNotIn('Archived (0)', html)
        self.assertNotIn('Archived ()', html)

    def test_all_chips_have_no_paren_when_all_counts_are_zero(self):
        # Empty CRM — every count is 0.
        response = self.client.get('/studio/crm/')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        # Chip labels are still rendered.
        self.assertIn('data-testid="crm-filter-active"', html)
        self.assertIn('data-testid="crm-filter-archived"', html)
        self.assertIn('data-testid="crm-filter-all"', html)

        # No empty paren suffix anywhere on these chips.
        self.assertNotIn('Active (0)', html)
        self.assertNotIn('Archived (0)', html)
        self.assertNotIn('All (0)', html)
        self.assertNotIn('Active ()', html)
        self.assertNotIn('Archived ()', html)
        self.assertNotIn('All ()', html)

    def test_chips_show_paren_when_counts_are_positive(self):
        # One active, one archived (filter=all to load all rows).
        CRMRecord.objects.create(
            user=self.member1, created_by=self.staff, status='active',
        )
        CRMRecord.objects.create(
            user=self.member2, created_by=self.staff, status='archived',
        )
        response = self.client.get('/studio/crm/?filter=all')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        self.assertIn('Active (1)', html)
        self.assertIn('Archived (1)', html)
        self.assertIn('All (2)', html)


class PeerReviewChipsTest(TestCase):
    """The peer-review status chips show a paren count only when > 0."""

    @classmethod
    def setUpTestData(cls):
        from content.models import Course
        cls.staff = User.objects.create_user(
            email='peer-staff@test.com', password='pw', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Test course',
            slug='paren-count-course',
            status='published',
            peer_review_enabled=True,
        )

    def setUp(self):
        self.client.login(email='peer-staff@test.com', password='pw')

    def test_no_paren_on_chips_when_no_submissions(self):
        url = (
            f'/studio/courses/{self.course.pk}/peer-reviews'
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        # The "All" chip is always rendered; with zero submissions it
        # must NOT show an empty (0) suffix.
        self.assertIn('data-testid="status-filter-all"', html)
        self.assertNotIn('All (0)', html)
        self.assertNotIn('All ()', html)

        # Per-status chips with zero submissions also have no (0).
        self.assertNotIn(' (0)', html)


class StudioImportResultHeadingsTest(TestCase):
    """The Errors / Warnings headings on import result pages render via
    the shared paren_count rule. The historical {% if %} wrapper means
    the zero branch never renders the heading at all — both behaviors
    are acceptable per the spec's Acceptance Criteria.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='import-staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='import-staff@test.com', password='pw')

    def test_user_import_template_renders_warnings_with_paren_when_present(
        self,
    ):
        # Render the template directly with a synthetic context so we do
        # not have to drive a full multipart upload through the view.
        from django.template.loader import render_to_string
        html = render_to_string(
            'studio/users/import_result.html',
            {
                'created': 1,
                'updated': 0,
                'skipped': 0,
                'malformed': 0,
                'warnings': [(2, 'foo@bar', 'reason 1'),
                             (3, 'baz@qux', 'reason 2')],
                'tag': '',
                'tier_name': '',
            },
        )
        self.assertIn('Warnings (2)', html)

    def test_user_import_template_no_warnings_heading_when_empty(self):
        from django.template.loader import render_to_string
        html = render_to_string(
            'studio/users/import_result.html',
            {
                'created': 1,
                'updated': 0,
                'skipped': 0,
                'malformed': 0,
                'warnings': [],
                'tag': '',
                'tier_name': '',
            },
        )
        # Whether the heading renders at all is gated by
        # ``{% if warnings %}``; what matters is it never says "(0)".
        self.assertNotIn('Warnings (0)', html)
        self.assertNotIn('Warnings ()', html)

    def test_utm_campaign_import_template_renders_errors_with_paren(self):
        from django.template.loader import render_to_string
        html = render_to_string(
            'studio/utm_campaigns/import_result.html',
            {
                'campaigns_created': 0,
                'campaigns_matched': 0,
                'links_created': 0,
                'links_skipped': 0,
                'errors': [('https://x', 'bad'), ('https://y', 'bad')],
            },
        )
        self.assertIn('Errors (2)', html)

    def test_utm_campaign_import_template_no_errors_heading_when_empty(
        self,
    ):
        from django.template.loader import render_to_string
        html = render_to_string(
            'studio/utm_campaigns/import_result.html',
            {
                'campaigns_created': 1,
                'campaigns_matched': 0,
                'links_created': 1,
                'links_skipped': 0,
                'errors': [],
            },
        )
        self.assertNotIn('Errors (0)', html)
        self.assertNotIn('Errors ()', html)


class SprintEnrollResultHeadingsTest(TestCase):
    """The four result cards on bulk sprint enrollment must hide ``(0)``
    on empty buckets while still rendering the card and label."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='sprint-staff@test.com', password='pw', is_staff=True,
        )
        cls.sprint = Sprint.objects.create(
            name='Sprint A',
            slug='sprint-a',
            start_date=datetime.date(2026, 3, 1),
        )

    def test_template_with_only_enrolled_hides_other_zeros(self):
        from django.template.loader import render_to_string
        html = render_to_string(
            'studio/sprints/enroll.html',
            {
                'sprint': self.sprint,
                'required_tier_name': 'Basic',
                'enrollment_count': 5,
                'raw_emails': '',
                'results': {
                    'enrolled': ['a@x', 'b@x', 'c@x', 'd@x', 'e@x'],
                    'already_enrolled': [],
                    'under_tier': [],
                    'unknown_emails': [],
                },
            },
        )
        # Enrolled card shows the (5) suffix.
        self.assertIn('Enrolled (5)', html)
        # Other three cards: label present, no (0) suffix.
        self.assertIn('Already enrolled', html)
        self.assertNotIn('Already enrolled (0)', html)
        self.assertIn('Under-tier warning', html)
        self.assertNotIn('Under-tier warning (0)', html)
        self.assertIn('Unknown emails', html)
        self.assertNotIn('Unknown emails (0)', html)

    def test_template_with_no_results_does_not_render_cards_at_all(self):
        # When ``results`` is None the whole result grid is skipped, so
        # no heading at all — verify no zero-suffix leakage.
        from django.template.loader import render_to_string
        html = render_to_string(
            'studio/sprints/enroll.html',
            {
                'sprint': self.sprint,
                'required_tier_name': 'Basic',
                'enrollment_count': 0,
                'raw_emails': '',
                'results': None,
            },
        )
        self.assertNotIn('Enrolled (0)', html)
        self.assertNotIn('Already enrolled (0)', html)
        self.assertNotIn('Under-tier warning (0)', html)
        self.assertNotIn('Unknown emails (0)', html)
