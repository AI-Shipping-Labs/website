"""E2E coverage for issue #1591: nameless members are greeted ``Hi there,``.

Member email templates open with ``Hi {{ user_name }},``. ``user_name`` used
to resolve through ``display_name``, which falls back to the email local-part,
so a member with no name on file received ``Hi x.arrieta,``. The fallback now
resolves in ``EmailService`` (and in the Studio preview context), so no
template file changes and operator overrides in the database are fixed too.

The Studio preview pane gained a recipient selector so a copy reviewer can
see the degraded greeting before shipping. Scenarios mirror the issue body.
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers
from playwright_tests.conftest import goto_with_retry
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.db import connection  # noqa: E402

# Local-only: seeds users directly and injects session cookies.
pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]

STAFF_EMAIL = "greet1591-staff@test.com"

_PREVIEW_REFRESHED = (
    "(prev) => {"
    "  const f = document.querySelector('[data-testid=\"email-template-preview\"]');"
    "  const s = document.querySelector('[data-testid=\"preview-status\"]');"
    "  return f && f.getAttribute('srcdoc') !== prev"
    "    && s && s.textContent.trim() === 'Up to date';"
    "}"
)


def _srcdoc(page):
    return page.locator(
        '[data-testid="email-template-preview"]'
    ).get_attribute("srcdoc")


def _open_editor(page, django_server, template_name):
    goto_with_retry(
        page,
        f"{django_server}/studio/email-templates/{template_name}/edit/",
        wait_until="domcontentloaded",
    )
    page.locator('[data-testid="preview-status"]').wait_for(state="visible")
    page.wait_for_function(
        "() => {"
        "  const el = document.querySelector('[data-testid=\"preview-status\"]');"
        "  return el && el.textContent && el.textContent.trim() === 'Up to date';"
        "}",
        timeout=20000,
    )
    srcdoc = _srcdoc(page)
    assert srcdoc, f"Preview iframe srcdoc empty for {template_name!r}"
    return srcdoc


def _select_recipient(page, value):
    """Change the preview recipient and wait for the refreshed srcdoc."""
    before = _srcdoc(page)
    page.locator('[data-testid="preview-recipient"]').select_option(value)
    page.wait_for_function(_PREVIEW_REFRESHED, arg=before, timeout=20000)
    return _srcdoc(page)


def _staff_page(browser):
    _ensure_tiers()
    _create_staff_user(STAFF_EMAIL)
    return _auth_context(browser, STAFF_EMAIL).new_page()


# ---------------------------------------------------------------------------
# Scenario 1: copy reviewer sees how a nameless member is greeted
# ---------------------------------------------------------------------------


@pytest.mark.core
@browser_journey
def test_copy_reviewer_sees_the_nameless_greeting(django_server, browser):
    page = _staff_page(browser)
    srcdoc = _open_editor(page, django_server, "welcome")

    assert "Hi Ada," in srcdoc

    selector = page.locator('[data-testid="preview-recipient"]')
    assert selector.is_visible()
    assert selector.input_value() == "named"
    assert "Roughly 7 in 10 accounts have no name on file." in page.content()

    degraded = _select_recipient(page, "no_name")

    assert "Hi there," in degraded
    assert "Hi Ada," not in degraded
    # The greeting carries no address and no email handle.
    lead_in = degraded.split("Hi there,")[0][-300:]
    assert "@" not in lead_in


# ---------------------------------------------------------------------------
# Scenario 2: switching back keeps named members named
# ---------------------------------------------------------------------------


@pytest.mark.core
@browser_journey
def test_copy_reviewer_switches_back_to_the_named_recipient(
    django_server, browser
):
    page = _staff_page(browser)
    _open_editor(page, django_server, "free_welcome")

    degraded = _select_recipient(page, "no_name")
    assert "Hi there," in degraded

    named = _select_recipient(page, "named")
    assert "Hi Ada," in named
    assert "Hi there," not in named

    # Only the greeting differs between the two previews.
    assert degraded.replace("Hi there,", "GREETING") == named.replace(
        "Hi Ada,", "GREETING"
    )


# ---------------------------------------------------------------------------
# Scenario 3: an operator override is fixed too
# ---------------------------------------------------------------------------


@pytest.mark.core
@browser_journey
def test_operator_override_still_degrades_for_nameless_members(
    django_server, browser
):
    """The reason the fallback is resolved at the injection site rather than
    as ``|default:"there"`` inside the shipped ``.md`` files."""
    page = _staff_page(browser)
    _open_editor(page, django_server, "password_reset")

    _select_recipient(page, "no_name")

    body = page.locator("#tpl-body")
    original = body.input_value()
    edited = original.replace("Hi {{ user_name }},", "Hello {{ user_name }},")
    assert edited != original, "password_reset no longer greets on user_name"

    before = _srcdoc(page)
    body.fill(edited)
    page.wait_for_function(_PREVIEW_REFRESHED, arg=before, timeout=20000)

    # The recipient selection survives an edit-triggered refresh.
    assert page.locator(
        '[data-testid="preview-recipient"]'
    ).input_value() == "no_name"
    assert "Hello there," in _srcdoc(page)

    page.get_by_role("button", name="Save override").click()
    page.wait_for_url("**/studio/email-templates/", timeout=20000)

    srcdoc = _open_editor(page, django_server, "password_reset")
    assert "Hello {{ user_name }}," in page.locator("#tpl-body").input_value()
    assert page.locator(
        '[data-testid="preview-recipient"]'
    ).input_value() == "named"
    assert "Hello Ada," in srcdoc

    assert "Hello there," in _select_recipient(page, "no_name")


# ---------------------------------------------------------------------------
# Scenarios 4-6: security notice, Maven welcome, partner intro
# ---------------------------------------------------------------------------


@pytest.mark.core
@browser_journey
def test_security_notice_greets_nameless_members_neutrally(
    django_server, browser
):
    page = _staff_page(browser)
    _open_editor(page, django_server, "account_email_changed_notice")

    degraded = _select_recipient(page, "no_name")

    assert "Hi there," in degraded
    assert "login email" in degraded.lower()
    assert "Hi Ada," not in degraded


@pytest.mark.core
@browser_journey
def test_maven_welcome_greets_nameless_enrollees_neutrally(
    django_server, browser
):
    page = _staff_page(browser)
    _open_editor(page, django_server, "maven_welcome")

    degraded = _select_recipient(page, "no_name")

    assert "Hi there," in degraded
    assert "Hi Ada," not in degraded
    # The enrollment sentence still follows the greeting.
    assert "enrol" in degraded.lower() or "welcome" in degraded.lower()


@pytest.mark.core
@browser_journey
def test_sprint_partner_intro_keeps_partner_labels(django_server, browser):
    page = _staff_page(browser)
    _open_editor(page, django_server, "sprint_partner_intro")

    degraded = _select_recipient(page, "no_name")

    assert "Hi there," in degraded
    # The greeting went neutral; the partner labels did not.
    assert "Grace Hopper" in degraded
    assert "alan.turing" in degraded


# ---------------------------------------------------------------------------
# Scenario 7: nameless member resets their password
# ---------------------------------------------------------------------------


@pytest.mark.core
@browser_journey
def test_nameless_member_password_reset_is_greeted_as_a_person(
    django_server, browser
):
    _ensure_tiers()
    _create_user("nameless-reset@test.com")

    page = browser.new_context().new_page()
    goto_with_retry(
        page, f"{django_server}/accounts/login/", wait_until="domcontentloaded"
    )
    page.locator("#forgot-password-link").click()
    page.wait_for_selector("#password-reset-email", timeout=20000)
    page.fill("#password-reset-email", "nameless-reset@test.com")
    page.locator("#password-reset-request-submit").click()

    success = page.locator("#password-reset-request-success")
    success.wait_for(state="visible", timeout=20000)
    assert "password reset instructions" in success.inner_text()

    # Staff can find the delivery in the Studio email log.
    staff = _staff_page(browser)
    goto_with_retry(
        staff,
        f"{django_server}/studio/email-log/?q=nameless-reset@test.com",
        wait_until="domcontentloaded",
    )
    assert "nameless-reset@test.com" in staff.content()

    from accounts.models import User
    from email_app.services.email_service import EmailService

    user = User.objects.get(email="nameless-reset@test.com")
    assert not user.first_name and not user.last_name
    _subject, body = EmailService()._render_template(
        "password_reset",
        user,
        {"reset_url": "https://example.com/reset?token=demo"},
    )
    connection.close()

    assert "Hi there," in body
    assert "nameless-reset" not in body


# ---------------------------------------------------------------------------
# Scenario 8: a named member is unaffected
# ---------------------------------------------------------------------------


@pytest.mark.core
@browser_journey
def test_named_member_registration_email_keeps_their_name(
    django_server, browser
):
    from events.models import Event, EventRegistration

    _ensure_tiers()
    _create_user("main@test.com", first_name="Ada")

    from accounts.models import User

    User.objects.filter(email="main@test.com").update(last_name="Lovelace")
    EventRegistration.objects.all().delete()
    event = Event.objects.create(
        slug="greet1591-evt",
        title="Greeting Regression Event",
        description="An open event used to trigger a transactional email.",
        start_datetime=timezone.now() + datetime.timedelta(days=7),
        status="upcoming",
    )
    connection.close()

    page = _auth_context(browser, "main@test.com").new_page()
    goto_with_retry(
        page,
        f"{django_server}{event.get_absolute_url()}",
        wait_until="domcontentloaded",
    )
    page.locator("#register-btn").click()
    page.wait_for_selector(
        '[data-testid="event-registered-confirmation"]', timeout=20000
    )
    confirmation = page.locator(
        '[data-testid="event-registered-confirmation"]'
    )
    assert "You're registered!" in confirmation.inner_text()

    from email_app.services.email_service import EmailService
    from email_app.services.preview_contexts import get_preview_context

    member = User.objects.get(email="main@test.com")
    context = get_preview_context("event_registration")
    context.pop("user_name", None)
    _subject, body = EmailService()._render_template(
        "event_registration", member, context,
    )
    connection.close()

    assert "Hi Ada Lovelace," in body
    assert "Hi there," not in body


# ---------------------------------------------------------------------------
# Scenario 9: the preview selection never leaks into a real send
# ---------------------------------------------------------------------------


@pytest.mark.core
@browser_journey
def test_send_test_addresses_the_operator_by_their_own_name(
    django_server, browser
):
    _ensure_tiers()
    _create_staff_user(STAFF_EMAIL)

    from accounts.models import User

    User.objects.filter(email=STAFF_EMAIL).update(first_name="Grace")
    connection.close()

    page = _auth_context(browser, STAFF_EMAIL).new_page()
    _open_editor(page, django_server, "welcome")
    _select_recipient(page, "no_name")

    goto_with_retry(
        page,
        f"{django_server}/studio/email-templates/",
        wait_until="domcontentloaded",
    )
    form = page.locator('form[action*="/welcome/send-test/"]').first
    form.locator('button[type="submit"], input[type="submit"]').first.click()
    page.wait_for_load_state("domcontentloaded")

    assert "Test email sent" in page.content()

    from email_app.models import EmailLog

    log = (
        EmailLog.objects.filter(email_type="welcome")
        .order_by("-sent_at")
        .first()
    )
    connection.close()
    assert log is not None
    assert log.recipient_email == STAFF_EMAIL
