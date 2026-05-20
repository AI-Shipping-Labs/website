"""Tests for the ``Open in Studio`` link on Django admin pages (issue #727).

Reverse direction of issue #702 (which added an ``Open in Django admin``
chip to Studio surfaces). For each of the 16 admin classes listed in the
issue inventory, assert that the change form AND the changelist render
an ``Open in Studio`` anchor that points at the matching Studio URL,
opens in a new tab, and carries ``rel="noopener"``.

Also covers:

- The ``studio_link`` helper's defensive guards (``None`` / unsaved obj
  / ``NoReverseMatch``).
- An admin NOT in the inventory (``WorkshopPageAdmin``) does not render
  the link.
- A live monkeypatched URL name on a registered admin renders the
  change view at 200 (not 500) with an empty link cell, exercising the
  ``NoReverseMatch`` self-suppression on a real request path.
"""

from datetime import date, datetime, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ImportBatch
from content.models import (
    Article,
    Course,
    Download,
    Module,
    Project,
    Unit,
    Workshop,
    WorkshopPage,
)
from crm.models import CRMRecord
from email_app.models import EmailCampaign
from events.models import Event
from integrations.models import Redirect, UtmCampaign, UtmCampaignLink
from plans.models import Plan, Sprint
from studio.admin_links import studio_link

User = get_user_model()


