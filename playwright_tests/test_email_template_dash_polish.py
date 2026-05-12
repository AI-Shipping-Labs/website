"""E2E coverage for issue #600: no ASCII ` -- ` in user-facing email prose.

The Studio email-template editor renders each on-disk template through the
same markdown -> HTML pipeline that real sends use, then injects the result
into a preview iframe via JavaScript (POST to
``/studio/email-templates/<name>/preview/``). We drive the editor page,
wait for the iframe to populate, and assert on the rendered preview HTML.

Scenarios mirror the issue body:

1. Event registration preview — calendar-invite sentence reads cleanly.
2. Email verification preview — sign-in sentence reads cleanly.
3. Every shipped transactional template preview is free of ` -- ` and
   the ``&mdash;`` HTML entity.
"""

import os

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


# Shipped transactional templates that have a markdown file on disk. Kept
# in sync with ``email_app/email_templates/*.md`` (verified 2026-05-12).
SHIPPED_EMAIL_TEMPLATES = [
    "welcome",
    "welcome_imported",
    "email_verification",
    "email_verification_reminder",
    "event_registration",
    "event_reminder",
    "cancellation",
    "community_invite",
    "payment_failed",
    "password_reset",
    "lead_magnet_delivery",
]


def _open_preview(page, django_server, template_name):
    """Open the Studio edit page for ``template_name`` and return the
    rendered preview HTML (the iframe ``srcdoc`` attribute).

    The editor JS issues a POST to the preview endpoint on load and
    again after every keystroke. We poll for the ``srcdoc`` attribute
    to appear (rather than relying on a fixed sleep) and wait for the
    visible status label to flip to ``Up to date``.
    """
    url = f"{django_server}/studio/email-templates/{template_name}/edit/"
    page.goto(url, wait_until="domcontentloaded")

    # The status label flips to "Up to date" after the initial fetch
    # resolves and the iframe srcdoc is set. Wait on that instead of a
    # blind sleep so the test stays fast on a quick box and still
    # reliable on a slow one.
    status = page.locator('[data-testid="preview-status"]')
    status.wait_for(state="visible")
    page.wait_for_function(
        "() => {"
        "  const el = document.querySelector('[data-testid=\"preview-status\"]');"
        "  return el && el.textContent && el.textContent.trim() === 'Up to date';"
        "}",
        timeout=10000,
    )

    iframe = page.locator('[data-testid="email-template-preview"]')
    srcdoc = iframe.get_attribute("srcdoc")
    assert srcdoc, f"Preview iframe srcdoc empty for {template_name!r}"
    return srcdoc


# ---------------------------------------------------------------------------
# Scenario 1: event_registration preview is clean
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestEventRegistrationPreviewHasNoDoubleDash:
    """The event_registration preview renders the calendar-invite copy
    without the ASCII ` -- ` sequence."""

    def test_calendar_invite_sentence_reads_cleanly(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        srcdoc = _open_preview(page, django_server, "event_registration")

        # The double-dash ASCII fallback must not appear anywhere in the
        # rendered preview body.
        assert " -- " not in srcdoc, (
            "event_registration preview still contains ' -- ' fallback; "
            "expected an em-dash or rewritten sentence."
        )

        # The calendar-invite copy still mentions the .ics attachment.
        # After issue #588 the wording is split across the "Add to your
        # calendar" block and a line referencing the ".ics" attachment.
        # Either reading is fine as long as both clauses are present and
        # ASCII ` -- ` is gone.
        assert ".ics" in srcdoc, (
            "event_registration preview no longer mentions the .ics "
            "calendar attachment; copy may have drifted further."
        )

        # No HTML-entity em-dash either: these are markdown bodies and
        # the entity won't decode in plain-text mail.
        assert "&mdash;" not in srcdoc, (
            "event_registration preview must use the literal U+2014 "
            "character, not the &mdash; HTML entity."
        )


# ---------------------------------------------------------------------------
# Scenario 2: email_verification preview is clean
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestEmailVerificationPreviewHasNoDoubleDash:
    """The email_verification preview renders the sign-in sentence
    without the ASCII ` -- ` sequence."""

    def test_sign_in_sentence_reads_cleanly(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        srcdoc = _open_preview(page, django_server, "email_verification")

        assert " -- " not in srcdoc, (
            "email_verification preview still contains ' -- ' fallback; "
            "expected an em-dash or rewritten sentence."
        )

        # The sign-in copy is either split into two sentences ending in
        # ``/accounts/login/.`` OR joined with a real em-dash. Accept
        # either reading so the test does not lock the copywriter into
        # one specific rewrite.
        login_split = "/accounts/login/." in srcdoc
        login_emdash = "/accounts/login/ —" in srcdoc
        assert login_split or login_emdash, (
            "email_verification preview should either split the sign-in "
            "sentence after `/accounts/login/.` or use a real em-dash; "
            "neither reading was found in the rendered preview."
        )

        # The sign-in clause must still mention the future password email
        # so we know the copy was not silently dropped.
        assert "set a password" in srcdoc, (
            "email_verification preview no longer mentions the future "
            "password email; the sentence may have been deleted."
        )

        assert "&mdash;" not in srcdoc, (
            "email_verification preview must use the literal U+2014 "
            "character, not the &mdash; HTML entity."
        )


# ---------------------------------------------------------------------------
# Scenario 3: every shipped transactional preview is clean
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAllTransactionalPreviewsHaveNoDoubleDash:
    """Sweep every shipped transactional template's preview to make sure
    no new ASCII ` -- ` or ``&mdash;`` entity creeps in."""

    def test_no_ascii_double_dash_in_any_preview(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        offenders = []
        entity_offenders = []
        for template_name in SHIPPED_EMAIL_TEMPLATES:
            srcdoc = _open_preview(page, django_server, template_name)
            if " -- " in srcdoc:
                offenders.append(template_name)
            if "&mdash;" in srcdoc:
                entity_offenders.append(template_name)

        assert not offenders, (
            "These transactional template previews still contain the "
            f"ASCII ' -- ' fallback: {offenders}. Use a real em-dash "
            "(U+2014) or rewrite the sentence."
        )
        assert not entity_offenders, (
            "These transactional template previews use the &mdash; HTML "
            f"entity: {entity_offenders}. Markdown email bodies need the "
            "literal U+2014 character so plain-text mail still reads."
        )
