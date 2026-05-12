import datetime
import os
import re

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import DEFAULT_PASSWORD, VIEWPORT
from playwright_tests.conftest import create_session_for_user as _create_session

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _clear_events_settings_and_users():
    from django.db import connection

    from accounts.models import User
    from events.models import Event, EventRegistration
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    IntegrationSetting.objects.filter(key='EVENT_DISPLAY_TIMEZONE').delete()
    User.objects.filter(email__endswith='@timezone.test').delete()
    clear_config_cache()
    connection.close()


def _create_fixed_event(slug='local-time-event', status='upcoming', location='Zoom'):
    from django.db import connection

    from events.models import Event

    event = Event.objects.create(
        title='Local Time Event',
        slug=slug,
        description='Timezone display test event.',
        start_datetime=datetime.datetime(2026, 4, 13, 16, 30, tzinfo=datetime.UTC),
        end_datetime=datetime.datetime(2026, 4, 13, 18, 0, tzinfo=datetime.UTC),
        status=status,
        location=location,
        timezone='Europe/Berlin',
    )
    connection.close()
    return event


def _set_default_timezone(timezone_name):
    from django.db import connection

    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key='EVENT_DISPLAY_TIMEZONE',
        defaults={
            'value': timezone_name,
            'group': 'site',
            'is_secret': False,
            'description': 'Default public event timezone.',
        },
    )
    clear_config_cache()
    connection.close()


def _create_user(email, preferred_timezone=''):
    from django.db import connection

    from accounts.models import User
    from payments.models import Tier
    from playwright_tests.conftest import ensure_tiers

    ensure_tiers()
    tier = Tier.objects.get(slug='free')
    user, _ = User.objects.get_or_create(email=email)
    user.set_password(DEFAULT_PASSWORD)
    user.email_verified = True
    user.tier = tier
    user.preferred_timezone = preferred_timezone
    user.save()
    connection.close()
    return user


def _auth_context(browser, email, django_db_blocker):
    with django_db_blocker.unblock():
        session_key = _create_session(email)
    context = browser.new_context(viewport=VIEWPORT)
    csrf_token = 'a' * 32
    context.add_cookies([
        {
            'name': 'sessionid',
            'value': session_key,
            'domain': '127.0.0.1',
            'path': '/',
        },
        {
            'name': 'csrftoken',
            'value': csrf_token,
            'domain': '127.0.0.1',
            'path': '/',
        },
    ])
    return context


def _stub_browser_timezone(page, timezone_name=None):
    resolved_timezone = 'undefined' if timezone_name is None else repr(timezone_name)
    page.add_init_script(
        f"""
        (() => {{
          const OriginalDateTimeFormat = Intl.DateTimeFormat;
          Intl.DateTimeFormat = function(locales, options) {{
            const formatter = new OriginalDateTimeFormat(locales, options);
            if (arguments.length === 0) {{
              const originalResolvedOptions = formatter.resolvedOptions.bind(formatter);
              formatter.resolvedOptions = () => ({{
                ...originalResolvedOptions(),
                timeZone: {resolved_timezone},
              }});
            }}
            return formatter;
          }};
          Intl.DateTimeFormat.prototype = OriginalDateTimeFormat.prototype;
        }})();
        """
    )


