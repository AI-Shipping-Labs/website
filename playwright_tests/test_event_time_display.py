import datetime
import os

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _clear_events_and_settings():
    from django.db import connection

    from events.models import Event, EventRegistration
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    IntegrationSetting.objects.filter(key='EVENT_DISPLAY_TIMEZONE').delete()
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


@pytest.mark.django_db(transaction=True)
def test_berlin_visitor_sees_local_event_range(django_server, page):
    _clear_events_and_settings()
    _create_fixed_event()
    _stub_browser_timezone(page, 'Europe/Berlin')

    page.goto(f'{django_server}/events/local-time-event', wait_until='domcontentloaded')

    time_row = page.get_by_test_id('event-time-row')
    assert 'April 13, 2026, 18:30-20:00 Europe/Berlin' in time_row.inner_text()
    assert 'Until 18:00 UTC' not in page.content()


@pytest.mark.django_db(transaction=True)
def test_new_york_browser_timezone_replaces_source_timezone(django_server, page):
    _clear_events_and_settings()
    _create_fixed_event()
    _stub_browser_timezone(page, 'America/New_York')

    page.goto(f'{django_server}/events/local-time-event', wait_until='domcontentloaded')

    time_row = page.get_by_test_id('event-time-row')
    assert 'April 13, 2026, 12:30-14:00 America/New_York' in time_row.inner_text()
    assert page.get_by_test_id('event-timezone-select').input_value() == 'America/New_York'


@pytest.mark.django_db(transaction=True)
def test_timezone_selector_updates_without_reload_and_persists(django_server, page):
    _clear_events_and_settings()
    _create_fixed_event()
    _stub_browser_timezone(page, 'Europe/Berlin')

    page.goto(f'{django_server}/events/local-time-event', wait_until='domcontentloaded')
    page.evaluate('window.__timezoneSelectorMarker = 1')
    page.get_by_test_id('event-timezone-select').select_option('America/New_York')

    assert page.evaluate('window.__timezoneSelectorMarker') == 1
    assert 'April 13, 2026, 12:30-14:00 America/New_York' in page.get_by_test_id('event-time-row').inner_text()

    page.reload(wait_until='domcontentloaded')

    assert page.get_by_test_id('event-timezone-select').input_value() == 'America/New_York'
    assert 'April 13, 2026, 12:30-14:00 America/New_York' in page.get_by_test_id('event-time-row').inner_text()


@pytest.mark.django_db(transaction=True)
def test_invalid_saved_timezone_uses_browser_then_default(django_server, page):
    _clear_events_and_settings()
    _create_fixed_event(slug='invalid-saved-timezone')
    _set_default_timezone('Europe/Berlin')
    _stub_browser_timezone(page, 'America/New_York')
    page.add_init_script(
        "window.localStorage.setItem('event-display-timezone', 'Invalid/Zone');"
    )

    page.goto(f'{django_server}/events/invalid-saved-timezone', wait_until='domcontentloaded')

    assert page.get_by_test_id('event-timezone-select').input_value() == 'America/New_York'
    assert 'April 13, 2026, 12:30-14:00 America/New_York' in page.get_by_test_id('event-time-row').inner_text()


@pytest.mark.django_db(transaction=True)
def test_unavailable_browser_timezone_uses_studio_default(django_server, page):
    _clear_events_and_settings()
    _create_fixed_event(slug='default-timezone-event')
    _set_default_timezone('Europe/Berlin')
    _stub_browser_timezone(page, None)

    page.goto(f'{django_server}/events/default-timezone-event', wait_until='domcontentloaded')

    assert page.get_by_test_id('event-timezone-select').input_value() == 'Europe/Berlin'
    assert 'April 13, 2026, 18:30-20:00 Europe/Berlin' in page.get_by_test_id('event-time-row').inner_text()


@pytest.mark.django_db(transaction=True)
def test_completed_zoom_location_hidden_but_upcoming_zoom_kept(django_server, page):
    _clear_events_and_settings()
    _create_fixed_event(slug='completed-zoom-event', status='completed', location='Zoom')
    _create_fixed_event(slug='upcoming-zoom-event', status='upcoming', location='Zoom')

    page.goto(f'{django_server}/events/completed-zoom-event', wait_until='domcontentloaded')
    assert 'Zoom' not in page.locator('article header').inner_text()

    page.goto(f'{django_server}/events/upcoming-zoom-event', wait_until='domcontentloaded')
    assert 'Zoom' in page.locator('article header').inner_text()