class StudioLinkHelperTest(TestCase):
    """Direct unit tests for ``studio.admin_links.studio_link``."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Helper Sprint',
            slug='helper-sprint-link',
            start_date=date(2026, 1, 1),
        )

    def test_returns_anchor_for_resolved_url(self):
        html = studio_link(
            self.sprint,
            'studio_sprint_detail',
            lambda o: {'sprint_id': o.pk},
        )
        self.assertIn(f'/studio/sprints/{self.sprint.pk}/', html)
        self.assertIn('target="_blank"', html)
        self.assertIn('rel="noopener"', html)
        self.assertIn('Open in Studio', html)

    def test_returns_empty_string_for_none(self):
        self.assertEqual(
            studio_link(None, 'studio_sprint_detail'),
            '',
        )

    def test_returns_empty_string_for_unsaved_instance(self):
        unsaved = Sprint(name='Unsaved', slug='unsaved-link')
        self.assertEqual(unsaved.pk, None)
        self.assertEqual(
            studio_link(
                unsaved,
                'studio_sprint_detail',
                lambda o: {'sprint_id': o.pk},
            ),
            '',
        )

    def test_returns_empty_string_for_unknown_url_name(self):
        # Self-suppression: an admin whose URL was renamed must keep
        # rendering (the cell is just empty), it must not 500.
        self.assertEqual(
            studio_link(
                self.sprint,
                'does_not_exist_url_name',
                lambda o: {'sprint_id': o.pk},
            ),
            '',
        )

    def test_default_kwargs_func_uses_pk(self):
        # When kwargs_func is omitted, the helper falls back to
        # ``{'pk': obj.pk}``. ``studio_sprint_detail`` does not accept
        # ``pk`` as a kwarg name (it wants ``sprint_id``) so the call
        # must self-suppress to an empty string, NOT crash.
        self.assertEqual(
            studio_link(self.sprint, 'studio_sprint_detail'),
            '',
        )


class _AdminBase(TestCase):
    """Shared superuser session for admin change/changelist surface tests."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email='admin-studio-link@test.com',
            password='pw',
        )

    def setUp(self):
        self.client.login(
            email='admin-studio-link@test.com', password='pw',
        )

    def assertStudioLinkPresent(self, response, expected_href):
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('Open in Studio', body)
        self.assertIn(f'href="{expected_href}"', body)
        self.assertIn('target="_blank"', body)
        self.assertIn('rel="noopener"', body)

    def assertStudioLinkAbsent(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Open in Studio')


class ContentAdminStudioLinkTest(_AdminBase):
    """Content app admins (Article, Course, Unit, Workshop, Download, Project)."""

    def test_article_change_form_has_studio_link(self):
        article = Article.objects.create(
            title='ART-727', slug='art-727', date=date(2026, 1, 1),
        )
        response = self.client.get(
            f'/admin/content/article/{article.pk}/change/',
        )
        self.assertStudioLinkPresent(
            response, f'/studio/articles/{article.pk}/edit',
        )

    def test_article_changelist_has_studio_link_column(self):
        article = Article.objects.create(
            title='ART-list-727', slug='art-list-727',
            date=date(2026, 1, 1),
        )
        response = self.client.get('/admin/content/article/')
        self.assertStudioLinkPresent(
            response, f'/studio/articles/{article.pk}/edit',
        )

    def test_course_change_form_has_studio_link(self):
        course = Course.objects.create(
            title='C-727', slug='c-727', status='draft',
        )
        response = self.client.get(
            f'/admin/content/course/{course.pk}/change/',
        )
        self.assertStudioLinkPresent(
            response, f'/studio/courses/{course.pk}/edit',
        )

    def test_course_changelist_has_studio_link_column(self):
        course = Course.objects.create(
            title='C-list-727', slug='c-list-727', status='draft',
        )
        response = self.client.get('/admin/content/course/')
        self.assertStudioLinkPresent(
            response, f'/studio/courses/{course.pk}/edit',
        )

    def test_unit_change_form_has_studio_link(self):
        course = Course.objects.create(
            title='C-unit-727', slug='c-unit-727', status='draft',
        )
        module = Module.objects.create(
            course=course, title='M', sort_order=0,
        )
        unit = Unit.objects.create(
            module=module, title='U', slug='u-727', sort_order=0,
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertStudioLinkPresent(
            response, f'/studio/units/{unit.pk}/edit',
        )

    def test_unit_changelist_has_studio_link_column(self):
        course = Course.objects.create(
            title='C-unit-list-727', slug='c-unit-list-727', status='draft',
        )
        module = Module.objects.create(
            course=course, title='M', sort_order=0,
        )
        unit = Unit.objects.create(
            module=module, title='U', slug='u-list-727', sort_order=0,
        )
        response = self.client.get('/admin/content/unit/')
        self.assertStudioLinkPresent(
            response, f'/studio/units/{unit.pk}/edit',
        )

    def test_workshop_change_form_has_studio_link(self):
        workshop = Workshop.objects.create(
            title='WS-727', slug='ws-727', date=date(2026, 1, 1),
        )
        response = self.client.get(
            f'/admin/content/workshop/{workshop.pk}/change/',
        )
        self.assertStudioLinkPresent(
            response, f'/studio/workshops/{workshop.pk}/',
        )

    def test_workshop_changelist_has_studio_link_column(self):
        workshop = Workshop.objects.create(
            title='WS-list-727', slug='ws-list-727',
            date=date(2026, 1, 1),
        )
        response = self.client.get('/admin/content/workshop/')
        self.assertStudioLinkPresent(
            response, f'/studio/workshops/{workshop.pk}/',
        )

    def test_download_change_form_has_studio_link(self):
        download = Download.objects.create(
            title='DL-727', slug='dl-727',
            file_url='https://example.com/file.pdf',
        )
        response = self.client.get(
            f'/admin/content/download/{download.pk}/change/',
        )
        self.assertStudioLinkPresent(
            response, f'/studio/downloads/{download.pk}/edit',
        )

    def test_download_changelist_has_studio_link_column(self):
        download = Download.objects.create(
            title='DL-list-727', slug='dl-list-727',
            file_url='https://example.com/file.pdf',
        )
        response = self.client.get('/admin/content/download/')
        self.assertStudioLinkPresent(
            response, f'/studio/downloads/{download.pk}/edit',
        )

    def test_project_change_form_has_studio_link(self):
        project = Project.objects.create(
            title='P-727', slug='p-727', date=date(2026, 1, 1),
            status='pending_review', published=False,
        )
        response = self.client.get(
            f'/admin/content/project/{project.pk}/change/',
        )
        self.assertStudioLinkPresent(
            response, f'/studio/projects/{project.pk}/review',
        )

    def test_project_changelist_has_studio_link_column(self):
        project = Project.objects.create(
            title='P-list-727', slug='p-list-727',
            date=date(2026, 1, 1),
            status='pending_review', published=False,
        )
        response = self.client.get('/admin/content/project/')
        self.assertStudioLinkPresent(
            response, f'/studio/projects/{project.pk}/review',
        )


class EventAdminStudioLinkTest(_AdminBase):
    """Events app admin."""

    def test_event_change_form_has_studio_link(self):
        event = Event.objects.create(
            title='E-727', slug='e-727',
            start_datetime=timezone.make_aware(datetime(2026, 6, 1, 10, 0)),
            end_datetime=timezone.make_aware(datetime(2026, 6, 1, 11, 0)),
        )
        response = self.client.get(f'/admin/events/event/{event.pk}/change/')
        self.assertStudioLinkPresent(
            response, f'/studio/events/{event.pk}/edit',
        )

    def test_event_changelist_has_studio_link_column(self):
        event = Event.objects.create(
            title='E-list-727', slug='e-list-727',
            start_datetime=timezone.make_aware(datetime(2026, 6, 1, 10, 0)),
            end_datetime=timezone.make_aware(datetime(2026, 6, 1, 11, 0)),
        )
        response = self.client.get('/admin/events/event/')
        self.assertStudioLinkPresent(
            response, f'/studio/events/{event.pk}/edit',
        )


class PlansAdminStudioLinkTest(_AdminBase):
    """Plans app admins (Sprint + Plan). The reporter's stated example."""

    def test_sprint_change_form_has_studio_link(self):
        sprint = Sprint.objects.create(
            name='S-727', slug='s-727',
            start_date=date(2026, 1, 1),
        )
        response = self.client.get(
            f'/admin/plans/sprint/{sprint.pk}/change/',
        )
        self.assertStudioLinkPresent(
            response, f'/studio/sprints/{sprint.pk}/',
        )

    def test_sprint_changelist_has_studio_link_column(self):
        sprint = Sprint.objects.create(
            name='S-list-727', slug='s-list-727',
            start_date=date(2026, 1, 1),
        )
        response = self.client.get('/admin/plans/sprint/')
        self.assertStudioLinkPresent(
            response, f'/studio/sprints/{sprint.pk}/',
        )

    def test_sprint_changelist_one_link_per_row(self):
        # The changelist column carries one link per row pointing at
        # that row's Studio surface; clicking row N must NOT land on
        # row N-1.
        sprints = [
            Sprint.objects.create(
                name=f'Multi-{i}', slug=f'multi-727-{i}',
                start_date=date(2026, 1, i + 1),
            )
            for i in range(3)
        ]
        response = self.client.get('/admin/plans/sprint/')
        body = response.content.decode()
        for sprint in sprints:
            self.assertIn(
                f'href="/studio/sprints/{sprint.pk}/"', body,
                msg=f'Sprint {sprint.pk} missing on changelist',
            )

    def test_plan_change_form_has_studio_link(self):
        sprint = Sprint.objects.create(
            name='S-plan-727', slug='s-plan-727',
            start_date=date(2026, 1, 1),
        )
        member = User.objects.create_user(
            email='plan-727@test.com', password='pw',
        )
        plan = Plan.objects.create(member=member, sprint=sprint)
        response = self.client.get(f'/admin/plans/plan/{plan.pk}/change/')
        self.assertStudioLinkPresent(
            response, f'/studio/plans/{plan.pk}/',
        )

    def test_plan_changelist_has_studio_link_column(self):
        sprint = Sprint.objects.create(
            name='S-plan-list-727', slug='s-plan-list-727',
            start_date=date(2026, 1, 1),
        )
        member = User.objects.create_user(
            email='plan-list-727@test.com', password='pw',
        )
        plan = Plan.objects.create(member=member, sprint=sprint)
        response = self.client.get('/admin/plans/plan/')
        self.assertStudioLinkPresent(
            response, f'/studio/plans/{plan.pk}/',
        )


class AccountsAdminStudioLinkTest(_AdminBase):
    """Accounts app admins (User + ImportBatch)."""

    def test_user_change_form_has_studio_link(self):
        member = User.objects.create_user(
            email='member-727@test.com', password='pw',
        )
        response = self.client.get(
            f'/admin/accounts/user/{member.pk}/change/',
        )
        self.assertStudioLinkPresent(
            response, f'/studio/users/{member.pk}/',
        )

    def test_user_changelist_has_studio_link_column(self):
        member = User.objects.create_user(
            email='member-list-727@test.com', password='pw',
        )
        response = self.client.get('/admin/accounts/user/')
        self.assertStudioLinkPresent(
            response, f'/studio/users/{member.pk}/',
        )

    def test_import_batch_change_form_has_studio_link(self):
        batch = ImportBatch.objects.create(
            source='csv', dry_run=False, status='completed',
        )
        response = self.client.get(
            f'/admin/accounts/importbatch/{batch.pk}/change/',
        )
        self.assertStudioLinkPresent(
            response, f'/studio/imports/{batch.pk}/',
        )

    def test_import_batch_changelist_has_studio_link_column(self):
        batch = ImportBatch.objects.create(
            source='csv', dry_run=False, status='completed',
        )
        response = self.client.get('/admin/accounts/importbatch/')
        self.assertStudioLinkPresent(
            response, f'/studio/imports/{batch.pk}/',
        )


class EmailAdminStudioLinkTest(_AdminBase):
    """Email app admin (EmailCampaign)."""

    def test_campaign_change_form_has_studio_link(self):
        campaign = EmailCampaign.objects.create(
            subject='C-727', body='Hello',
        )
        response = self.client.get(
            f'/admin/email_app/emailcampaign/{campaign.pk}/change/',
        )
        self.assertStudioLinkPresent(
            response, f'/studio/campaigns/{campaign.pk}/',
        )

    def test_campaign_changelist_has_studio_link_column(self):
        campaign = EmailCampaign.objects.create(
            subject='C-list-727', body='Hello',
        )
        response = self.client.get('/admin/email_app/emailcampaign/')
        self.assertStudioLinkPresent(
            response, f'/studio/campaigns/{campaign.pk}/',
        )


class IntegrationsAdminStudioLinkTest(_AdminBase):
    """Integrations app admins (UtmCampaign, UtmCampaignLink, Redirect)."""

    def test_utm_campaign_change_form_has_studio_link(self):
        utm = UtmCampaign.objects.create(
            name='UTM-727', slug='utm_727',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )
        response = self.client.get(
            f'/admin/integrations/utmcampaign/{utm.pk}/change/',
        )
        self.assertStudioLinkPresent(
            response, f'/studio/utm-campaigns/{utm.pk}/',
        )

    def test_utm_campaign_changelist_has_studio_link_column(self):
        utm = UtmCampaign.objects.create(
            name='UTM-list-727', slug='utm_list_727',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )
        response = self.client.get('/admin/integrations/utmcampaign/')
        self.assertStudioLinkPresent(
            response, f'/studio/utm-campaigns/{utm.pk}/',
        )

    def test_utm_link_change_form_has_two_segment_studio_link(self):
        utm = UtmCampaign.objects.create(
            name='UTM-link-727', slug='utm_link_727',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )
        link = UtmCampaignLink.objects.create(
            campaign=utm, utm_content='hero-727',
            label='Hero', destination='/hello-727',
        )
        response = self.client.get(
            f'/admin/integrations/utmcampaignlink/{link.pk}/change/',
        )
        # Two-segment kwargs builder: reads obj.campaign_id AND obj.pk.
        self.assertStudioLinkPresent(
            response,
            f'/studio/utm-campaigns/{utm.pk}/links/{link.pk}/edit',
        )

    def test_utm_link_changelist_has_two_segment_studio_link_column(self):
        utm = UtmCampaign.objects.create(
            name='UTM-link-list-727', slug='utm_link_list_727',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )
        link = UtmCampaignLink.objects.create(
            campaign=utm, utm_content='hero-list-727',
            label='Hero', destination='/hello-list-727',
        )
        response = self.client.get('/admin/integrations/utmcampaignlink/')
        self.assertStudioLinkPresent(
            response,
            f'/studio/utm-campaigns/{utm.pk}/links/{link.pk}/edit',
        )

    def test_redirect_change_form_has_studio_link(self):
        redirect = Redirect.objects.create(
            source_path='/from-727', target_path='/to-727',
            redirect_type=301,
        )
        response = self.client.get(
            f'/admin/integrations/redirect/{redirect.pk}/change/',
        )
        self.assertStudioLinkPresent(
            response, f'/studio/redirects/{redirect.pk}/edit',
        )

    def test_redirect_changelist_has_studio_link_column(self):
        redirect = Redirect.objects.create(
            source_path='/from-list-727', target_path='/to-list-727',
            redirect_type=301,
        )
        response = self.client.get('/admin/integrations/redirect/')
        self.assertStudioLinkPresent(
            response, f'/studio/redirects/{redirect.pk}/edit',
        )


class CRMAdminStudioLinkTest(_AdminBase):
    """CRM app admin (CRMRecord)."""

    def test_crm_change_form_has_studio_link(self):
        member = User.objects.create_user(
            email='crm-727@test.com', password='pw',
        )
        record = CRMRecord.objects.create(
            user=member, created_by=self.admin, status='active',
        )
        response = self.client.get(
            f'/admin/crm/crmrecord/{record.pk}/change/',
        )
        self.assertStudioLinkPresent(
            response, f'/studio/crm/{record.pk}/',
        )

    def test_crm_changelist_has_studio_link_column(self):
        member = User.objects.create_user(
            email='crm-list-727@test.com', password='pw',
        )
        record = CRMRecord.objects.create(
            user=member, created_by=self.admin, status='active',
        )
        response = self.client.get('/admin/crm/crmrecord/')
        self.assertStudioLinkPresent(
            response, f'/studio/crm/{record.pk}/',
        )


class OutOfInventoryAdminTest(_AdminBase):
    """Admins NOT in the inventory must render unchanged."""

    def test_workshop_page_admin_does_not_render_studio_link(self):
        # ``WorkshopPageAdmin`` is explicitly out of scope (workshop
        # pages are edited inline on the workshop form; no Studio
        # per-page URL). The change form must NOT carry the link.
        workshop = Workshop.objects.create(
            title='WS-out', slug='ws-out-727',
            date=date(2026, 1, 1),
        )
        page = WorkshopPage.objects.create(
            workshop=workshop,
            title='Page-out',
            slug='page-out-727',
            sort_order=0,
        )
        response = self.client.get(
            f'/admin/content/workshoppage/{page.pk}/change/',
        )
        self.assertStudioLinkAbsent(response)

    def test_workshop_page_changelist_does_not_render_studio_link(self):
        workshop = Workshop.objects.create(
            title='WS-out-list', slug='ws-out-list-727',
            date=date(2026, 1, 1),
        )
        WorkshopPage.objects.create(
            workshop=workshop,
            title='Page-out-list',
            slug='page-out-list-727',
            sort_order=0,
        )
        response = self.client.get('/admin/content/workshoppage/')
        self.assertStudioLinkAbsent(response)


class NoReverseMatchGuardTest(_AdminBase):
    """End-to-end self-suppression check on a real request path.

    When the configured Studio URL name is renamed/removed, the admin
    change view must keep returning 200 with an empty link cell --
    NOT a 500. We monkeypatch the SprintAdmin's ``studio_link`` method
    to point at a non-existent URL name and hit the change view.
    """

    def test_change_view_self_suppresses_on_no_reverse_match(self):
        sprint = Sprint.objects.create(
            name='Guard', slug='guard-727',
            start_date=date(2026, 1, 1),
        )

        # Replace the helper used by the bound method via patch so the
        # ``reverse()`` call raises NoReverseMatch internally and the
        # helper returns ''.
        def broken_studio_link(self, obj):
            return studio_link(
                obj,
                'does_not_exist_url_name',
                lambda o: {'sprint_id': o.pk},
            )

        from plans.admin.plan import SprintAdmin

        with patch.object(SprintAdmin, 'studio_link', broken_studio_link):
            response = self.client.get(
                f'/admin/plans/sprint/{sprint.pk}/change/',
            )

        # Must still render (no 500).
        self.assertEqual(response.status_code, 200)
        # The link is suppressed: no anchor target points at a Studio
        # URL on the change form's studio_link row. ``Open in Studio``
        # text is the anchor body — it MUST not appear.
        body = response.content.decode()
        self.assertNotIn('Open in Studio', body)


class AdminLinksReverseUrlsExistTest(TestCase):
    """Spot-check that every Studio URL name wired into an admin resolves.

    Catches typos in URL names without exercising the change view (so
    failures localize to the right admin instantly).
    """

    URL_FIXTURES = [
        ('studio_article_edit', {'article_id': 1}, '/studio/articles/1/edit'),
        ('studio_course_edit', {'course_id': 1}, '/studio/courses/1/edit'),
        ('studio_unit_edit', {'unit_id': 1}, '/studio/units/1/edit'),
        ('studio_workshop_detail', {'workshop_id': 1}, '/studio/workshops/1/'),
        ('studio_download_edit', {'download_id': 1}, '/studio/downloads/1/edit'),
        ('studio_project_review', {'project_id': 1}, '/studio/projects/1/review'),
        ('studio_event_edit', {'event_id': 1}, '/studio/events/1/edit'),
        ('studio_sprint_detail', {'sprint_id': 1}, '/studio/sprints/1/'),
        ('studio_plan_detail', {'plan_id': 1}, '/studio/plans/1/'),
        ('studio_user_detail', {'user_id': 1}, '/studio/users/1/'),
        ('studio_import_batch_detail', {'batch_id': 1}, '/studio/imports/1/'),
        ('studio_campaign_detail', {'campaign_id': 1}, '/studio/campaigns/1/'),
        ('studio_utm_campaign_detail', {'campaign_id': 1}, '/studio/utm-campaigns/1/'),
        (
            'studio_utm_link_edit',
            {'campaign_id': 1, 'link_id': 2},
            '/studio/utm-campaigns/1/links/2/edit',
        ),
        ('studio_redirect_edit', {'redirect_id': 1}, '/studio/redirects/1/edit'),
        ('studio_crm_detail', {'crm_id': 1}, '/studio/crm/1/'),
    ]

    def test_all_inventory_url_names_resolve(self):
        # If a URL name in the inventory is renamed in studio/urls.py
        # without updating the admin, this loop fails on that row and
        # tells us exactly which admin to fix.
        self.assertEqual(len(self.URL_FIXTURES), 16)
        for name, kwargs, expected in self.URL_FIXTURES:
            with self.subTest(url_name=name):
                self.assertEqual(reverse(name, kwargs=kwargs), expected)


# Silence unused-import warning: time is imported for parity with
# studio.tests.test_admin_links, kept available for future fixtures.
_ = time
