"""Tests for Studio UTM campaign views and importer."""

import re

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from integrations.models import UtmCampaign, UtmCampaignLink
from integrations.models.utm_campaign import (
    UTM_MEDIUM_PRESETS,
    UTM_SOURCE_PRESETS,
)

User = get_user_model()

LAUNCH_URLS = [
    'https://aishippinglabs.com/events/ai-shipping-labs-launch-recap'
    '?utm_source=newsletter&utm_medium=email'
    '&utm_campaign=ai_shipping_labs_launch_april2026&utm_content=ai_hero_list',
    'https://aishippinglabs.com/events/ai-shipping-labs-launch-recap'
    '?utm_source=newsletter&utm_medium=email'
    '&utm_campaign=ai_shipping_labs_launch_april2026&utm_content=maven_list',
    'https://aishippinglabs.com/events/ai-shipping-labs-launch-recap'
    '?utm_source=newsletter&utm_medium=email'
    '&utm_campaign=ai_shipping_labs_launch_april2026'
    '&utm_content=luma_launch_event_list',
]


def _staff_login(client, email='staff@test.com'):
    user = User.objects.create_user(email=email, password='pw', is_staff=True)
    client.login(email=email, password='pw')
    return user


def _user_login(client, email='member@test.com'):
    user = User.objects.create_user(email=email, password='pw', is_staff=False)
    client.login(email=email, password='pw')
    return user


class UtmCampaignAccessTest(TestCase):
    """Verify staff-only access for all UTM views."""

    def setUp(self):
        self.client = Client()
        self.campaign = UtmCampaign.objects.create(
            name='Test', slug='test_campaign',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        self.link = UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content='ai_hero_list',
            destination='/events/launch',
        )

    def _all_paths(self):
        return [
            ('GET', '/studio/utm-campaigns/'),
            ('GET', '/studio/utm-campaigns/new'),
            ('GET', '/studio/utm-campaigns/import'),
            ('GET', f'/studio/utm-campaigns/{self.campaign.pk}/'),
            ('GET', f'/studio/utm-campaigns/{self.campaign.pk}/edit'),
            ('GET', f'/studio/utm-campaigns/{self.campaign.pk}/links/{self.link.pk}/edit'),
        ]

    def test_anonymous_redirected_to_login(self):
        for method, path in self._all_paths():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302, f'expected redirect for {path}, got {response.status_code}')
            self.assertIn('/accounts/login/', response['Location'], f'wrong redirect for {path}')

    def test_non_staff_user_gets_403(self):
        _user_login(self.client, 'member@test.com')
        for method, path in self._all_paths():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 403, f'expected 403 for {path}, got {response.status_code}')


class UtmCampaignListViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        _staff_login(self.client)

    def test_empty_state(self):
        """UTM campaigns list renders the canonical fresh-zero empty state
        (#756) plus the supplemental Import action below the partial card."""
        response = self.client.get('/studio/utm-campaigns/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No UTM campaigns yet')
        self.assertContains(response, 'data-testid="studio-empty-state-fresh"')
        # Both CTAs visible: header import link + below-card empty import.
        self.assertContains(response, 'Add Campaign')
        self.assertContains(response, 'Import')
        self.assertContains(
            response,
            'data-testid="utm-campaign-import-link"',
        )
        self.assertContains(
            response,
            'data-testid="utm-campaign-empty-import-link"',
        )
        self.assertContains(response, 'href="/studio/utm-campaigns/import"')

    def test_list_shows_active_only_by_default(self):
        active = UtmCampaign.objects.create(
            name='Active', slug='active_one',
            default_utm_source='s', default_utm_medium='m',
        )
        archived = UtmCampaign.objects.create(
            name='Archived Old', slug='archived_old',
            default_utm_source='s', default_utm_medium='m',
            is_archived=True,
        )
        response = self.client.get('/studio/utm-campaigns/')
        self.assertContains(response, active.name)
        self.assertNotContains(response, archived.name)

    def test_list_archived_filter(self):
        UtmCampaign.objects.create(
            name='Archived Two', slug='archived_two',
            default_utm_source='s', default_utm_medium='m',
            is_archived=True,
        )
        response = self.client.get('/studio/utm-campaigns/?archived=1')
        self.assertContains(response, 'Archived Two')


class UtmCampaignCreateViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = _staff_login(self.client)

    def test_create_form_renders(self):
        response = self.client.get('/studio/utm-campaigns/new')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'New UTM campaign')

    def test_create_success_redirects_to_detail(self):
        response = self.client.post('/studio/utm-campaigns/new', {
            'name': 'AI Launch April',
            'slug': 'ai_launch_april',
            'default_utm_source': 'newsletter',
            'default_utm_medium': 'email',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        c = UtmCampaign.objects.get(slug='ai_launch_april')
        self.assertEqual(response['Location'], f'/studio/utm-campaigns/{c.pk}/')
        self.assertEqual(c.created_by, self.user)

    def test_create_duplicate_slug_shows_error(self):
        UtmCampaign.objects.create(
            name='Existing', slug='dup_one',
            default_utm_source='s', default_utm_medium='m',
        )
        before = UtmCampaign.objects.count()
        response = self.client.post('/studio/utm-campaigns/new', {
            'name': 'Other',
            'slug': 'dup_one',
            'default_utm_source': 'newsletter',
            'default_utm_medium': 'email',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already exists')
        self.assertEqual(UtmCampaign.objects.count(), before)

    def test_create_invalid_slug_rejected(self):
        before = UtmCampaign.objects.count()
        response = self.client.post('/studio/utm-campaigns/new', {
            'name': 'Bad',
            'slug': 'Bad-Slug',
            'default_utm_source': 'newsletter',
            'default_utm_medium': 'email',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(UtmCampaign.objects.count(), before)


class UtmCampaignEditViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        _staff_login(self.client)
        self.campaign = UtmCampaign.objects.create(
            name='Editable', slug='editable_camp',
            default_utm_source='newsletter', default_utm_medium='email',
        )

    def test_edit_slug_when_no_links(self):
        response = self.client.post(f'/studio/utm-campaigns/{self.campaign.pk}/edit', {
            'name': 'Renamed',
            'slug': 'renamed_camp',
            'default_utm_source': 'newsletter',
            'default_utm_medium': 'email',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.slug, 'renamed_camp')
        self.assertEqual(self.campaign.name, 'Renamed')

    def test_edit_slug_locked_when_links_exist(self):
        UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content='ai_hero_list', destination='/x',
        )
        # GET should mark slug as locked
        response = self.client.get(f'/studio/utm-campaigns/{self.campaign.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Slug is locked')
        # POST attempting to change slug should be ignored
        original_slug = self.campaign.slug
        response = self.client.post(f'/studio/utm-campaigns/{self.campaign.pk}/edit', {
            'name': 'Renamed',
            'slug': 'attempted_new_slug',
            'default_utm_source': 'newsletter',
            'default_utm_medium': 'email',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.slug, original_slug)
        self.assertEqual(self.campaign.name, 'Renamed')


class UtmCampaignArchiveViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        _staff_login(self.client)
        self.campaign = UtmCampaign.objects.create(
            name='To archive', slug='to_archive',
            default_utm_source='s', default_utm_medium='m',
        )

    def test_archive_hides_from_default_list(self):
        response = self.client.post(f'/studio/utm-campaigns/{self.campaign.pk}/archive')
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertTrue(self.campaign.is_archived)
        # default list excludes the archived campaign from the table
        list_response = self.client.get('/studio/utm-campaigns/')
        self.assertEqual(list_response.context['campaigns'].count(), 0)
        # archived list shows it
        archived_response = self.client.get('/studio/utm-campaigns/?archived=1')
        self.assertEqual(archived_response.context['campaigns'].count(), 1)
        self.assertContains(archived_response, 'To archive')

    def test_unarchive(self):
        self.campaign.is_archived = True
        self.campaign.save()
        response = self.client.post(f'/studio/utm-campaigns/{self.campaign.pk}/unarchive')
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertFalse(self.campaign.is_archived)


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class UtmLinkCreateViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        _staff_login(self.client)
        self.campaign = UtmCampaign.objects.create(
            name='Launch', slug='ai_shipping_labs_launch_april2026',
            default_utm_source='newsletter', default_utm_medium='email',
        )

    def test_create_link_success_renders_full_url_on_detail(self):
        response = self.client.post(
            f'/studio/utm-campaigns/{self.campaign.pk}/links/add',
            {
                'utm_content': 'ai_hero_list',
                'destination': '/events/ai-shipping-labs-launch-recap',
                'label': 'AI Hero newsletter list',
            },
        )
        self.assertEqual(response.status_code, 302)
        link = self.campaign.links.get(utm_content='ai_hero_list')
        # Detail should render the canonical URL string
        detail_response = self.client.get(f'/studio/utm-campaigns/{self.campaign.pk}/')
        expected_url = (
            'https://aishippinglabs.com/events/ai-shipping-labs-launch-recap'
            '?utm_source=newsletter&amp;utm_medium=email'
            '&amp;utm_campaign=ai_shipping_labs_launch_april2026'
            '&amp;utm_content=ai_hero_list'
        )
        self.assertContains(detail_response, expected_url)
        self.assertContains(detail_response, 'AI Hero newsletter list')
        self.assertEqual(link.destination, '/events/ai-shipping-labs-launch-recap')

    def test_create_duplicate_utm_content_rejected(self):
        UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content='ai_hero_list',
            destination='/events/launch',
        )
        before = self.campaign.links.count()
        response = self.client.post(
            f'/studio/utm-campaigns/{self.campaign.pk}/links/add',
            {
                'utm_content': 'ai_hero_list',
                'destination': '/some/other/path',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already exists for this campaign')
        self.assertEqual(self.campaign.links.count(), before)

    def test_create_invalid_utm_content_rejected(self):
        before = self.campaign.links.count()
        response = self.client.post(
            f'/studio/utm-campaigns/{self.campaign.pk}/links/add',
            {
                'utm_content': 'Bad-Content',
                'destination': '/x',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.campaign.links.count(), before)


class UtmLinkEditAndArchiveTest(TestCase):
    def setUp(self):
        self.client = Client()
        _staff_login(self.client)
        self.campaign = UtmCampaign.objects.create(
            name='C', slug='cc',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        self.link = UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content='ai_hero_list',
            destination='/x', label='Old',
        )

    def test_edit_link_updates_fields(self):
        response = self.client.post(
            f'/studio/utm-campaigns/{self.campaign.pk}/links/{self.link.pk}/edit',
            {
                'utm_content': 'ai_hero_list',
                'destination': '/y',
                'label': 'New label',
                'utm_term': 'spring',
                'utm_source': '',
                'utm_medium': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.link.refresh_from_db()
        self.assertEqual(self.link.destination, '/y')
        self.assertEqual(self.link.label, 'New label')
        self.assertEqual(self.link.utm_term, 'spring')

    def test_archive_link(self):
        response = self.client.post(
            f'/studio/utm-campaigns/{self.campaign.pk}/links/{self.link.pk}/archive'
        )
        self.assertEqual(response.status_code, 302)
        self.link.refresh_from_db()
        self.assertTrue(self.link.is_archived)


class UtmFieldGuideHelpLinkTest(TestCase):
    """The (?) help link points to the GitHub UTM field guide doc on both forms."""

    DOC_URL = (
        'https://github.com/AI-Shipping-Labs/website/blob/main/_docs/utm_links.md'
    )

    def setUp(self):
        self.client = Client()
        _staff_login(self.client)
        self.campaign = UtmCampaign.objects.create(
            name='C', slug='cc',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        self.link = UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content='ai_hero_list',
            destination='/x',
        )

    def test_add_link_form_shows_help_link_to_doc(self):
        response = self.client.get(f'/studio/utm-campaigns/{self.campaign.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<a href="{self.DOC_URL}" target="_blank" rel="noopener noreferrer" '
            f'class="text-sm text-accent hover:underline" '
            f'data-testid="utm-fields-help-link">(?) UTM field guide</a>',
            html=True,
        )

    def test_edit_link_form_shows_help_link_to_doc(self):
        response = self.client.get(
            f'/studio/utm-campaigns/{self.campaign.pk}/links/{self.link.pk}/edit'
        )
        self.assertEqual(response.status_code, 200)
        match = re.search(
            r'<a\b[^>]*data-testid="utm-fields-help-link"[^>]*>[^<]*</a>',
            response.content.decode(),
        )
        self.assertIsNotNone(match)
        anchor = match.group(0)
        self.assertIn(f'href="{self.DOC_URL}"', anchor)
        self.assertIn('target="_blank"', anchor)
        self.assertIn('rel="noopener noreferrer"', anchor)
        self.assertIn('>(?) UTM field guide</a>', anchor)
        self.assertIn(
            'focus-visible:outline-none focus-visible:ring-2 '
            'focus-visible:ring-accent focus-visible:ring-offset-2 '
            'focus-visible:ring-offset-background',
            anchor,
        )


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class UtmCampaignImporterTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = _staff_login(self.client)

    def test_import_three_launch_urls_creates_one_campaign_three_links(self):
        response = self.client.post(
            '/studio/utm-campaigns/import',
            {'urls': '\n'.join(LAUNCH_URLS)},
        )
        self.assertEqual(response.status_code, 200)
        # Result page numbers
        self.assertEqual(response.context['campaigns_created'], 1)
        self.assertEqual(response.context['links_created'], 3)
        self.assertEqual(response.context['links_skipped'], 0)
        self.assertEqual(response.context['errors'], [])
        # Database state
        c = UtmCampaign.objects.get(slug='ai_shipping_labs_launch_april2026')
        self.assertEqual(c.links.count(), 3)
        self.assertEqual(c.created_by, self.user)
        contents = sorted(c.links.values_list('utm_content', flat=True))
        self.assertEqual(contents, ['ai_hero_list', 'luma_launch_event_list', 'maven_list'])
        for link in c.links.all():
            self.assertEqual(link.destination, 'https://aishippinglabs.com/events/ai-shipping-labs-launch-recap')

    def test_import_is_idempotent(self):
        # First run
        self.client.post('/studio/utm-campaigns/import', {'urls': '\n'.join(LAUNCH_URLS)})
        before_campaigns = UtmCampaign.objects.count()
        before_links = UtmCampaignLink.objects.count()
        # Second run
        response = self.client.post(
            '/studio/utm-campaigns/import',
            {'urls': '\n'.join(LAUNCH_URLS)},
        )
        self.assertEqual(response.context['campaigns_created'], 0)
        self.assertEqual(response.context['campaigns_matched'], 1)
        self.assertEqual(response.context['links_created'], 0)
        self.assertEqual(response.context['links_skipped'], 3)
        self.assertEqual(response.context['errors'], [])
        # No new rows
        self.assertEqual(UtmCampaign.objects.count(), before_campaigns)
        self.assertEqual(UtmCampaignLink.objects.count(), before_links)

    def test_import_csv_file_produces_same_result_as_paste(self):
        csv_bytes = ('url\n' + '\n'.join(LAUNCH_URLS)).encode('utf-8')
        response = self.client.post(
            '/studio/utm-campaigns/import',
            {
                'urls': '',
                'csv_file': self._uploaded_file(csv_bytes, 'links.csv'),
            },
        )
        self.assertEqual(response.context['campaigns_created'], 1)
        self.assertEqual(response.context['links_created'], 3)
        self.assertEqual(UtmCampaignLink.objects.count(), 3)

    def _uploaded_file(self, data: bytes, name: str):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, data, content_type='text/csv')

    def test_import_reports_missing_utm_content(self):
        bad_url = (
            'https://aishippinglabs.com/foo'
            '?utm_source=newsletter&utm_medium=email&utm_campaign=launch_april'
        )
        good_url = LAUNCH_URLS[0]
        response = self.client.post(
            '/studio/utm-campaigns/import',
            {'urls': f'{good_url}\n{bad_url}'},
        )
        self.assertEqual(response.context['links_created'], 1)
        errors = response.context['errors']
        self.assertEqual(len(errors), 1)
        raw, reason = errors[0]
        self.assertEqual(raw, bad_url)
        self.assertIn('utm_content', reason)
        # Bad row did not create a link with empty utm_content
        self.assertFalse(UtmCampaignLink.objects.filter(utm_content='').exists())

    def test_import_reports_missing_utm_source(self):
        bad_url = (
            'https://aishippinglabs.com/foo'
            '?utm_medium=email&utm_campaign=launch&utm_content=tag'
        )
        response = self.client.post(
            '/studio/utm-campaigns/import',
            {'urls': bad_url},
        )
        self.assertEqual(response.context['links_created'], 0)
        errors = response.context['errors']
        self.assertEqual(len(errors), 1)
        _, reason = errors[0]
        self.assertIn('utm_source', reason)


class StudioSidebarTest(TestCase):
    def setUp(self):
        self.client = Client()
        _staff_login(self.client)

    def test_sidebar_shows_utm_campaigns_link_distinct_from_email_campaigns(self):
        """Issue #570 renamed ``Campaigns`` to ``Email campaigns`` and
        ``UTM Campaigns`` to ``UTM links``; ``User imports`` is now
        ``Imports`` under the People > Users sub-group. All URLs are
        unchanged."""
        response = self.client.get('/studio/utm-campaigns/')
        # Email campaigns link still present.
        self.assertContains(response, 'href="/studio/campaigns/"')
        self.assertContains(response, '<span>Email campaigns</span>', html=True)
        # The UTM link sits under a distinct label.
        self.assertContains(response, 'href="/studio/utm-campaigns/"')
        self.assertContains(response, '<span>UTM links</span>', html=True)
        # Imports sub-group navigation remains separate from UTM campaign import.
        self.assertContains(response, 'href="/studio/imports/"')
        self.assertContains(response, '<span>Imports</span>', html=True)


class UtmPresetDropdownTest(TestCase):
    """Issue #874: source/medium override fields offer presets via a
    <datalist>-backed text input while still accepting custom values."""

    def setUp(self):
        self.client = Client()
        _staff_login(self.client)
        self.campaign = UtmCampaign.objects.create(
            name='Presets', slug='presets_camp',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        self.link = UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content='homepage',
            destination='/', utm_source='github', utm_medium='referral',
        )

    def test_presets_are_single_source_of_truth(self):
        """The Studio constants are the shared list (#874 / #875)."""
        self.assertEqual(UTM_SOURCE_PRESETS[0], 'youtube')
        self.assertIn('github', UTM_SOURCE_PRESETS)
        self.assertIn('newsletter', UTM_SOURCE_PRESETS)
        self.assertIn('community', UTM_MEDIUM_PRESETS)
        self.assertIn('display', UTM_MEDIUM_PRESETS)
        # The token API imports the very same objects (no drift).
        from api.views.utm_campaigns import (
            UTM_MEDIUM_PRESETS as API_MEDIUM,
        )
        from api.views.utm_campaigns import (
            UTM_SOURCE_PRESETS as API_SOURCE,
        )
        self.assertIs(API_SOURCE, UTM_SOURCE_PRESETS)
        self.assertIs(API_MEDIUM, UTM_MEDIUM_PRESETS)

    def test_add_link_form_renders_preset_datalists(self):
        response = self.client.get(f'/studio/utm-campaigns/{self.campaign.pk}/')
        self.assertEqual(response.status_code, 200)
        # Inputs are wired to the datalists.
        self.assertContains(response, 'list="utm-source-presets"')
        self.assertContains(response, 'list="utm-medium-presets"')
        # Datalist option elements exist for representative presets.
        self.assertContains(response, '<datalist id="utm-source-presets">')
        self.assertContains(response, '<datalist id="utm-medium-presets">')
        for value in UTM_SOURCE_PRESETS:
            self.assertContains(response, f'<option value="{value}">', html=True)
        for value in UTM_MEDIUM_PRESETS:
            self.assertContains(response, f'<option value="{value}">', html=True)

    def test_edit_link_form_renders_preset_datalists(self):
        response = self.client.get(
            f'/studio/utm-campaigns/{self.campaign.pk}/links/{self.link.pk}/edit'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'list="utm-source-presets"')
        self.assertContains(response, 'list="utm-medium-presets"')
        self.assertContains(response, '<option value="youtube">', html=True)
        self.assertContains(response, '<option value="community">', html=True)

    def test_campaign_form_renders_preset_datalists(self):
        """Nice-to-have: campaign default source/medium also offer presets."""
        response = self.client.get('/studio/utm-campaigns/new')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'list="utm-source-presets"')
        self.assertContains(response, 'list="utm-medium-presets"')

    def test_custom_source_medium_saved_verbatim_and_round_trips(self):
        """A typed value not in the presets is persisted and shown on edit."""
        response = self.client.post(
            f'/studio/utm-campaigns/{self.campaign.pk}/links/add',
            {
                'utm_content': 'partner_page',
                'destination': '/pricing',
                'utm_source': 'partner_blog',
                'utm_medium': 'partner_blast',
            },
        )
        self.assertEqual(response.status_code, 302)
        link = self.campaign.links.get(utm_content='partner_page')
        self.assertEqual(link.utm_source, 'partner_blog')
        self.assertEqual(link.utm_medium, 'partner_blast')
        # Reopen for edit: the custom values are shown in the inputs.
        edit = self.client.get(
            f'/studio/utm-campaigns/{self.campaign.pk}/links/{link.pk}/edit'
        )
        self.assertContains(edit, 'value="partner_blog"')
        self.assertContains(edit, 'value="partner_blast"')

    def test_preset_source_medium_saved_and_built_into_url(self):
        self.client.post(
            f'/studio/utm-campaigns/{self.campaign.pk}/links/add',
            {
                'utm_content': 'preset_link',
                'destination': '/',
                'utm_source': 'github',
                'utm_medium': 'referral',
            },
        )
        link = self.campaign.links.get(utm_content='preset_link')
        url = link.build_url()
        self.assertIn('utm_source=github', url)
        self.assertIn('utm_medium=referral', url)

    def test_blank_override_falls_back_to_campaign_default(self):
        self.client.post(
            f'/studio/utm-campaigns/{self.campaign.pk}/links/add',
            {
                'utm_content': 'launch',
                'destination': '/pricing',
                'utm_source': '',
                'utm_medium': '',
            },
        )
        link = self.campaign.links.get(utm_content='launch')
        self.assertEqual(link.utm_source, '')
        self.assertEqual(link.utm_medium, '')
        url = link.build_url()
        self.assertIn('utm_source=newsletter', url)
        self.assertIn('utm_medium=email', url)


class UtmCloneActionTest(TestCase):
    """Issue #874: each tracked-link row exposes a Clone action that carries
    the row's values as data attributes for client-side prefill."""

    def setUp(self):
        self.client = Client()
        _staff_login(self.client)
        self.campaign = UtmCampaign.objects.create(
            name='Clone', slug='clone_camp',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        self.link = UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content='homepage',
            destination='/', label='Home', utm_term='spring',
            utm_source='github', utm_medium='referral',
        )

    def test_row_has_clone_button_with_data_attributes(self):
        response = self.client.get(f'/studio/utm-campaigns/{self.campaign.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'utm-clone-btn')
        self.assertContains(response, 'data-utm-content="homepage"')
        self.assertContains(response, 'data-destination="/"')
        self.assertContains(response, 'data-utm-term="spring"')
        self.assertContains(response, 'data-utm-source="github"')
        self.assertContains(response, 'data-utm-medium="referral"')
        self.assertContains(response, 'data-label-value="Home"')

    def test_add_link_form_has_id_for_clone_target(self):
        response = self.client.get(f'/studio/utm-campaigns/{self.campaign.pk}/')
        self.assertContains(response, 'id="utm-add-link-form"')
        self.assertContains(response, 'id="add-link-section"')

    def test_clone_button_is_a_real_button_not_a_link(self):
        """Clone is a <button type="button"> so it is keyboard-focusable and
        does not navigate (the prefill happens client-side)."""
        response = self.client.get(f'/studio/utm-campaigns/{self.campaign.pk}/')
        self.assertContains(
            response,
            'type="button" class="utm-clone-btn',
        )
