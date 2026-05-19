"""Tests for the shared ``Open in Django admin`` chip (issue #702).

Covers the ``admin_change_url`` template tag and the
``studio/includes/_admin_link.html`` partial, plus an end-to-end check
that the chip renders on the Studio detail/edit surfaces enumerated in
the issue and self-suppresses on the surfaces explicitly out of scope.
"""

from datetime import date, datetime, time

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase
from django.utils import timezone

from content.models import (
    Article,
    Course,
    Download,
    Module,
    Project,
    Unit,
    Workshop,
)
from crm.models import CRMRecord
from email_app.models import EmailCampaign
from events.models import Event, EventSeries
from integrations.models import Redirect, UtmCampaign, UtmCampaignLink
from plans.models import Plan, Sprint
from studio.templatetags.admin_links import admin_change_url

User = get_user_model()


class AdminChangeUrlTagTest(TestCase):
    """Direct unit tests for the ``admin_change_url`` simple tag."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='admin-tag@test.com', password='pw',
        )

    def test_returns_admin_url_for_registered_model(self):
        # ``accounts.User`` is registered in Django admin, so the tag
        # must return the canonical change URL.
        url = admin_change_url(self.user)
        self.assertEqual(url, f'/admin/accounts/user/{self.user.pk}/change/')

    def test_returns_empty_string_for_none(self):
        # Defensive: callers may pass a context variable that resolves
        # to ``None`` (e.g. a model that hasn't been hydrated yet).
        self.assertEqual(admin_change_url(None), '')

    def test_returns_empty_string_when_obj_has_no_pk(self):
        # Unsaved instances must not produce a broken URL with a
        # ``None`` pk segment.
        unsaved = User(email='unsaved@test.com')
        self.assertEqual(unsaved.pk, None)
        self.assertEqual(admin_change_url(unsaved), '')

    def test_returns_empty_string_for_unregistered_model(self):
        # ``events.EventSeries`` is the canonical out-of-scope surface
        # in issue #702 — the tag must self-suppress instead of raising
        # ``NoReverseMatch``.
        series = EventSeries.objects.create(
            name='Test series',
            slug='test-series-admin-link',
            start_time=time(14, 30),
        )
        self.assertEqual(admin_change_url(series), '')

    def test_returns_empty_string_for_non_model_object(self):
        # Strings/dicts/etc have no ``_meta`` and must not crash the
        # template; the tag must return an empty string so the partial
        # renders nothing.
        self.assertEqual(admin_change_url('hello'), '')
        self.assertEqual(admin_change_url({'pk': 1}), '')


class AdminLinkPartialTest(TestCase):
    """Render the partial directly and assert its contract.

    The partial is the shared chip every Studio detail/edit template
    includes. We render it via a tiny inline template so we don't
    depend on any particular Studio surface being reachable.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='partial@test.com', password='pw',
        )

    def _render(self, obj):
        template = Template(
            '{% include "studio/includes/_admin_link.html" with obj=obj %}'
        )
        return template.render(Context({'obj': obj}))

    def test_renders_chip_for_registered_model(self):
        out = self._render(self.user)
        # The chip carries the documented testid and label.
        self.assertIn('data-testid="studio-open-in-admin"', out)
        self.assertIn('Open in Django admin', out)
        self.assertIn(
            f'href="/admin/accounts/user/{self.user.pk}/change/"', out,
        )
        # Uses the wrench icon to distinguish from #667's pencil chip.
        self.assertIn('data-lucide="wrench"', out)

    def test_renders_nothing_for_unregistered_model(self):
        series = EventSeries.objects.create(
            name='Self-suppress',
            slug='self-suppress-admin-link',
            start_time=time(14, 30),
        )
        out = self._render(series).strip()
        # The chip must not render any visible content (no anchor, no
        # testid). Comments inside the partial are stripped by Django's
        # template engine.
        self.assertNotIn('data-testid="studio-open-in-admin"', out)
        self.assertNotIn('<a ', out)

    def test_renders_nothing_for_none(self):
        out = self._render(None).strip()
        self.assertNotIn('data-testid="studio-open-in-admin"', out)
        self.assertNotIn('<a ', out)


