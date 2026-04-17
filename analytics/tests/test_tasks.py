"""Tests for analytics.tasks (record_visit + slug-to-id resolution)."""

from django.core.cache import cache
from django.test import TestCase

from analytics.models import CampaignVisit
from analytics.tasks import (
    CAMPAIGN_CACHE_KEY,
    _resolve_campaign_id,
    record_visit,
)
from integrations.models import UtmCampaign


class ResolveCampaignIdTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_returns_id_for_known_slug(self):
        camp = UtmCampaign.objects.create(
            name='Launch', slug='resolved_known',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        self.assertEqual(_resolve_campaign_id('resolved_known'), camp.id)

    def test_returns_none_for_unknown_slug(self):
        self.assertIsNone(_resolve_campaign_id('does_not_exist'))

    def test_returns_none_for_empty_slug(self):
        self.assertIsNone(_resolve_campaign_id(''))

    def test_caches_hit_so_second_call_does_not_query_db(self):
        camp = UtmCampaign.objects.create(
            name='Launch', slug='cached_hit',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        # Prime cache
        _resolve_campaign_id('cached_hit')
        # Second call should hit cache, not DB.
        with self.assertNumQueries(0):
            result = _resolve_campaign_id('cached_hit')
        self.assertEqual(result, camp.id)

    def test_caches_miss_so_second_call_does_not_query_db(self):
        # Prime negative cache
        _resolve_campaign_id('miss_one')
        with self.assertNumQueries(0):
            result = _resolve_campaign_id('miss_one')
        self.assertIsNone(result)


class RecordVisitTaskTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_creates_visit_row(self):
        record_visit(
            utm_source='newsletter',
            utm_medium='email',
            utm_campaign='unknown_slug',
            path='/blog',
            anonymous_id='dddddddd-dddd-dddd-dddd-dddddddddddd',
        )
        v = CampaignVisit.objects.get()
        self.assertEqual(v.utm_source, 'newsletter')
        self.assertEqual(v.utm_campaign, 'unknown_slug')
        self.assertEqual(v.path, '/blog')
        self.assertIsNone(v.campaign_id)

    def test_resolves_campaign_fk_when_slug_matches(self):
        camp = UtmCampaign.objects.create(
            name='Launch', slug='record_match',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        record_visit(
            utm_campaign='record_match',
            anonymous_id='eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee',
        )
        v = CampaignVisit.objects.get()
        self.assertEqual(v.campaign_id, camp.id)
