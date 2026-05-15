"""Tests for the unified workshop/event materials behavior (issue #646).

Covers:
- Workshop.materials JSONField default behavior.
- Workshop.resolved_materials resolution rule
  (workshop-level wins over event-level, falls back to []).
- Workshop landing page renders Materials block from resolved_materials.
- Workshop video page renders Materials block from resolved_materials.
- Materials gate against the level that *authored* them
  (workshop -> pages_required_level, event -> recording_required_level)
  so an event-only list doesn't leak under a looser workshop pages gate.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Workshop
from events.models import Event
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_event(slug='ws-event', materials=None, recording_url=''):
    return Event.objects.create(
        slug=slug,
        title='Event for Workshop',
        start_datetime=timezone.now(),
        status='completed',
        kind='workshop',
        recording_url=recording_url,
        materials=materials or [],
        published=True,
    )


def _make_workshop(
    *,
    slug='ws',
    materials=None,
    event=None,
    landing=0,
    pages=0,
    recording=0,
    status='published',
):
    return Workshop.objects.create(
        slug=slug,
        title='Workshop',
        date=date(2026, 4, 21),
        status=status,
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
        description='Description body.',
        materials=materials if materials is not None else [],
        event=event,
    )


class WorkshopMaterialsFieldTest(TestCase):
    """Model-level resolution rule."""

    def test_workshop_materials_field_defaults_to_empty_list(self):
        # Create without passing materials= -- the field default kicks in.
        workshop = Workshop.objects.create(
            slug='no-mat',
            title='No materials',
            date=date(2026, 4, 21),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        self.assertEqual(workshop.materials, [])
        self.assertEqual(workshop.resolved_materials, [])

    def test_resolved_materials_prefers_workshop_over_event(self):
        event = _make_event(materials=[
            {'title': 'EVENT', 'url': 'https://example.com/event'},
        ])
        workshop = _make_workshop(
            materials=[
                {'title': 'WS', 'url': 'https://example.com/ws'},
            ],
            event=event,
        )
        resolved = workshop.resolved_materials
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]['title'], 'WS')
        self.assertEqual(workshop.materials_source, 'workshop')

    def test_resolved_materials_falls_back_to_event_when_workshop_empty(self):
        event = _make_event(materials=[
            {'title': 'EVENT', 'url': 'https://example.com/event'},
        ])
        workshop = _make_workshop(materials=[], event=event)
        resolved = workshop.resolved_materials
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]['title'], 'EVENT')
        self.assertEqual(workshop.materials_source, 'event')

    def test_resolved_materials_empty_when_no_workshop_and_no_event(self):
        workshop = _make_workshop(materials=[], event=None)
        self.assertEqual(workshop.resolved_materials, [])
        self.assertEqual(workshop.materials_source, '')

    def test_resolved_materials_empty_when_no_workshop_and_event_empty(self):
        event = _make_event(materials=[])
        workshop = _make_workshop(materials=[], event=event)
        self.assertEqual(workshop.resolved_materials, [])
        self.assertEqual(workshop.materials_source, '')


class WorkshopLandingMaterialsTest(TierSetupMixin, TestCase):
    """Workshop landing page renders materials from resolved_materials."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # All gates open so the landing branch tests focus on the
        # materials section itself, not on the upstream paywall.
        cls.workshop_with_materials = _make_workshop(
            slug='ws-mat',
            materials=[
                {'title': 'Slides',
                 'url': 'https://example.com/slides.pdf'},
                {'title': 'Repo',
                 'url': 'https://github.com/example/repo',
                 'type': 'code'},
            ],
        )

    def test_landing_renders_materials_from_workshop(self):
        response = self.client.get(
            f'/workshops/{self.workshop_with_materials.slug}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="workshop-materials"')
        self.assertContains(response, 'https://example.com/slides.pdf')
        self.assertContains(response, 'https://github.com/example/repo')
        self.assertContains(response, 'Slides')
        self.assertContains(response, 'Repo')

    def test_landing_omits_materials_when_pages_gated(self):
        # Pages gate is Basic (10). Anonymous (level 0) does NOT clear
        # it, so the materials section must be suppressed.
        workshop = _make_workshop(
            slug='gated-pages',
            pages=10,
            recording=20,
            materials=[
                {'title': 'Secret', 'url': 'https://example.com/secret'},
            ],
        )
        response = self.client.get(f'/workshops/{workshop.slug}')
        self.assertNotContains(response, 'data-testid="workshop-materials"')
        # The pages paywall is the single CTA.
        self.assertContains(response, 'data-testid="workshop-pages-paywall"')

    def test_landing_falls_back_to_event_materials(self):
        # Workshop materials empty, event has materials. Recording must
        # be open too (event materials gate against recording level) so
        # this test focuses on the fallback path itself.
        event = _make_event(slug='fb-evt', materials=[
            {'title': 'EventDoc', 'url': 'https://example.com/event-doc'},
        ])
        workshop = _make_workshop(
            slug='fb', materials=[], event=event,
            landing=0, pages=0, recording=0,
        )
        response = self.client.get(f'/workshops/{workshop.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="workshop-materials"')
        self.assertContains(response, 'https://example.com/event-doc')
        self.assertContains(response, 'EventDoc')

    def test_landing_no_materials_block_when_resolved_empty(self):
        bare = _make_workshop(slug='bare', materials=[], event=None)
        response = self.client.get(f'/workshops/{bare.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="workshop-materials"')
        # And no stray Materials heading anywhere on the page.
        self.assertNotContains(response, 'Materials</h2>')