def _offset_minutes(label):
    match = re.match(r"^GMT([+-])(\d{2}):(\d{2}) ", label)
    assert match is not None
    sign = 1 if match.group(1) == "+" else -1
    return sign * (int(match.group(2)) * 60 + int(match.group(3)))


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_member_finds_saves_and_reloads_timezone(
    django_server, django_db_blocker, browser
):
    with django_db_blocker.unblock():
        _clear_events_settings_and_users()
        _create_user('account-save@timezone.test')

    context = _auth_context(browser, 'account-save@timezone.test', django_db_blocker)
    page = context.new_page()
    page.goto(f'{django_server}/account/', wait_until='domcontentloaded')

    timezone_input = page.get_by_test_id('account-timezone-input')
    timezone_input.fill('Berlin')
    timezone_input.press('ArrowDown')
    timezone_input.press('Enter')
    timezone_input.fill('GMT+02:00 Europe/Berlin')
    page.get_by_test_id('save-timezone-btn').click()

    status = page.get_by_test_id('timezone-preference-status')
    expect(status).to_contain_text('Current timezone: GMT+02:00 Europe/Berlin')

    page.reload(wait_until='domcontentloaded')
    assert timezone_input.input_value() == 'GMT+02:00 Europe/Berlin'
    context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_account_timezone_options_have_offsets_and_are_ordered(
    django_server, django_db_blocker, browser
):
    with django_db_blocker.unblock():
        _clear_events_settings_and_users()
        _create_user('account-order@timezone.test')

    context = _auth_context(browser, 'account-order@timezone.test', django_db_blocker)
    page = context.new_page()
    page.goto(f'{django_server}/account/', wait_until='domcontentloaded')

    labels = page.locator('#timezone-preference-options option').evaluate_all(
        """options => options.slice(0, 80).map(option => option.value)"""
    )
    label_pattern = re.compile(r"^GMT[+-]\d{2}:\d{2} .+$")
    assert all(label_pattern.match(label) for label in labels)

    berlin_option = page.locator(
        '#timezone-preference-options option[value="GMT+02:00 Europe/Berlin"]'
    )
    new_york_option = page.locator(
        '#timezone-preference-options option[value="GMT-04:00 America/New_York"]'
    )
    assert berlin_option.count() == 1
    assert new_york_option.count() == 1

    offset_values = [_offset_minutes(label) for label in labels]
    assert offset_values == sorted(offset_values)
    context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_logged_in_preference_wins_over_browser_timezone(
    django_server, django_db_blocker, browser
):
    with django_db_blocker.unblock():
        _clear_events_settings_and_users()
        _create_fixed_event()
        _create_user('ny@timezone.test', preferred_timezone='America/New_York')

    context = _auth_context(browser, 'ny@timezone.test', django_db_blocker)
    page = context.new_page()
    _stub_browser_timezone(page, 'Europe/Berlin')

    page.goto(f'{django_server}/events/local-time-event', wait_until='domcontentloaded')

    assert (
        'April 13, 2026, 12:30-14:00 America/New_York'
        in page.get_by_test_id('event-time-row').inner_text()
    )
    assert page.get_by_test_id('event-timezone-select').count() == 0
    assert 'Until 18:00 UTC' not in page.content()
    context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_member_clears_timezone_and_uses_browser_timezone(
    django_server, django_db_blocker, browser
):
    with django_db_blocker.unblock():
        _clear_events_settings_and_users()
        _create_fixed_event()
        _create_user('clear@timezone.test', preferred_timezone='America/New_York')

    context = _auth_context(browser, 'clear@timezone.test', django_db_blocker)
    page = context.new_page()
    _stub_browser_timezone(page, 'Europe/Berlin')
    page.goto(f'{django_server}/account/', wait_until='domcontentloaded')
    page.get_by_test_id('clear-timezone-btn').click()
    expect(page.get_by_test_id('timezone-preference-status')).to_contain_text(
        'Using browser timezone.'
    )

    page.goto(f'{django_server}/events/local-time-event', wait_until='domcontentloaded')
    assert (
        'April 13, 2026, 18:30-20:00 Europe/Berlin'
        in page.get_by_test_id('event-time-row').inner_text()
    )
    page.reload(wait_until='domcontentloaded')
    assert (
        'April 13, 2026, 18:30-20:00 Europe/Berlin'
        in page.get_by_test_id('event-time-row').inner_text()
    )
    context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_invalid_account_timezone_does_not_change_preference(
    django_server, django_db_blocker, browser
):
    with django_db_blocker.unblock():
        _clear_events_settings_and_users()
        _create_fixed_event()
        _create_user('invalid@timezone.test', preferred_timezone='Europe/Berlin')

    context = _auth_context(browser, 'invalid@timezone.test', django_db_blocker)
    page = context.new_page()
    page.goto(f'{django_server}/account/', wait_until='domcontentloaded')
    response = page.request.post(
        f'{django_server}/account/api/timezone-preference',
        data={'timezone': 'Invalid/Zone'},
        headers={'X-CSRFToken': 'a' * 32},
    )
    assert response.status == 400

    page.goto(f'{django_server}/events/local-time-event', wait_until='domcontentloaded')
    assert (
        'April 13, 2026, 18:30-20:00 Europe/Berlin'
        in page.get_by_test_id('event-time-row').inner_text()
    )
    context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_anonymous_berlin_visitor_sees_browser_local_time_without_controls(
    django_server, django_db_blocker, page
):
    with django_db_blocker.unblock():
        _clear_events_settings_and_users()
        _create_fixed_event()
    _stub_browser_timezone(page, 'Europe/Berlin')

    page.goto(f'{django_server}/events/local-time-event', wait_until='domcontentloaded')

    assert (
        'April 13, 2026, 18:30-20:00 Europe/Berlin'
        in page.get_by_test_id('event-time-row').inner_text()
    )
    assert page.get_by_test_id('event-timezone-select').count() == 0
    assert 'event-display-timezone' not in page.content()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_anonymous_new_york_visitor_sees_browser_local_time(
    django_server, django_db_blocker, page
):
    with django_db_blocker.unblock():
        _clear_events_settings_and_users()
        _create_fixed_event()
    _stub_browser_timezone(page, 'America/New_York')

    page.goto(f'{django_server}/events/local-time-event', wait_until='domcontentloaded')

    time_text = page.get_by_test_id('event-time-row').inner_text()
    assert 'April 13, 2026, 12:30-14:00 America/New_York' in time_text
    assert 'Europe/Berlin' not in time_text


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_unavailable_browser_timezone_uses_studio_default(
    django_server, django_db_blocker, page
):
    with django_db_blocker.unblock():
        _clear_events_settings_and_users()
        _create_fixed_event(slug='default-timezone-event')
        _set_default_timezone('Europe/Berlin')
    _stub_browser_timezone(page, None)

    page.goto(
        f'{django_server}/events/default-timezone-event',
        wait_until='domcontentloaded',
    )

    assert (
        'April 13, 2026, 18:30-20:00 Europe/Berlin'
        in page.get_by_test_id('event-time-row').inner_text()
    )
    assert page.get_by_test_id('event-timezone-select').count() == 0


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_completed_zoom_location_hidden_but_upcoming_zoom_kept(
    django_server, django_db_blocker, page
):
    with django_db_blocker.unblock():
        _clear_events_settings_and_users()
        _create_fixed_event(
            slug='completed-zoom-event',
            status='completed',
            location='Zoom',
        )
        _create_fixed_event(
            slug='upcoming-zoom-event',
            status='upcoming',
            location='Zoom',
        )

    page.goto(f'{django_server}/events/completed-zoom-event', wait_until='domcontentloaded')
    assert 'Zoom' not in page.locator('article header').inner_text()

    page.goto(f'{django_server}/events/upcoming-zoom-event', wait_until='domcontentloaded')
    assert 'Zoom' in page.locator('article header').inner_text()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_registered_member_keeps_upcoming_zoom_attendance_flow(
    django_server, django_db_blocker, browser
):
    with django_db_blocker.unblock():
        _clear_events_settings_and_users()
        event = _create_fixed_event(
            slug='registered-upcoming-zoom-event',
            status='upcoming',
            location='Zoom',
        )
        user = _create_user('registered@timezone.test')

        from django.db import connection

        from events.models import EventRegistration

        EventRegistration.objects.create(event=event, user=user)
        connection.close()

    context = _auth_context(browser, 'registered@timezone.test', django_db_blocker)
    page = context.new_page()
    page.goto(
        f'{django_server}/events/registered-upcoming-zoom-event',
        wait_until='domcontentloaded',
    )

    header_text = page.locator('article header').inner_text()
    assert 'Zoom' in header_text
    assert "You're registered!" in page.locator('article').inner_text()
    assert page.locator('#unregister-btn').is_visible()
    context.close()
