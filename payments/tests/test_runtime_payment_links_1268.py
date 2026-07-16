"""Runtime Stripe Payment Link configuration regressions for issue #1268."""

import json
from pathlib import Path

import yaml
from django.conf import settings
from django.test import TestCase, override_settings

from content.models import SiteConfig
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from payments.stripe_links import get_stripe_payment_links
from tests.fixtures import TierSetupMixin

FALLBACK_LINKS = {
    tier: {
        period: f'https://fallback.test/{tier}/{period}'
        for period in ('monthly', 'annual')
    }
    for tier in ('basic', 'main', 'premium')
}
OVERRIDE_LINKS = {
    tier: {
        period: f'https://override.test/{tier}/{period}'
        for period in ('monthly', 'annual')
    }
    for tier in ('basic', 'main', 'premium')
}


@override_settings(STRIPE_PAYMENT_LINKS=FALLBACK_LINKS)
class RuntimePaymentLinksTest(TierSetupMixin, TestCase):
    def setUp(self):
        clear_config_cache()
        tiers_path = (
            Path(settings.BASE_DIR)
            / 'content' / 'tests' / 'fixtures' / 'tiers.yaml'
        )
        SiteConfig.objects.update_or_create(
            key='tiers',
            defaults={'data': yaml.safe_load(tiers_path.read_text())},
        )

    def tearDown(self):
        IntegrationSetting.objects.filter(key='STRIPE_PAYMENT_LINKS').delete()
        clear_config_cache()

    def _set_override(self, value):
        IntegrationSetting.objects.update_or_create(
            key='STRIPE_PAYMENT_LINKS',
            defaults={'value': value, 'group': 'stripe'},
        )
        clear_config_cache()

    def test_valid_db_json_override_wins(self):
        self._set_override(json.dumps(OVERRIDE_LINKS))

        self.assertEqual(get_stripe_payment_links(), OVERRIDE_LINKS)

    def test_home_and_pricing_share_runtime_override(self):
        self._set_override(json.dumps(OVERRIDE_LINKS))

        pricing = self.client.get('/pricing')
        pricing_links = {
            item['tier'].slug: {
                'monthly': item['payment_link_monthly'],
                'annual': item['payment_link_annual'],
            }
            for item in pricing.context['tiers_data']
            if item['tier'].slug in OVERRIDE_LINKS
        }
        home = self.client.get('/')
        home_links = {
            item['stripe_key']: {
                'monthly': item['payment_link_monthly'],
                'annual': item['payment_link_annual'],
            }
            for item in home.context['tiers']
        }

        self.assertEqual(pricing.status_code, 200)
        self.assertEqual(home.status_code, 200)
        self.assertEqual(pricing_links, OVERRIDE_LINKS)
        self.assertEqual(home_links, OVERRIDE_LINKS)

    def test_invalid_overrides_are_redacted_and_fall_back_completely(self):
        invalid_values = [
            'secret-invalid-json',
            '[]',
            json.dumps({'basic': OVERRIDE_LINKS['basic']}),
            json.dumps({**OVERRIDE_LINKS, 'extra': OVERRIDE_LINKS['basic']}),
            json.dumps({
                **OVERRIDE_LINKS,
                'basic': {'monthly': '', 'annual': 'secret-invalid-link'},
            }),
        ]
        for invalid in invalid_values:
            with self.subTest(invalid=invalid[:12]):
                self._set_override(invalid)
                with self.assertLogs('payments.stripe_links', 'WARNING') as logs:
                    resolved = get_stripe_payment_links()
                self.assertEqual(resolved, FALLBACK_LINKS)
                self.assertNotIn(invalid, ' '.join(logs.output))

        self._set_override('secret-invalid-json')
        self.assertEqual(self.client.get('/pricing').status_code, 200)
        self.assertEqual(self.client.get('/').status_code, 200)
