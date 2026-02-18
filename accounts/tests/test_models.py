from django.test import TestCase
from accounts.models import Tier


class TierModelTest(TestCase):
    def setUp(self):
        self.tier = Tier.objects.create(
            name='basic',
            tagline='Content only',
            description='Access content',
            price_monthly=20,
            price_annual=200,
            hook='Educational content',
            positioning='Best for independent builders',
            features=[{'text': 'Feature 1', 'included': True}],
            highlighted=False,
            sort_order=0,
        )

    def test_str(self):
        self.assertEqual(str(self.tier), 'Basic')

    def test_ordering(self):
        Tier.objects.create(
            name='main',
            sort_order=1,
        )
        tiers = list(Tier.objects.all())
        self.assertEqual(tiers[0].name, 'basic')
        self.assertEqual(tiers[1].name, 'main')

    def test_unique_name(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Tier.objects.create(name='basic')

    def test_json_features(self):
        self.assertEqual(len(self.tier.features), 1)
        self.assertEqual(self.tier.features[0]['text'], 'Feature 1')
        self.assertTrue(self.tier.features[0]['included'])

    def test_default_values(self):
        tier = Tier.objects.create(name='premium', sort_order=2)
        self.assertEqual(tier.tagline, '')
        self.assertEqual(tier.description, '')
        self.assertEqual(tier.price_monthly, 0)
        self.assertEqual(tier.features, [])
        self.assertFalse(tier.highlighted)
