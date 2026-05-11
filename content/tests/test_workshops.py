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
            'landing_required_level': 0,
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

    def test_clean_accepts_full_chain(self):
        """landing=0, pages=10, recording=20 — strict chain is valid."""
        ws = self._make_workshop(
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
        )
        ws.clean()  # no exception

    def test_clean_accepts_all_equal(self):
        """landing=pages=recording=10 — equal values are valid."""
        ws = self._make_workshop(
            landing_required_level=10,
            pages_required_level=10,
            recording_required_level=10,
        )
        ws.clean()  # no exception

    def test_clean_rejects_landing_above_pages(self):
        """landing > pages must raise with the landing field flagged."""
        ws = self._make_workshop(
            landing_required_level=20,
            pages_required_level=10,
            recording_required_level=20,
        )
        with self.assertRaises(ValidationError) as ctx:
            ws.clean()
        self.assertIn('landing_required_level', ctx.exception.error_dict)

    def test_clean_rejects_landing_above_recording(self):
        """landing > recording is caught (transitively via pages check).

        With landing=30, pages=30, recording=10 the pages<=recording edge
        also fails. Either edge surfacing is acceptable — what matters is
        that the row does not validate and one of the two failing fields
        is named in the error_dict.
        """
        ws = self._make_workshop(
            landing_required_level=30,
            pages_required_level=30,
            recording_required_level=10,
        )
        with self.assertRaises(ValidationError) as ctx:
            ws.clean()
        flagged = set(ctx.exception.error_dict.keys())
        self.assertTrue(
            flagged & {'landing_required_level', 'recording_required_level'},
            f'Expected landing_* or recording_* in error_dict, got: {flagged}',
        )

    def test_save_rejects_landing_above_pages(self):
        """save() must not persist a Workshop with landing > pages."""
        ws = self._make_workshop(
            slug='landing-above-pages',
            landing_required_level=20,
            pages_required_level=10,
            recording_required_level=20,
        )
        with self.assertRaises(ValidationError):
            ws.save()
        self.assertFalse(
            Workshop.objects.filter(slug='landing-above-pages').exists(),
            'Workshop with landing > pages must not be persisted.',
        )

    def test_landing_defaults_to_zero(self):
        """A Workshop created without landing_required_level gets 0."""
        ws = Workshop.objects.create(
            slug='landing-default',
            title='Default',
            date=date(2026, 4, 21),
            pages_required_level=10,
            recording_required_level=20,
        )
        self.assertEqual(ws.landing_required_level, 0)

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

    def test_user_can_access_landing_by_tier(self):
        """Landing=10, pages=20, recording=30 — check all four tiers."""
        workshop = Workshop.objects.create(
            slug='landing-gated',
            title='Landing Gated',
            date=date(2026, 4, 21),
            landing_required_level=10,   # Basic
            pages_required_level=20,     # Main
            recording_required_level=30,  # Premium
        )
        # Landing gate (10)
        self.assertFalse(workshop.user_can_access_landing(self.user_free))
        self.assertTrue(workshop.user_can_access_landing(self.user_basic))
        self.assertTrue(workshop.user_can_access_landing(self.user_main))
        self.assertTrue(workshop.user_can_access_landing(self.user_premium))
        # Pages gate (20)
        self.assertFalse(workshop.user_can_access_pages(self.user_basic))
        self.assertTrue(workshop.user_can_access_pages(self.user_main))
        self.assertTrue(workshop.user_can_access_pages(self.user_premium))
        # Recording gate (30)
        self.assertFalse(workshop.user_can_access_recording(self.user_main))
        self.assertTrue(workshop.user_can_access_recording(self.user_premium))

    def test_staff_bypasses_landing_gate(self):
        """Staff pass user_can_access_landing regardless of landing level."""
        workshop = Workshop.objects.create(
            slug='landing-premium',
            title='Premium Landing',
            date=date(2026, 4, 21),
            landing_required_level=30,
            pages_required_level=30,
            recording_required_level=30,
        )
        staff = User.objects.create_user(
            email='landing-staff@example.com', password='pw',
            tier=self.free_tier, is_staff=True,
        )
        self.assertTrue(workshop.user_can_access_landing(staff))


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