class WorkshopVideoMaterialsTest(TierSetupMixin, TestCase):
    """Workshop video page renders materials from resolved_materials.

    The key invariant: workshop-level materials gate against
    ``pages_required_level``; event-level materials gate against
    ``recording_required_level``. This stops an event-only materials
    list from leaking under a looser workshop pages gate.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user_basic = User.objects.create_user(
            email='basic@test.com', password='pw', tier=cls.basic_tier,
        )
        cls.user_main = User.objects.create_user(
            email='main@test.com', password='pw', tier=cls.main_tier,
        )

    def test_video_renders_workshop_materials_under_pages_gate_not_recording_gate(self):
        # Workshop has materials; user clears pages (Basic) but NOT
        # recording (Main). Materials must still render because
        # workshop-level materials gate against pages.
        event = _make_event(
            slug='wmat-evt',
            recording_url='https://www.youtube.com/watch?v=abc',
            materials=[],
        )
        workshop = _make_workshop(
            slug='wmat',
            landing=0,
            pages=10,
            recording=20,
            materials=[
                {'title': 'Workbook',
                 'url': 'https://example.com/workbook.pdf'},
            ],
            event=event,
        )
        self.client.force_login(self.user_basic)
        response = self.client.get(f'/workshops/{workshop.slug}/video')
        # Recording is paywalled (Basic < Main) but materials still show.
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'data-testid="video-materials"', status_code=403,
        )
        self.assertContains(
            response, 'https://example.com/workbook.pdf', status_code=403,
        )

    def test_video_renders_event_materials_when_recording_accessible(self):
        # Workshop materials empty; event has materials. User has Main
        # tier (clears recording=20). Event-level materials show under
        # the recording gate.
        event = _make_event(
            slug='emat-evt',
            recording_url='https://www.youtube.com/watch?v=abc',
            materials=[
                {'title': 'Cheat sheet',
                 'url': 'https://example.com/cheat.pdf'},
            ],
        )
        workshop = _make_workshop(
            slug='emat',
            landing=0,
            pages=10,
            recording=20,
            materials=[],
            event=event,
        )
        self.client.force_login(self.user_main)
        response = self.client.get(f'/workshops/{workshop.slug}/video')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="video-materials"')
        self.assertContains(response, 'https://example.com/cheat.pdf')

    def test_video_event_materials_hidden_under_recording_gate(self):
        # Workshop materials empty; event has materials. User is Basic
        # (clears pages, fails recording). Event materials must NOT
        # render because they gate against recording_required_level.
        event = _make_event(
            slug='ehide-evt',
            recording_url='https://www.youtube.com/watch?v=abc',
            materials=[
                {'title': 'EventOnly',
                 'url': 'https://example.com/event-only'},
            ],
        )
        workshop = _make_workshop(
            slug='ehide',
            landing=0,
            pages=10,
            recording=20,
            materials=[],
            event=event,
        )
        self.client.force_login(self.user_basic)
        response = self.client.get(f'/workshops/{workshop.slug}/video')
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(
            response, 'data-testid="video-materials"', status_code=403,
        )
        self.assertNotContains(
            response, 'https://example.com/event-only', status_code=403,
        )

    def test_video_no_materials_block_when_resolved_empty(self):
        # No workshop materials, no event materials. The Materials
        # heading must not appear in any branch (player path either).
        event = _make_event(
            slug='nomat-evt',
            recording_url='https://www.youtube.com/watch?v=abc',
            materials=[],
        )
        workshop = _make_workshop(
            slug='nomat',
            landing=0,
            pages=0,
            recording=0,
            materials=[],
            event=event,
        )
        response = self.client.get(f'/workshops/{workshop.slug}/video')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="video-materials"')
        self.assertNotContains(response, 'Materials</h2>')


class WorkshopMaterialsOverrideTest(TierSetupMixin, TestCase):
    """Workshop materials must shadow the linked event's materials."""

    def test_workshop_materials_shadow_event_materials_on_landing(self):
        event = _make_event(
            slug='ov-evt',
            recording_url='https://www.youtube.com/watch?v=abc',
            materials=[
                {'title': 'OLD', 'url': 'https://example.com/old'},
            ],
        )
        workshop = _make_workshop(
            slug='ov',
            landing=0,
            pages=0,
            recording=0,
            materials=[
                {'title': 'NEW', 'url': 'https://example.com/new'},
            ],
            event=event,
        )
        response = self.client.get(f'/workshops/{workshop.slug}')
        self.assertContains(response, 'NEW')
        self.assertContains(response, 'https://example.com/new')
        self.assertNotContains(response, 'OLD')
        self.assertNotContains(response, 'https://example.com/old')

    def test_workshop_materials_shadow_event_materials_on_video(self):
        event = _make_event(
            slug='ov2-evt',
            recording_url='https://www.youtube.com/watch?v=abc',
            materials=[
                {'title': 'OLD', 'url': 'https://example.com/old'},
            ],
        )
        workshop = _make_workshop(
            slug='ov2',
            landing=0,
            pages=0,
            recording=0,
            materials=[
                {'title': 'NEW', 'url': 'https://example.com/new'},
            ],
            event=event,
        )
        response = self.client.get(f'/workshops/{workshop.slug}/video')
        self.assertContains(response, 'NEW')
        self.assertContains(response, 'https://example.com/new')
        self.assertNotContains(response, 'OLD')
        self.assertNotContains(response, 'https://example.com/old')


class WorkshopAdminMaterialsFormTest(TestCase):
    """Django admin exposes the materials field on the Workshop change form."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')
        self.workshop = _make_workshop(slug='admin-mat')

    def test_admin_workshop_form_exposes_materials_field(self):
        response = self.client.get(
            f'/admin/content/workshop/{self.workshop.pk}/change/',
        )
        self.assertEqual(response.status_code, 200)
        # Django renders JSONField as a <textarea name="materials">.
        self.assertContains(response, 'name="materials"')
