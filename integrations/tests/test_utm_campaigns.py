"""Tests for UtmCampaign and UtmCampaignLink models."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase, override_settings

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting, UtmCampaign, UtmCampaignLink


class UtmCampaignModelTest(TestCase):
    def test_slug_validator_rejects_uppercase(self):
        c = UtmCampaign(
            name='Bad',
            slug='Bad-Slug',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )
        with self.assertRaises(ValidationError):
            c.full_clean()

    def test_slug_validator_accepts_lowercase_underscore_digits(self):
        c = UtmCampaign(
            name='Good',
            slug='launch_2026_april',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )
        # full_clean should not raise
        c.full_clean()

    def test_slug_uniqueness(self):
        UtmCampaign.objects.create(
            name='One',
            slug='dup_slug',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )
        with self.assertRaises(IntegrityError):
            UtmCampaign.objects.create(
                name='Two',
                slug='dup_slug',
                default_utm_source='newsletter',
                default_utm_medium='email',
            )


class UtmCampaignLinkModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.campaign = UtmCampaign.objects.create(
            name='Launch',
            slug='ai_shipping_labs_launch_april2026',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )

    def test_utm_content_validator_rejects_dash(self):
        link = UtmCampaignLink(
            campaign=self.campaign,
            utm_content='bad-content',
            destination='/x',
        )
        with self.assertRaises(ValidationError):
            link.full_clean()

    @override_settings(SITE_BASE_URL='https://aishippinglabs.com')
    def test_build_url_path_destination_uses_site_base_url(self):
        link = UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content='ai_hero_list',
            destination='/events/ai-shipping-labs-launch-recap',
        )
        expected = (
            'https://aishippinglabs.com/events/ai-shipping-labs-launch-recap'
            '?utm_source=newsletter&utm_medium=email'
            '&utm_campaign=ai_shipping_labs_launch_april2026'
            '&utm_content=ai_hero_list'
        )
        self.assertEqual(link.build_url(), expected)

    @override_settings(SITE_BASE_URL='https://aishippinglabs.com')
    def test_build_url_full_url_destination_left_untouched(self):
        link = UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content='maven_list',
            destination='https://example.com/landing',
        )
        url = link.build_url()
        self.assertTrue(url.startswith('https://example.com/landing?'))
        self.assertIn('utm_source=newsletter', url)
        self.assertIn('utm_campaign=ai_shipping_labs_launch_april2026', url)
        self.assertIn('utm_content=maven_list', url)

    @override_settings(SITE_BASE_URL='https://aishippinglabs.com')
    def test_build_url_preserves_existing_query_and_fragment(self):
        link = UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content='luma_launch_event_list',
            destination='/events/launch?ref=email#agenda',
        )
        url = link.build_url()
        # fragment preserved
        self.assertTrue(url.endswith('#agenda'))
        # ref preserved
        self.assertIn('ref=email', url)
        # canonical UTM ordering
        self.assertIn(
            'utm_source=newsletter&utm_medium=email'
            '&utm_campaign=ai_shipping_labs_launch_april2026'
            '&utm_content=luma_launch_event_list',
            url,
        )

    @override_settings(SITE_BASE_URL='https://aishippinglabs.com')
    def test_build_url_strips_existing_utm_on_destination(self):
        link = UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content='ai_hero_list',
            destination='/events/launch?utm_source=stale&utm_medium=stale',
        )
        url = link.build_url()
        self.assertEqual(url.count('utm_source='), 1)
        self.assertIn('utm_source=newsletter', url)
        self.assertNotIn('utm_source=stale', url)

    @override_settings(SITE_BASE_URL='https://aishippinglabs.com')
    def test_build_url_includes_utm_term_only_when_set(self):
        link_no_term = UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content='ai_hero_list',
            destination='/x',
        )
        link_with_term = UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content='maven_list',
            destination='/y',
            utm_term='launch_recap',
        )
        self.assertNotIn('utm_term', link_no_term.build_url())
        url_with = link_with_term.build_url()
        self.assertTrue(url_with.endswith('utm_term=launch_recap'),
                        f'utm_term should be last: {url_with}')

    @override_settings(SITE_BASE_URL='https://aishippinglabs.com')
    def test_build_url_uses_overrides_when_set(self):
        link = UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content='ai_hero_list',
            destination='/x',
            utm_source='partner',
            utm_medium='social',
        )
        url = link.build_url()
        self.assertIn('utm_source=partner', url)
        self.assertIn('utm_medium=social', url)
        self.assertNotIn('utm_source=newsletter', url)

    @override_settings(SITE_BASE_URL='https://staging.example.com')
    def test_build_url_respects_site_base_url_setting(self):
        link = UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content='ai_hero_list',
            destination='/events/launch',
        )
        self.assertTrue(link.build_url().startswith('https://staging.example.com/events/launch?'))


class UtmCampaignLinkSiteBaseUrlOverrideTest(TestCase):
    """``UtmLink.build_url()`` must read ``site_base_url()`` at call time
    so DB overrides (Studio > Settings > Site) take effect on tracking
    URLs without a process restart (issue #462 — backfill of #435).
    """

    @classmethod
    def setUpTestData(cls):
        cls.campaign = UtmCampaign.objects.create(
            name='Override',
            slug='override_campaign_april2026',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    @override_settings(SITE_BASE_URL='https://env.example.com')
    def test_db_override_used_instead_of_settings(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://override.example.com',
            group='site',
        )
        clear_config_cache()
        link = UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content='ai_hero_list',
            destination='/events/launch',
        )
        url = link.build_url()
        self.assertTrue(
            url.startswith('https://override.example.com/events/launch?'),
            f'Expected override host in URL, got {url}',
        )
        # Negative assertion: env value must not appear when override is set.
        self.assertNotIn('env.example.com', url)


class UtmCampaignSlugLockTest(TestCase):
    def test_has_links_returns_false_when_no_links(self):
        c = UtmCampaign.objects.create(
            name='Empty',
            slug='empty_campaign',
            default_utm_source='s',
            default_utm_medium='m',
        )
        self.assertFalse(c.has_links())

    def test_has_links_returns_true_when_links_exist(self):
        c = UtmCampaign.objects.create(
            name='Has links',
            slug='has_links_campaign',
            default_utm_source='s',
            default_utm_medium='m',
        )
        UtmCampaignLink.objects.create(
            campaign=c, utm_content='ai_hero_list', destination='/x',
        )
        self.assertTrue(c.has_links())
