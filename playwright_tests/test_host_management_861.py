"""Browser proof for scoped host-management landing (#861)."""

import os
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import connection
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context,
    create_staff_user,
    create_user,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.core]
SCREENSHOT_DIR = Path('.tmp/screenshots/issue-861-correction')


def _screenshot(page, name, *, full_page=True):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=SCREENSHOT_DIR / f'{name}.png',
        full_page=full_page,
    )


def _open_email_preview(page, django_server, template_name):
    page.goto(
        f'{django_server}/studio/email-templates/{template_name}/edit/',
        wait_until='domcontentloaded',
    )
    page.wait_for_function(
        "() => document.querySelector('[data-testid=\"preview-status\"]')"
        "?.textContent?.trim() === 'Up to date'",
        timeout=10000,
    )
    preview = page.locator('[data-testid="email-template-preview"]')
    srcdoc = preview.get_attribute('srcdoc')
    assert srcdoc
    return preview, srcdoc


def _setup():
    from events.models import Event, EventRegistration
    from events.services.host_access import generate_host_access_token

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    host = create_user('host-browser-861@test.com')
    other = create_user('other-browser-861@test.com')
    start = timezone.now() + timedelta(days=14)
    event = Event.objects.create(
        title='Browser Host Controls',
        slug='browser-host-controls-861',
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status='upcoming',
        platform='zoom',
        host_email=host.email,
    )
    EventRegistration.objects.create(event=event, user=host)
    token = generate_host_access_token(event, host)
    connection.close()
    return event, host, other, token


@pytest.mark.django_db(transaction=True)
def test_normal_host_can_use_safe_management_landing(django_server, browser):
    event, host, _other, token = _setup()
    context = auth_context(browser, host.email)
    page = context.new_page()
    try:
        response = page.goto(
            f'{django_server}/events/{event.pk}/host/manage?token={token}',
        )
        assert response.status == 200
        assert page.get_by_role('heading', name='Host controls').is_visible()
        assert page.get_by_role('heading', name='Edit event details').is_visible()
        zoom_form = page.locator(
            f'form[action="/events/{event.pk}/host/create-zoom"]',
        )
        assert zoom_form.count() == 1
        assert zoom_form.get_attribute('method').lower() == 'post'
        assert page.locator('input[name="host_email"]').count() == 0
        _screenshot(page, 'host-management-authorized')
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_forwarded_host_link_does_not_authorize_another_member(
    django_server, browser,
):
    event, _host, other, token = _setup()
    context = auth_context(browser, other.email)
    context.add_init_script("localStorage.setItem('theme', 'light')")
    page = context.new_page()
    try:
        response = page.goto(
            f'{django_server}/events/{event.pk}/host/manage?token={token}',
        )
        assert response.status == 403
        assert page.get_by_role(
            'heading', name='Sign in with the designated host account',
        ).is_visible()
        assert page.get_by_role('link', name='Switch account').is_visible()
        events_link = page.get_by_role('link', name='Back to Events')
        assert events_link.is_visible()
        assert events_link.get_attribute('href') == '/events'
        assert token not in page.locator('body').inner_text()
        _screenshot(page, 'host-management-wrong-account-recovery')
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_reassigned_host_link_has_safe_recovery_route(django_server, browser):
    from events.models import Event

    event, host, other, token = _setup()
    Event.objects.filter(pk=event.pk).update(
        host_email=other.email,
        host_access_version='00000000-0000-4000-8000-000000000861',
    )
    connection.close()
    context = auth_context(browser, host.email)
    context.add_init_script("localStorage.setItem('theme', 'light')")
    page = context.new_page()
    try:
        response = page.goto(
            f'{django_server}/events/{event.pk}/host/manage?token={token}',
        )
        assert response.status == 403
        assert page.get_by_role(
            'heading', name='This host link is no longer current',
        ).is_visible()
        body = page.locator('body').inner_text()
        assert 'reassigned' in body
        assert 'Contact the event operator' in body
        assert token not in body
        events_link = page.get_by_role('link', name='Back to Events')
        assert events_link.get_attribute('href') == '/events'
        _screenshot(page, 'host-management-stale-link-recovery')
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_studio_delivery_status_never_renders_raw_provider_detail(
    django_server, browser,
):
    from events.models import HostInviteDelivery

    event, host, _other, _token = _setup()
    private_detail = (
        'secret=browser-host-provider-key '
        'https://provider.test/request?token=private-browser-payload'
    )
    HostInviteDelivery.objects.create(
        event=event,
        user=host,
        access_version=event.host_access_version,
        status=HostInviteDelivery.STATUS_FAILED,
        attempt_count=2,
        last_error=private_detail,
    )
    staff = create_staff_user('host-delivery-staff-861@test.com')
    connection.close()

    context = auth_context(browser, staff.email)
    page = context.new_page()
    try:
        response = page.goto(f'{django_server}/studio/events/{event.pk}/edit')
        assert response.status == 200
        status = page.locator('[data-testid="host-invite-delivery-status"]')
        assert status.is_visible()
        status_text = status.inner_text()
        assert private_detail not in status_text
        assert 'private-browser-payload' not in page.content()
        assert 'Delivery failed; review application logs.' in status_text
        _screenshot(page, 'studio-safe-delivery-diagnostic')
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_host_lifecycle_email_previews_use_truthful_calendar_copy(
    django_server, browser,
):
    staff = create_staff_user('host-copy-staff-861@test.com')
    context = auth_context(browser, staff.email)
    page = context.new_page()
    expected = {
        'event_registration': (
            'includes a calendar invitation for this event',
            'if prompted',
        ),
        'event_rescheduled': (
            'includes an updated calendar invitation',
            'supported calendar apps can apply',
            'if prompted',
        ),
        'event_cancelled': (
            'includes a calendar cancellation update',
            'supported calendar apps can use it',
            'if prompted',
        ),
    }
    try:
        for template_name, required_copy in expected.items():
            preview, srcdoc = _open_email_preview(
                page, django_server, template_name,
            )
            lowered = srcdoc.lower()
            for phrase in required_copy:
                assert phrase in lowered
            assert 'attached' not in lowered
            assert '.ics file' not in lowered
            assert 'overwrite the original entry automatically' not in lowered
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            preview.screenshot(
                path=SCREENSHOT_DIR / f'{template_name}-preview.png',
            )
    finally:
        context.close()
