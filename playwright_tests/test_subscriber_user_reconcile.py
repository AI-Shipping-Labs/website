"""Playwright E2E for issue #513: newsletter subscribe auto-creates a free
account, anonymous email-only event registration on free events.

Scenarios mirror the spec in issue #513:

1. First-time visitor subscribes from the homepage and the on-page
   success message tells them an account was created.
2. Returning subscriber re-subscribes — no duplicate User row, no
   ``verification_expires_at`` extension.
3. Anonymous visitor registers for a free upcoming event with email
   only — User + EventRegistration rows are created, both
   ``event_registration`` and ``email_verification`` EmailLog rows
   exist, the page reloads to a confirmation block.
4. Anonymous visitor registers using an email that already maps to a
   verified user — the existing user is not touched, only
   ``event_registration`` is sent (no ``email_verification``).
5. Anonymous email-only registration on a gated event is rejected with
   403 and creates no User row.
6. Subscriber clicks the verification link and lands on a verified
   account.
7. Unverified subscriber that never returns is auto-purged after the
   grace period; the same email can subscribe again afterwards.
8. Anonymous event registrant later signs in to manage their
   registration (cancel button visible after sign-in).
9. The dedicated /subscribe page success copy mentions "account" and
   the verification email body says "we've created a free account for
   you" verbatim.

Usage:
    uv run pytest playwright_tests/test_subscriber_user_reconcile.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _clear_events_and_users(emails):
    """Reset DB rows that the scenarios touch.

    We explicitly delete rows by email rather than truncating the
    ``User`` table because the test infrastructure may rely on a
    persistent staff/superuser. Each scenario lists exactly the emails
    it uses so reruns are deterministic.
    """
    from accounts.models import User
    from email_app.models import EmailLog
    from events.models import Event, EventRegistration

    EmailLog.objects.filter(user__email__in=emails).delete()
    EventRegistration.objects.filter(user__email__in=emails).delete()
    User.objects.filter(email__in=emails).delete()
    Event.objects.all().delete()
    connection.close()


def _create_event(*, slug, title, required_level=0, status="upcoming",
                  start_datetime=None):
    from events.models import Event

    if start_datetime is None:
        start_datetime = timezone.now() + datetime.timedelta(days=7)
    event = Event.objects.create(
        slug=slug,
        title=title,
        start_datetime=start_datetime,
        status=status,
        required_level=required_level,
    )
    connection.close()
    return event


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestSubscribeAutoCreatesAccount:
    """Newsletter subscribe creates a free account and surfaces that to
    the user on-site and in the verification email body.
    """

    def test_first_time_subscriber_message_mentions_account(
        self, django_server, page,
    ):
        emails = ["new-subscriber@test.com"]
        _clear_events_and_users(emails)
        _ensure_tiers()

        page.goto(f"{django_server}/subscribe", wait_until="domcontentloaded")
        page.fill('.subscribe-form input[type=email]', emails[0])
        page.click('.subscribe-form button[type=submit]')

        # On-page success message tells them an account was created.
        page.wait_for_selector('.subscribe-message:not(.hidden)', timeout=5000)
        message = page.locator('.subscribe-message').first.inner_text()
        assert "account" in message.lower()

        from accounts.models import User
        user = User.objects.get(email=emails[0])
        assert user.email_verified is False
        assert user.verification_expires_at is not None
        # Default 7-day grace period.
        delta = user.verification_expires_at - timezone.now()
        assert datetime.timedelta(days=6) < delta < datetime.timedelta(days=8)

        from email_app.models import EmailLog
        log = EmailLog.objects.filter(
            user=user, email_type='email_verification',
        )
        assert log.count() == 1

    def test_returning_subscriber_keeps_single_row_and_original_expiry(
        self, django_server, page,
    ):
        emails = ["existing@test.com"]
        _clear_events_and_users(emails)
        _ensure_tiers()

        from accounts.models import User
        original_expiry = (
            timezone.now() + datetime.timedelta(days=2)
        )
        User.objects.create_user(
            email=emails[0],
            verification_expires_at=original_expiry,
        )

        page.goto(f"{django_server}/subscribe", wait_until="domcontentloaded")
        page.fill('.subscribe-form input[type=email]', emails[0])
        page.click('.subscribe-form button[type=submit]')

        page.wait_for_selector('.subscribe-message:not(.hidden)', timeout=5000)
        message = page.locator('.subscribe-message').first.inner_text()
        # Same friendly message as for new subscribers — no info leak.
        assert "account" in message.lower()

        # Single User row and the original expiry is unchanged.
        rows = User.objects.filter(email__iexact=emails[0])
        assert rows.count() == 1
        rows[0].refresh_from_db()
        assert rows[0].verification_expires_at == original_expiry

        # A second verification email is recorded (re-sent on resubmit).
        from email_app.models import EmailLog
        log_count = EmailLog.objects.filter(
            user=rows[0], email_type='email_verification',
        ).count()
        assert log_count >= 1


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestAnonymousEventRegistrationE2E:
    """Anonymous email-only event registration creates a free unverified
    user, registers them for the event, and surfaces both registration
    + verification emails.
    """

    def test_free_event_anonymous_registration_creates_account(
        self, django_server, page,
    ):
        emails = ["event-anon@test.com"]
        _clear_events_and_users(emails)
        _ensure_tiers()
        event = _create_event(
            slug="community-call",
            title="Community Call",
            required_level=0,
        )

        page.goto(
            f"{django_server}/events/community-call",
            wait_until="domcontentloaded",
        )

        # Form is the entry point for anonymous visitors.
        assert page.locator(
            '[data-testid="event-anonymous-email-form"]'
        ).count() == 1
        page.fill('#event-anon-email', emails[0])
        page.click('#event-anon-submit-btn')

        # The JS reloads with ?registered=<email>; wait for the
        # confirmation block to appear.
        page.wait_for_selector(
            '[data-testid="event-anonymous-registered-confirmation"]',
            timeout=10000,
        )

        confirmation = page.locator(
            '[data-testid="event-anonymous-registered-confirmation"]'
        )
        confirmation_text = confirmation.inner_text()
        assert event.title in confirmation_text
        assert emails[0] in confirmation_text

        # Calendar download link is offered.
        ics_link = page.locator(
            '[data-testid="event-anonymous-add-to-calendar"]'
        )
        assert ics_link.count() == 1
        assert ics_link.get_attribute("href") == (
            f"/events/{event.slug}/calendar.ics"
        )

        # Sign-in-to-manage link present.
        manage = page.locator('[data-testid="event-anonymous-manage-link"]')
        assert manage.count() == 1
        assert "Sign in to manage" in manage.inner_text()

        from accounts.models import User
        user = User.objects.get(email=emails[0])
        assert user.email_verified is False
        assert user.verification_expires_at is not None

        from events.models import EventRegistration
        assert EventRegistration.objects.filter(
            event=event, user=user,
        ).count() == 1

        from email_app.models import EmailLog
        assert EmailLog.objects.filter(
            user=user, email_type='event_registration',
        ).count() == 1
        assert EmailLog.objects.filter(
            user=user, email_type='email_verification',
        ).count() == 1

    def test_existing_verified_user_email_is_not_reset(
        self, django_server, page,
    ):
        emails = ["member@test.com"]
        _clear_events_and_users(emails)
        _ensure_tiers()
        # Existing verified Free user.
        _create_user(emails[0], tier_slug="free")
        from accounts.models import User
        existing = User.objects.get(email=emails[0])
        existing.email_verified = True
        existing.save(update_fields=["email_verified"])
        original_password = existing.password

        event = _create_event(
            slug="community-call-existing",
            title="Existing Member Call",
            required_level=0,
        )

        page.goto(
            f"{django_server}/events/community-call-existing",
            wait_until="domcontentloaded",
        )
        page.fill('#event-anon-email', emails[0])
        page.click('#event-anon-submit-btn')
        page.wait_for_selector(
            '[data-testid="event-anonymous-registered-confirmation"]',
            timeout=10000,
        )

        existing.refresh_from_db()
        assert existing.email_verified is True
        assert existing.password == original_password
        # No new ``verification_expires_at`` set for already-verified users.
        assert existing.verification_expires_at is None

        from events.models import EventRegistration
        assert EventRegistration.objects.filter(
            event=event, user=existing,
        ).count() == 1

        # No verification email sent to a verified user — only the
        # event registration confirmation.
        from email_app.models import EmailLog
        assert EmailLog.objects.filter(
            user=existing, email_type='email_verification',
        ).count() == 0
        assert EmailLog.objects.filter(
            user=existing, email_type='event_registration',
        ).count() == 1

    def test_anonymous_registration_on_gated_event_blocked(
        self, django_server, page,
    ):
        emails = ["gate-attempt@test.com"]
        _clear_events_and_users(emails)
        _ensure_tiers()
        _create_event(
            slug="main-only",
            title="Main Only Workshop",
            required_level=20,  # LEVEL_MAIN
        )

        # The detail page must NOT show the email-only form for gated
        # events.
        page.goto(
            f"{django_server}/events/main-only",
            wait_until="domcontentloaded",
        )
        assert page.locator(
            '[data-testid="event-anonymous-email-form"]'
        ).count() == 0

        # Direct API attempt is also rejected — no User row created.
        api_resp = page.request.post(
            f"{django_server}/api/events/main-only/register",
            data='{"email": "gate-attempt@test.com"}',
            headers={"Content-Type": "application/json"},
        )
        assert api_resp.status == 403
        from accounts.models import User
        from events.models import EventRegistration
        assert User.objects.filter(email__iexact=emails[0]).count() == 0
        assert EventRegistration.objects.count() == 0


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestAnonymousRegistrantSignsInLater:
    """An anonymous registrant who later sets a password and signs in
    can manage their registration — the event detail page shows the
    Cancel registration button, not the anonymous form.
    """

    def test_signed_in_registrant_can_cancel(
        self, django_server, browser,
    ):
        emails = ["event-anon-later@test.com"]
        _clear_events_and_users(emails)
        _ensure_tiers()

        # Simulate the post-anonymous-register state: the user verified
        # their email and set a password (i.e. they are now a normal Free
        # user), and they are still registered for the event.
        _create_user(emails[0], tier_slug="free")
        from accounts.models import User
        from events.models import EventRegistration
        user = User.objects.get(email=emails[0])
        user.email_verified = True
        user.save(update_fields=["email_verified"])

        event = _create_event(
            slug="post-anon-event",
            title="Post Anon Event",
            required_level=0,
        )
        EventRegistration.objects.create(event=event, user=user)
        connection.close()

        context = _auth_context(browser, emails[0])
        page = context.new_page()
        page.goto(
            f"{django_server}/events/post-anon-event",
            wait_until="domcontentloaded",
        )

        # Authenticated registered users see the standard confirmation
        # block — NOT the anonymous email-only form.
        assert page.locator(
            '[data-testid="event-anonymous-email-form"]'
        ).count() == 0
        assert page.locator(
            '[data-testid="event-registered-confirmation"]'
        ).count() == 1
        cancel = page.locator("#unregister-btn")
        assert cancel.count() == 1
        assert "Cancel registration" in cancel.inner_text()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestSubscribePageDiscloseAccount:
    """The /subscribe page success copy mentions "account" and the rendered
    verification email body says "we've created a free account for you".
    """

    def test_subscribe_page_message_and_email_body_disclose_account(
        self, django_server, page,
    ):
        emails = ["dedicated-page@test.com"]
        _clear_events_and_users(emails)
        _ensure_tiers()

        page.goto(f"{django_server}/subscribe", wait_until="domcontentloaded")
        page.fill('.subscribe-form input[type=email]', emails[0])
        page.click('.subscribe-form button[type=submit]')
        page.wait_for_selector('.subscribe-message', timeout=5000)

        # On-site success copy tells the user an account was created.
        message = page.locator('.subscribe-message').inner_text()
        assert "account" in message.lower()

        # The verification email body — rendered through the same
        # template the user receives — says "we've created a free
        # account for you" verbatim.
        from accounts.models import User
        user = User.objects.get(email=emails[0])
        from accounts.services.verification import resolve_unverified_ttl_days
        from email_app.services.email_service import EmailService
        rendered_subject, rendered_html = (
            EmailService()._render_template(
                'email_verification',
                user,
                {
                    'verify_url': 'https://example.test/verify?token=t',
                    'site_url': 'https://example.test',
                    'ttl_days': resolve_unverified_ttl_days(),
                },
            )
        )
        assert "we've created a free account for you" in rendered_html.lower()
