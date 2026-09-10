"""Studio workflow coverage for email alternative-part changes (#1567)."""

import os
from unittest.mock import patch

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, ensure_tiers
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


def _wait_for_preview(page):
    expect(page.get_by_test_id('preview-status')).to_have_text(
        'Up to date', timeout=15000,
    )


@pytest.mark.core
@browser_journey
def test_staff_edits_previews_and_test_sends_maven_welcome(
    django_server, browser,
):
    from django.db import connection

    from email_app.models import EmailLog, EmailTemplateOverride
    from email_app.services.email_service import EmailService

    ensure_tiers()
    EmailLog.objects.all().delete()
    EmailTemplateOverride.objects.filter(template_name='maven_welcome').delete()
    create_staff_user('plain-template-1567@example.com')
    connection.close()

    context = auth_context(browser, 'plain-template-1567@example.com')
    page = context.new_page()
    page.goto(
        f'{django_server}/studio/email-templates/maven_welcome/edit/',
        wait_until='domcontentloaded',
    )
    _wait_for_preview(page)

    preview = page.frame_locator('[data-testid="email-template-preview"]')
    expected_paths = (
        '/accounts/login/',
        '/api/password-reset',
        '/community/slack',
        '/onboarding/',
        '/api/verify-and-subscribe',
        '/api/maven-email-opt-out',
    )
    for path in expected_paths:
        expect(preview.locator(f'a[href*="{path}"]')).to_have_count(1)

    body = page.locator('#tpl-body')
    original = body.input_value()
    body.fill(
        original
        + '\n\nPreview draft for {{ user_name }}: '
        + '[Review](https://example.test/review).'
    )
    _wait_for_preview(page)
    expect(preview.locator('body')).to_contain_text('Preview draft for Ada')
    expect(preview.locator('a[href="https://example.test/review"]')).to_have_text(
        'Review',
    )
    expect(preview.locator('body')).not_to_contain_text('{{ user_name }}')

    page.get_by_role('button', name='Save override').click()
    page.wait_for_load_state('domcontentloaded')
    row = page.locator('tr[data-template-name="maven_welcome"]')
    with patch.object(
        EmailService, '_send_ses', return_value='playwright-template-ses',
    ):
        row.get_by_role('button', name='Send test to me').click()
        page.wait_for_load_state('domcontentloaded')

    expect(page.get_by_text('Test email sent to plain-template-1567@example.com.')).to_be_visible()
    assert EmailLog.objects.filter(
        recipient_email='plain-template-1567@example.com',
        email_type='maven_welcome',
    ).count() == 1
    connection.close()
    context.close()


@pytest.mark.core
@browser_journey
def test_staff_previews_and_test_sends_markdown_campaign(django_server, browser):
    from django.db import connection

    from email_app.models import EmailCampaign
    from email_app.services.email_service import EmailService

    ensure_tiers()
    EmailCampaign.objects.all().delete()
    staff = create_staff_user('plain-campaign-1567@example.com')
    campaign = EmailCampaign.objects.create(
        subject='Plain alternative campaign',
        body=(
            '# Campaign heading\n\n- First item\n- Second item\n\n'
            '[Open the guide](https://example.test/guide)'
        ),
        status='draft',
    )
    campaign_id = campaign.pk
    connection.close()

    context = auth_context(browser, staff.email)
    page = context.new_page()
    detail_url = f'{django_server}/studio/campaigns/{campaign_id}/'
    page.goto(detail_url, wait_until='domcontentloaded')

    preview = page.frame_locator('[data-testid="campaign-preview-iframe"]')
    expect(preview.get_by_role('heading', name='Campaign heading')).to_be_visible()
    expect(preview.locator('li')).to_have_count(2)
    expect(preview.locator('a[href="https://example.test/guide"]')).to_have_text(
        'Open the guide',
    )

    page.locator('#test-recipients').fill(staff.email)
    with patch.object(
        EmailService, '_send_ses', return_value='playwright-campaign-ses',
    ):
        page.get_by_role('button', name='Send Test').click()
        page.wait_for_load_state('domcontentloaded')

    expect(page.get_by_text(f'Test email sent to 1 address(es): {staff.email}.')).to_be_visible()
    campaign.refresh_from_db()
    assert campaign.status == 'draft'
    connection.close()

    page.reload(wait_until='domcontentloaded')
    expect(preview.get_by_role('heading', name='Campaign heading')).to_be_visible()
    expect(page.get_by_role('button', name='Send Test')).to_be_visible()
    expect(page.get_by_test_id('send-campaign-btn')).to_be_visible()
    assert page.get_by_text('Plain-text', exact=False).count() == 0
    context.close()
