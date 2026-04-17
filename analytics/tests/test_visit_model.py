"""Tests for the CampaignVisit model."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from analytics.models import CampaignVisit
from integrations.models import UtmCampaign

User = get_user_model()


class CampaignVisitModelTest(TestCase):
    def test_create_with_minimum_fields(self):
        """A CampaignVisit can be created with just an anonymous_id."""
        visit = CampaignVisit.objects.create(anonymous_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        self.assertIsNotNone(visit.pk)
        self.assertIsNotNone(visit.ts)
        self.assertEqual(visit.utm_source, '')
        self.assertIsNone(visit.campaign_id)
        self.assertIsNone(visit.user_id)

    def test_str_includes_utm_source_and_path(self):
        v = CampaignVisit.objects.create(
            anonymous_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
            utm_source='newsletter',
            utm_medium='email',
            utm_campaign='launch',
            path='/blog',
        )
        s = str(v)
        self.assertIn('newsletter', s)
        self.assertIn('launch', s)
        self.assertIn('/blog', s)

    def test_campaign_set_null_on_campaign_delete(self):
        """If the FK target is deleted, the visit row keeps utm_campaign string."""
        camp = UtmCampaign.objects.create(
            name='Launch', slug='launch_x',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        v = CampaignVisit.objects.create(
            anonymous_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            campaign=camp,
            utm_campaign='launch_x',
        )
        camp.delete()
        v.refresh_from_db()
        self.assertIsNone(v.campaign_id)
        self.assertEqual(v.utm_campaign, 'launch_x')

    def test_user_set_null_on_user_delete(self):
        u = User.objects.create_user(email='ghost@test.com', password='x')
        v = CampaignVisit.objects.create(
            anonymous_id='cccccccc-cccc-cccc-cccc-cccccccccccc',
            user=u,
        )
        u.delete()
        v.refresh_from_db()
        self.assertIsNone(v.user_id)
