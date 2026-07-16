"""Operator-to-visitor runtime configuration journey for issue #1268."""

import json
import os
from pathlib import Path

import pytest
import yaml

from playwright_tests.conftest import auth_context, create_staff_user

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
]


def test_staff_rotates_payment_links_without_a_deploy(django_server, browser):
    from django.conf import settings
    from django.db import connection

    from content.models import SiteConfig
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.filter(key='STRIPE_PAYMENT_LINKS').delete()
    tiers_path = (
        Path(settings.BASE_DIR) / 'content' / 'tests' / 'fixtures' / 'tiers.yaml'
    )
    SiteConfig.objects.update_or_create(
        key='tiers',
        defaults={'data': yaml.safe_load(tiers_path.read_text())},
    )
    connection.close()
    links = {
        tier: {
            period: f'https://checkout.runtime.test/{tier}/{period}'
            for period in ('monthly', 'annual')
        }
        for tier in ('basic', 'main', 'premium')
    }

    create_staff_user('runtime-settings-admin@test.com')
    staff_context = auth_context(browser, 'runtime-settings-admin@test.com')
    staff_page = staff_context.new_page()
    staff_page.goto(f'{django_server}/studio/settings/#payments')
    stripe_card = staff_page.locator('#integration-stripe')
    stripe_card.locator('#field-STRIPE_PAYMENT_LINKS').fill(json.dumps(links))
    stripe_card.get_by_role('button', name='Save Stripe').click()
    staff_page.wait_for_load_state('domcontentloaded')
    assert stripe_card.locator('[data-source-badge="db"]').count() >= 1

    visitor = browser.new_page()
    visitor.goto(f'{django_server}/pricing')
    pricing_cta = visitor.locator(
        '[data-tier-card="main"] .tier-cta-link',
    )
    assert pricing_cta.get_attribute('href') == links['main']['annual']
    visitor.locator('#billing-toggle').click()
    assert pricing_cta.get_attribute('href') == links['main']['monthly']

    visitor.goto(django_server)
    home_cta = visitor.locator('[data-tier-card="main"] .tier-cta-link')
    assert home_cta.get_attribute('href') == links['main']['monthly']
    visitor.locator('#billing-toggle').click()
    assert home_cta.get_attribute('href') == links['main']['annual']

    staff_page.bring_to_front()
    stripe_card.locator(
        '[data-clear-override="STRIPE_PAYMENT_LINKS"]',
    ).click()
    staff_page.wait_for_load_state('domcontentloaded')
    assert stripe_card.locator(
        '[data-source-badge="django_settings"]',
    ).count() == 1
    visitor.bring_to_front()
    visitor.goto(f'{django_server}/pricing')
    restored_cta = visitor.locator(
        '[data-tier-card="main"] .tier-cta-link',
    )
    assert restored_cta.get_attribute('href') != links['main']['annual']

    staff_context.close()
    IntegrationSetting.objects.filter(key='STRIPE_PAYMENT_LINKS').delete()
    connection.close()