class WorkshopPageRequiredLevelTest(TierSetupMixin, TestCase):
    """Per-page ``required_level`` override (issue #571).

    Covers:
    - ``effective_required_level`` returns the override or falls back to
      the workshop default.
    - ``Workshop.user_can_access_pages(user, page=...)`` honours the
      override when set and matches the no-arg result otherwise.
    - ``WorkshopPage.clean()`` rejects an override below the workshop
      landing gate.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = Workshop.objects.create(
            slug='page-overrides',
            title='Page Overrides',
            date=date(2026, 4, 21),
            landing_required_level=0,
            pages_required_level=10,  # Basic by default
            recording_required_level=20,
        )
        cls.page_inherits = WorkshopPage.objects.create(
            workshop=cls.workshop,
            slug='deep-dive',
            title='Deep Dive',
            sort_order=2,
            body='Body.',
        )
        cls.page_open = WorkshopPage.objects.create(
            workshop=cls.workshop,
            slug='intro',
            title='Intro',
            sort_order=1,
            body='Body.',
            required_level=0,  # open override
        )
        cls.user_free = User.objects.create_user(
            email='free-page@example.com', password='pw',
            tier=cls.free_tier, email_verified=True,
        )

    def test_effective_level_inherits_when_null(self):
        # Page without an override resolves to the workshop default.
        self.assertIsNone(self.page_inherits.required_level)
        self.assertEqual(
            self.page_inherits.effective_required_level,
            self.workshop.pages_required_level,
        )

    def test_effective_level_uses_override_when_set(self):
        # Override beats the workshop default.
        self.assertEqual(self.page_open.required_level, 0)
        self.assertEqual(self.page_open.effective_required_level, 0)

    def test_user_can_access_pages_with_page_matches_noarg_when_null(self):
        # No-arg form gates against the workshop default; passing a page
        # that inherits must return the same result.
        no_arg = self.workshop.user_can_access_pages(self.user_free)
        with_page = self.workshop.user_can_access_pages(
            self.user_free, page=self.page_inherits,
        )
        self.assertEqual(no_arg, with_page)
        # And no-arg form again matches when page=None is passed.
        self.assertEqual(
            no_arg,
            self.workshop.user_can_access_pages(self.user_free, page=None),
        )

    def test_user_can_access_pages_honours_override(self):
        # Free user is blocked by the workshop-wide Basic gate.
        self.assertFalse(
            self.workshop.user_can_access_pages(self.user_free),
        )
        # But the open page lets them through.
        self.assertTrue(
            self.workshop.user_can_access_pages(
                self.user_free, page=self.page_open,
            ),
        )

    def test_anonymous_passes_open_override_but_blocked_on_default(self):
        # AnonymousUser is delivered as None.is_authenticated=False; the
        # easiest way to model it here is to call the helper with None.
        self.assertTrue(
            self.workshop.user_can_access_pages(None, page=self.page_open),
        )
        self.assertFalse(
            self.workshop.user_can_access_pages(
                None, page=self.page_inherits,
            ),
        )

    def test_clean_rejects_override_below_landing(self):
        # Workshop with landing=5 (registered); a page-level open (0)
        # would be more accessible than the landing → reject.
        ws = Workshop.objects.create(
            slug='strict-landing',
            title='Strict Landing',
            date=date(2026, 4, 21),
            landing_required_level=5,
            pages_required_level=5,
            recording_required_level=20,
        )
        page = WorkshopPage(
            workshop=ws,
            slug='intro',
            title='Intro',
            sort_order=1,
            body='Body.',
            required_level=0,
        )
        with self.assertRaises(ValidationError) as ctx:
            page.full_clean()
        self.assertIn('required_level', ctx.exception.error_dict)

    def test_save_rejects_override_below_landing(self):
        # Belt-and-braces: save() also enforces the invariant when
        # full_clean() is bypassed.
        ws = Workshop.objects.create(
            slug='save-strict',
            title='Save Strict',
            date=date(2026, 4, 21),
            landing_required_level=5,
            pages_required_level=5,
            recording_required_level=20,
        )
        page = WorkshopPage(
            workshop=ws,
            slug='intro',
            title='Intro',
            sort_order=1,
            body='Body.',
            required_level=0,
        )
        with self.assertRaises(ValidationError):
            page.save()
        self.assertFalse(
            WorkshopPage.objects.filter(workshop=ws, slug='intro').exists(),
            'Page with override below landing must not be persisted.',
        )

    def test_clean_accepts_override_equal_to_landing(self):
        # Equal is fine — landing == page == 5.
        ws = Workshop.objects.create(
            slug='equal-landing',
            title='Equal Landing',
            date=date(2026, 4, 21),
            landing_required_level=5,
            pages_required_level=10,
            recording_required_level=20,
        )
        page = WorkshopPage(
            workshop=ws,
            slug='intro',
            title='Intro',
            sort_order=1,
            body='Body.',
            required_level=5,
        )
        page.full_clean()  # must not raise


class WorkshopPagesDefaultRequiredLevelTest(TierSetupMixin, TestCase):
    """Issue #571: default pages_required_level dropped to LEVEL_REGISTERED (5).

    Existing rows keep their stored value; newly-created rows pick up the
    new default.
    """

    def test_new_workshop_defaults_to_registered(self):
        ws = Workshop.objects.create(
            slug='new-defaults',
            title='New Defaults',
            date=date(2026, 4, 21),
            recording_required_level=20,
        )
        # Default came from the field, not from kwargs — must be 5.
        self.assertEqual(ws.pages_required_level, 5)

    def test_existing_row_with_explicit_basic_unchanged(self):
        # A workshop created (or migrated) with the legacy Basic gate
        # must keep its value — no data drift.
        ws = Workshop.objects.create(
            slug='legacy-basic',
            title='Legacy Basic',
            date=date(2026, 4, 21),
            pages_required_level=10,
            recording_required_level=20,
        )
        ws.refresh_from_db()
        self.assertEqual(ws.pages_required_level, 10)
