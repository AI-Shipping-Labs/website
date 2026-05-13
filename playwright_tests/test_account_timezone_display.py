"""End-to-end tests for the auto-detected timezone display on /account/.

These cover the issue #582/#596 behaviour: when the user has no saved
``preferred_timezone``, the Display Preferences card detects the browser
timezone via ``Intl.DateTimeFormat().resolvedOptions().timeZone``, selects
the resolved IANA name in the dropdown, and gates the Save button so the
unmodified detection cannot be persisted by mistake. The detection is
display-only -- the value is not saved to the user record until the
user explicitly clicks Save.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import DEFAULT_PASSWORD, VIEWPORT
from playwright_tests.conftest import create_session_for_user as _create_session

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _clear_account_timezone_users():
    """Drop any timezone-test users left over from a previous run."""
    from django.db import connection

    from accounts.models import User

    User.objects.filter(email__endswith='@timezone.test').delete()
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
    """Force ``Intl.DateTimeFormat().resolvedOptions().timeZone`` to a value.

    Pass ``None`` to simulate a browser whose ``timeZone`` resolves to
    ``undefined`` -- the production code must fall back to the existing
    placeholder copy in that case.
    """
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


def _wait_for_timezone_init(page):
    """Wait until the inline init script has marked the input.

    The init runs on ``DOMContentLoaded`` -- we navigate with
    ``wait_until='domcontentloaded'`` but the listener still has to
    fire, so we poll the dataset attribute as a small synchronisation
    point. When detection returns an empty string (the fallback path),
    no attribute is set; callers that exercise that path should not
    call this helper.
    """
    page.locator(
        '#timezone-preference-input[data-detected-timezone]'
    ).wait_for(state='attached', timeout=5000)


@pytest.mark.django_db(transaction=True)
def test_no_preference_shows_detected_iana_name_in_select(
    django_server, django_db_blocker, browser
):
    """Acceptance: empty preference => select shows detected IANA name."""
    with django_db_blocker.unblock():
        _clear_account_timezone_users()
        _create_user('tz-detect@timezone.test')

    context = _auth_context(browser, 'tz-detect@timezone.test', django_db_blocker)
    page = context.new_page()
    _stub_browser_timezone(page, 'Europe/Berlin')

    page.goto(f'{django_server}/account/', wait_until='domcontentloaded')
    _wait_for_timezone_init(page)

    timezone_input = page.get_by_test_id('account-timezone-input')
    assert timezone_input.input_value() == 'Europe/Berlin'
    assert timezone_input.evaluate(
        "el => el.tagName === 'SELECT' && el.classList.contains('app-select')"
    )
    caret_image = timezone_input.evaluate(
        "el => getComputedStyle(el).backgroundImage"
    )
    assert 'linear-gradient' in caret_image
    assert 'url(' not in caret_image
    assert timezone_input.locator('option').first.evaluate(
        "el => el.value === '' && el.textContent.includes('Use browser timezone')"
    )

    expect(page.get_by_test_id('timezone-preference-status')).to_contain_text(
        'Showing your browser timezone: GMT+02:00 Europe/Berlin'
    )
    expect(page.get_by_test_id('timezone-detected-hint')).to_be_visible()
    expect(page.get_by_test_id('timezone-detected-hint')).to_contain_text(
        'Detected: Europe/Berlin'
    )
    context.close()


@pytest.mark.django_db(transaction=True)
def test_saved_preference_is_not_overwritten_by_browser_detection(
    django_server, django_db_blocker, browser
):
    """Saved preference wins over the browser-detected name."""
    with django_db_blocker.unblock():
        _clear_account_timezone_users()
        _create_user(
            'tz-saved@timezone.test', preferred_timezone='America/New_York'
        )

    context = _auth_context(browser, 'tz-saved@timezone.test', django_db_blocker)
    page = context.new_page()
    _stub_browser_timezone(page, 'Europe/Berlin')
    page.goto(f'{django_server}/account/', wait_until='domcontentloaded')

    timezone_input = page.get_by_test_id('account-timezone-input')
    assert re.match(
        r'^GMT[+-]\d{2}:\d{2} America/New_York$',
        timezone_input.locator('option:checked').inner_text(),
    ) is not None
    assert timezone_input.input_value() == 'America/New_York'

    status = page.get_by_test_id('timezone-preference-status')
    expect(status).to_contain_text('Current timezone:')
    expect(status).to_contain_text('America/New_York')

    # Detected hint must NOT show -- the user already chose a preference.
    expect(page.get_by_test_id('timezone-detected-hint')).to_be_hidden()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_auto_detected_value_is_informational_not_persisted(
    django_server, django_db_blocker, browser
):
    """The auto-detected value must not silently end up in the database."""
    with django_db_blocker.unblock():
        _clear_account_timezone_users()
        _create_user('tz-no-save@timezone.test')

    context = _auth_context(browser, 'tz-no-save@timezone.test', django_db_blocker)
    page = context.new_page()
    _stub_browser_timezone(page, 'Europe/Berlin')

    page.goto(f'{django_server}/account/', wait_until='domcontentloaded')
    _wait_for_timezone_init(page)
    timezone_input = page.get_by_test_id('account-timezone-input')
    assert timezone_input.input_value() == 'Europe/Berlin'

    page.reload(wait_until='domcontentloaded')
    _wait_for_timezone_init(page)
    assert page.get_by_test_id('account-timezone-input').input_value() == 'Europe/Berlin'

    with django_db_blocker.unblock():
        from django.db import connection

        from accounts.models import User

        user = User.objects.get(email='tz-no-save@timezone.test')
        connection.close()

    assert user.preferred_timezone == ''
    context.close()


@pytest.mark.django_db(transaction=True)
def test_save_button_disabled_until_input_diverges_from_detection(
    django_server, django_db_blocker, browser
):
    """Save toggles disabled <-> enabled based on value vs detected name."""
    with django_db_blocker.unblock():
        _clear_account_timezone_users()
        _create_user('tz-button@timezone.test')

    context = _auth_context(browser, 'tz-button@timezone.test', django_db_blocker)
    page = context.new_page()
    _stub_browser_timezone(page, 'Europe/Berlin')
    page.goto(f'{django_server}/account/', wait_until='domcontentloaded')
    _wait_for_timezone_init(page)

    save_btn = page.get_by_test_id('save-timezone-btn')
    expect(save_btn).to_have_attribute('aria-disabled', 'true')

    timezone_input = page.get_by_test_id('account-timezone-input')
    timezone_input.select_option('America/New_York')

    # Save becomes enabled the moment the value differs from detection.
    expect(save_btn).not_to_have_attribute('aria-disabled', 'true')

    # Selecting the detected value again re-disables Save.
    timezone_input.select_option('Europe/Berlin')
    expect(timezone_input).to_have_value('Europe/Berlin')
    expect(save_btn).to_have_attribute('aria-disabled', 'true')
    context.close()


@pytest.mark.django_db(transaction=True)
def test_explicit_save_persists_then_survives_reload(
    django_server, django_db_blocker, browser
):
    """Saving a chosen value persists it; reload reads it back."""
    with django_db_blocker.unblock():
        _clear_account_timezone_users()
        _create_user('tz-explicit@timezone.test')

    context = _auth_context(browser, 'tz-explicit@timezone.test', django_db_blocker)
    page = context.new_page()
    _stub_browser_timezone(page, 'Europe/Berlin')
    page.goto(f'{django_server}/account/', wait_until='domcontentloaded')
    _wait_for_timezone_init(page)

    timezone_input = page.get_by_test_id('account-timezone-input')
    timezone_input.select_option('America/New_York')
    page.get_by_test_id('save-timezone-btn').click()

    expect(page.get_by_test_id('timezone-preference-status')).to_contain_text(
        'Current timezone: GMT-04:00 America/New_York'
    )

    page.reload(wait_until='domcontentloaded')
    timezone_input = page.get_by_test_id('account-timezone-input')
    expect(timezone_input).to_have_value('America/New_York')
    expect(timezone_input.locator('option:checked')).to_have_text(
        re.compile(r'^GMT[+-]\d{2}:\d{2} America/New_York$')
    )

    # Once a preference is saved, the Detected hint must not reappear.
    expect(page.get_by_test_id('timezone-detected-hint')).to_be_hidden()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_clear_resets_to_browser_default_option(
    django_server, django_db_blocker, browser
):
    """Clearing a saved preference selects the browser-default option."""
    with django_db_blocker.unblock():
        _clear_account_timezone_users()
        _create_user(
            'tz-clear@timezone.test', preferred_timezone='America/New_York'
        )

    context = _auth_context(browser, 'tz-clear@timezone.test', django_db_blocker)
    page = context.new_page()
    _stub_browser_timezone(page, 'Europe/Berlin')
    page.goto(f'{django_server}/account/', wait_until='domcontentloaded')

    page.get_by_test_id('clear-timezone-btn').click()

    timezone_input = page.get_by_test_id('account-timezone-input')
    expect(timezone_input).to_have_value('')
    expect(timezone_input.locator('option:checked')).to_have_text(
        'Use browser timezone'
    )

    expect(page.get_by_test_id('timezone-preference-status')).to_contain_text(
        'Using browser timezone.'
    )
    expect(page.get_by_test_id('timezone-detected-hint')).to_be_hidden()

    page.reload(wait_until='domcontentloaded')
    _wait_for_timezone_init(page)
    expect(
        page.get_by_test_id('account-timezone-input')
    ).to_have_value('Europe/Berlin')
    context.close()


@pytest.mark.django_db(transaction=True)
def test_detection_failure_keeps_existing_fallback(
    django_server, django_db_blocker, browser
):
    """Missing browser timezone => fall back to the previous copy."""
    with django_db_blocker.unblock():
        _clear_account_timezone_users()
        _create_user('tz-undef@timezone.test')

    context = _auth_context(browser, 'tz-undef@timezone.test', django_db_blocker)
    page = context.new_page()
    _stub_browser_timezone(page, None)

    console_errors = []
    page.on('pageerror', lambda exc: console_errors.append(str(exc)))

    page.goto(f'{django_server}/account/', wait_until='domcontentloaded')
    # No DOMContentLoaded marker poll here -- detection deliberately
    # returned '' and the dataset attribute is never set.
    page.wait_for_load_state('networkidle')

    timezone_input = page.get_by_test_id('account-timezone-input')
    assert timezone_input.input_value() == ''
    expect(page.get_by_test_id('timezone-preference-status')).to_have_text(
        'Using browser timezone.'
    )
    assert console_errors == []
    context.close()


@pytest.mark.django_db(transaction=True)
def test_placeholder_string_never_appears_as_input_value(
    django_server, django_db_blocker, browser
):
    """The placeholder text must never be the visible input value."""
    with django_db_blocker.unblock():
        _clear_account_timezone_users()
        _create_user('tz-placeholder@timezone.test')

    context = _auth_context(browser, 'tz-placeholder@timezone.test', django_db_blocker)
    page = context.new_page()
    _stub_browser_timezone(page, 'Asia/Tokyo')

    page.goto(f'{django_server}/account/', wait_until='domcontentloaded')
    _wait_for_timezone_init(page)

    timezone_input = page.get_by_test_id('account-timezone-input')
    assert timezone_input.input_value() == 'Asia/Tokyo'
    assert timezone_input.input_value() != 'Use browser timezone'
    context.close()
