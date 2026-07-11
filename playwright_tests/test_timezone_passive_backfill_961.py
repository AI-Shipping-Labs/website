"""End-to-end tests for issue #961: passive browser-timezone backfill.

When a signed-in user has an empty ``preferred_timezone``, the base
template silently posts the browser-resolved IANA zone to
``/account/api/timezone-preference`` on the next authenticated page load,
so subsequent transactional emails render in the user's local zone instead
of falling back to UTC. The backfill:

- fires only for authenticated users with an empty preference,
- never overwrites a value that is already set,
- never runs for anonymous visitors,
- is silent and never blocks the page.

We set the real ``Intl`` zone via Playwright's ``timezone_id`` context
option so the page uses a genuine ``Intl.DateTimeFormat`` value (not a
stub), matching the human-verification loop in the issue.
"""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import DEFAULT_PASSWORD, VIEWPORT
from playwright_tests.conftest import create_session_for_user as _create_session

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Local-only: seeds the DB and injects session cookies, so it cannot run
# against the deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

EMAIL_SUFFIX = "@tz-backfill.test"


def _clear_users():
    from django.db import connection

    from accounts.models import User

    User.objects.filter(email__endswith=EMAIL_SUFFIX).delete()
    connection.close()


def _create_user(email, preferred_timezone=""):
    from django.db import connection

    from accounts.models import User
    from payments.models import Tier
    from playwright_tests.conftest import ensure_tiers

    ensure_tiers()
    tier = Tier.objects.get(slug="free")
    user, _ = User.objects.get_or_create(email=email)
    user.set_password(DEFAULT_PASSWORD)
    user.email_verified = True
    user.tier = tier
    user.preferred_timezone = preferred_timezone
    user.save()
    connection.close()
    return user


def _stored_timezone(email):
    from django.db import connection

    from accounts.models import User

    value = User.objects.get(email=email).preferred_timezone
    connection.close()
    return value


def _auth_context(browser, email, django_db_blocker, timezone_id=None):
    with django_db_blocker.unblock():
        session_key = _create_session(email)
    kwargs = {"viewport": VIEWPORT}
    if timezone_id is not None:
        kwargs["timezone_id"] = timezone_id
    context = browser.new_context(**kwargs)
    context.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "a" * 32,
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context


@pytest.mark.django_db(transaction=True)
def test_empty_tz_user_is_silently_backfilled_on_first_visit(
    django_server, django_db_blocker, browser
):
    """Empty-preference user gets their browser zone persisted on page load."""
    email = f"backfill{EMAIL_SUFFIX}"
    with django_db_blocker.unblock():
        _clear_users()
        _create_user(email)

    context = _auth_context(
        browser, email, django_db_blocker, timezone_id="Europe/Berlin"
    )
    page = context.new_page()

    # Capture the passive POST so we can assert it actually fired.
    with page.expect_request(
        lambda r: r.url.endswith("/account/api/timezone-preference")
        and r.method == "POST"
    ) as req_info:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

    request = req_info.value
    assert request.post_data_json["timezone"] == "Europe/Berlin"
    assert request.post_data_json["passive"] is True
    assert request.response().ok

    # The value is now persisted; the Account page reflects it.
    page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
    expect(page.get_by_test_id("account-timezone-input")).to_have_value(
        "Europe/Berlin"
    )

    with django_db_blocker.unblock():
        assert _stored_timezone(email) == "Europe/Berlin"
    context.close()


@pytest.mark.django_db(transaction=True)
def test_existing_tz_is_never_overwritten_by_passive_detection(
    django_server, django_db_blocker, browser
):
    """A user who already chose a zone is not clobbered by the browser zone."""
    email = f"chosen{EMAIL_SUFFIX}"
    with django_db_blocker.unblock():
        _clear_users()
        _create_user(email, preferred_timezone="America/New_York")

    context = _auth_context(
        browser, email, django_db_blocker, timezone_id="Europe/Berlin"
    )
    page = context.new_page()

    posted = {"fired": False}
    page.on(
        "request",
        lambda r: posted.update(fired=True)
        if r.url.endswith("/account/api/timezone-preference")
        and r.method == "POST"
        else None,
    )

    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")

    # The client must not even fire the request (flag is false).
    assert posted["fired"] is False

    page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
    expect(page.get_by_test_id("account-timezone-input")).to_have_value(
        "America/New_York"
    )

    with django_db_blocker.unblock():
        assert _stored_timezone(email) == "America/New_York"
    context.close()


@pytest.mark.django_db(transaction=True)
def test_manual_clear_is_not_immediately_rebackfilled(
    django_server, django_db_blocker, browser
):
    """Clearing the zone in Account settings is respected on the same load."""
    email = f"clear{EMAIL_SUFFIX}"
    with django_db_blocker.unblock():
        _clear_users()
        _create_user(email, preferred_timezone="Europe/Berlin")

    context = _auth_context(
        browser, email, django_db_blocker, timezone_id="Europe/Berlin"
    )
    page = context.new_page()
    page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

    page.get_by_test_id("account-timezone-input").select_option("")
    page.get_by_test_id("save-timezone-btn").click()
    expect(page.get_by_test_id("account-timezone-input")).to_have_value("")
    expect(page.get_by_test_id("timezone-preference-status")).to_contain_text(
        "Using browser timezone."
    )

    # The manual clear must persist; it is not re-backfilled by the
    # passive request on this same load.
    with django_db_blocker.unblock():
        assert _stored_timezone(email) == ""
    context.close()


@pytest.mark.django_db(transaction=True)
def test_manual_set_from_account_settings_persists(
    django_server, django_db_blocker, browser
):
    """A deliberate Account-settings save persists and survives reload."""
    email = f"set{EMAIL_SUFFIX}"
    with django_db_blocker.unblock():
        _clear_users()
        _create_user(email)

    context = _auth_context(
        browser, email, django_db_blocker, timezone_id="Europe/Berlin"
    )
    page = context.new_page()
    page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

    timezone_input = page.get_by_test_id("account-timezone-input")
    timezone_input.select_option("Asia/Tokyo")
    page.get_by_test_id("save-timezone-btn").click()

    expect(page.get_by_test_id("timezone-preference-status")).to_contain_text(
        "Asia/Tokyo"
    )

    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("account-timezone-input")).to_have_value(
        "Asia/Tokyo"
    )

    with django_db_blocker.unblock():
        assert _stored_timezone(email) == "Asia/Tokyo"
    context.close()


@pytest.mark.django_db(transaction=True)
def test_anonymous_visitor_triggers_no_detection(
    django_server, django_db_blocker, browser
):
    """Anonymous visitors never call the timezone endpoint."""
    context = browser.new_context(viewport=VIEWPORT, timezone_id="Europe/Berlin")
    page = context.new_page()

    posted = {"fired": False}
    page.on(
        "request",
        lambda r: posted.update(fired=True)
        if r.url.endswith("/account/api/timezone-preference")
        else None,
    )

    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")

    assert posted["fired"] is False
    # Page renders normally (homepage has a body).
    expect(page.locator("body")).to_be_visible()
    context.close()
