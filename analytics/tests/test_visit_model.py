"""Tests for the CampaignVisit model."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from analytics.models import CampaignVisit
from integrations.models import UtmCampaign

User = get_user_model()


class CampaignVisitModelTest(TestCase):
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