class _StudioBase(TestCase):
    """Shared staff session for surface tests."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-admin-link@test.com',
            password='pw',
            is_staff=True,
        )

    def setUp(self):
        self.client.login(
            email='staff-admin-link@test.com', password='pw',
        )

    def assertChipPresent(self, response, expected_href):
        """Assert the shared chip is present and points where we expect."""
        self.assertContains(response, 'data-testid="studio-open-in-admin"')
        self.assertContains(response, f'href="{expected_href}"')
        self.assertContains(response, 'Open in Django admin')

    def assertChipAbsent(self, response):
        self.assertNotContains(
            response, 'data-testid="studio-open-in-admin"',
        )


class ChipRendersOnContentSurfacesTest(_StudioBase):
    """End-to-end check: the chip renders on content Studio surfaces."""

    def test_chip_on_article_edit_links_to_content_admin(self):
        article = Article.objects.create(
            title='ART', slug='art-chip',
            date=date(2026, 1, 1),
        )
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/content/article/{article.pk}/change/',
        )

    def test_chip_on_course_edit_links_to_content_admin(self):
        course = Course.objects.create(
            title='Course-chip', slug='course-chip',
            status='published',
        )
        response = self.client.get(f'/studio/courses/{course.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/content/course/{course.pk}/change/',
        )

    def test_chip_on_unit_edit_links_to_content_admin(self):
        course = Course.objects.create(
            title='Course-unit-chip', slug='course-unit-chip',
            status='published',
        )
        module = Module.objects.create(
            course=course, title='M', sort_order=0,
        )
        unit = Unit.objects.create(
            module=module, title='U', slug='u-chip', sort_order=0,
        )
        response = self.client.get(f'/studio/units/{unit.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/content/unit/{unit.pk}/change/',
        )

    def test_chip_on_workshop_detail_links_to_content_admin(self):
        workshop = Workshop.objects.create(
            title='WS-chip', slug='ws-chip',
            date=date(2026, 1, 1),
        )
        response = self.client.get(f'/studio/workshops/{workshop.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/content/workshop/{workshop.pk}/change/',
        )

    def test_chip_on_workshop_edit_links_to_content_admin(self):
        workshop = Workshop.objects.create(
            title='WS-edit-chip', slug='ws-edit-chip',
            date=date(2026, 1, 1),
        )
        response = self.client.get(f'/studio/workshops/{workshop.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/content/workshop/{workshop.pk}/change/',
        )

    def test_chip_on_download_edit_links_to_content_admin(self):
        download = Download.objects.create(
            title='DL-chip', slug='dl-chip',
            file_url='https://example.com/file.pdf',
        )
        response = self.client.get(f'/studio/downloads/{download.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/content/download/{download.pk}/change/',
        )

    def test_chip_on_project_review_links_to_content_admin(self):
        project = Project.objects.create(
            title='Project-chip', slug='project-chip',
            date=date(2026, 1, 1),
            status='pending_review', published=False,
        )
        response = self.client.get(f'/studio/projects/{project.pk}/review')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/content/project/{project.pk}/change/',
        )


class ChipRendersOnEventSurfacesTest(_StudioBase):
    """End-to-end check: events app surfaces render the chip."""

    def test_chip_on_event_edit_links_to_events_admin(self):
        event = Event.objects.create(
            title='Event-chip', slug='event-chip',
            start_datetime=timezone.make_aware(datetime(2026, 6, 1, 10, 0)),
            end_datetime=timezone.make_aware(datetime(2026, 6, 1, 11, 0)),
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/events/event/{event.pk}/change/',
        )

    def test_chip_on_recording_edit_links_to_events_admin(self):
        # Recordings are stored on the Event model (recording_url
        # field). The Studio recording edit page therefore links to
        # /admin/events/event/<pk>/change/ -- same admin route as the
        # event edit page.
        event = Event.objects.create(
            title='Recording-chip', slug='recording-chip',
            start_datetime=timezone.make_aware(datetime(2026, 6, 1, 10, 0)),
        )
        response = self.client.get(f'/studio/recordings/{event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/events/event/{event.pk}/change/',
        )


class ChipRendersOnUsersAndPlansSurfacesTest(_StudioBase):
    """End-to-end check: accounts + plans app surfaces render the chip."""

    def test_chip_on_user_detail_links_to_accounts_admin(self):
        member = User.objects.create_user(
            email='member-link@test.com', password='pw',
        )
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/accounts/user/{member.pk}/change/',
        )
        # Issue #702 requires no duplicate chip in the action row
        # (the hand-built link was replaced by the partial).
        body = response.content.decode()
        self.assertEqual(
            body.count('data-testid="studio-open-in-admin"'), 1,
        )

    def test_chip_on_sprint_detail_links_to_plans_admin(self):
        sprint = Sprint.objects.create(
            name='Sprint A', slug='sprint-a-chip',
            start_date=date(2026, 1, 1),
        )
        response = self.client.get(f'/studio/sprints/{sprint.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/plans/sprint/{sprint.pk}/change/',
        )

    def test_chip_on_sprint_edit_links_to_plans_admin(self):
        sprint = Sprint.objects.create(
            name='Sprint B', slug='sprint-b-chip',
            start_date=date(2026, 1, 1),
        )
        response = self.client.get(f'/studio/sprints/{sprint.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/plans/sprint/{sprint.pk}/change/',
        )

    def test_chip_on_plan_detail_links_to_plans_admin(self):
        sprint = Sprint.objects.create(
            name='Sprint P', slug='sprint-p-chip',
            start_date=date(2026, 1, 1),
        )
        member = User.objects.create_user(
            email='plan-member@test.com', password='pw',
        )
        plan = Plan.objects.create(member=member, sprint=sprint)
        response = self.client.get(f'/studio/plans/{plan.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/plans/plan/{plan.pk}/change/',
        )


class ChipRendersOnEmailAndAnalyticsSurfacesTest(_StudioBase):
    """End-to-end check: email + utm/integrations surfaces render the chip."""

    def test_chip_on_campaign_detail_links_to_email_app_admin(self):
        campaign = EmailCampaign.objects.create(
            subject='Test campaign chip', body='Hello',
        )
        response = self.client.get(f'/studio/campaigns/{campaign.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response,
            f'/admin/email_app/emailcampaign/{campaign.pk}/change/',
        )

    def test_chip_on_campaign_edit_links_to_email_app_admin(self):
        campaign = EmailCampaign.objects.create(
            subject='Editable campaign chip', body='Hello',
        )
        response = self.client.get(f'/studio/campaigns/{campaign.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response,
            f'/admin/email_app/emailcampaign/{campaign.pk}/change/',
        )

    def test_chip_on_utm_campaign_detail_links_to_integrations_admin(self):
        utm = UtmCampaign.objects.create(
            name='Launch-chip', slug='launch_q1_chip',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )
        response = self.client.get(f'/studio/utm-campaigns/{utm.pk}/')
        self.assertEqual(response.status_code, 200)
        # UtmCampaign lives in the ``integrations`` app, so its admin
        # URL is /admin/integrations/utmcampaign/...
        self.assertChipPresent(
            response, f'/admin/integrations/utmcampaign/{utm.pk}/change/',
        )

    def test_chip_on_utm_campaign_edit_links_to_integrations_admin(self):
        utm = UtmCampaign.objects.create(
            name='Launch-edit-chip', slug='launch_edit_q1_chip',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )
        response = self.client.get(f'/studio/utm-campaigns/{utm.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/integrations/utmcampaign/{utm.pk}/change/',
        )

    def test_chip_on_utm_link_edit_links_to_integrations_admin(self):
        utm = UtmCampaign.objects.create(
            name='Links-chip', slug='links_q1_chip',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )
        link = UtmCampaignLink.objects.create(
            campaign=utm, utm_content='hero', label='Hero',
            destination='/hello',
        )
        response = self.client.get(
            f'/studio/utm-campaigns/{utm.pk}/links/{link.pk}/edit',
        )
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response,
            f'/admin/integrations/utmcampaignlink/{link.pk}/change/',
        )

    def test_chip_on_crm_detail_links_to_crm_admin(self):
        member = User.objects.create_user(
            email='crm-member-chip@test.com', password='pw',
        )
        record = CRMRecord.objects.create(
            user=member, created_by=self.staff, status='active',
        )
        response = self.client.get(f'/studio/crm/{record.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/crm/crmrecord/{record.pk}/change/',
        )

    def test_chip_on_redirect_edit_links_to_integrations_admin(self):
        redirect = Redirect.objects.create(
            source_path='/from-chip', target_path='/to-chip',
            redirect_type=301,
        )
        response = self.client.get(f'/studio/redirects/{redirect.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertChipPresent(
            response, f'/admin/integrations/redirect/{redirect.pk}/change/',
        )


class ChipDoesNotRenderWhenOutOfScopeTest(_StudioBase):
    """End-to-end check: surfaces explicitly out of scope render nothing."""

    def test_event_series_detail_does_not_render_chip(self):
        # ``events.EventSeries`` is the canonical out-of-scope model in
        # issue #702 (not registered in Django admin). The page must
        # still load, and the chip must NOT render.
        series = EventSeries.objects.create(
            name='Series-out-of-scope',
            slug='series-out-of-scope',
            start_time=time(14, 30),
        )
        response = self.client.get(f'/studio/event-series/{series.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertChipAbsent(response)

    def test_studio_dashboard_does_not_render_chip(self):
        # The dashboard is not about a specific object. The chip must
        # not appear anywhere on the page.
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertChipAbsent(response)

    def test_studio_article_list_does_not_render_chip(self):
        # Listing pages list many objects. The chip must not render
        # because there is no single ``obj`` to target.
        response = self.client.get('/studio/articles/')
        self.assertEqual(response.status_code, 200)
        self.assertChipAbsent(response)

    def test_studio_event_list_does_not_render_chip(self):
        response = self.client.get('/studio/events/')
        self.assertEqual(response.status_code, 200)
        self.assertChipAbsent(response)

    def test_studio_user_list_does_not_render_chip(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(response.status_code, 200)
        self.assertChipAbsent(response)
