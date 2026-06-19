"""
Playwright E2E tests for the workshop email channel (issue #659,
follow-up to #655).

Sibling to ``test_studio_workshop_notify.py``: that file owns the bell
channel from #647; this file owns the email channel from #655. The five
scenarios below honour the original spec promise in #655 that the
SWE shipped as 22 Django tests at the time (citing the then-current
single-fixed-port-8765 contention, since resolved in #885) rather than
as browser-driven Playwright coverage.

Scenarios
---------

1. Operator sees both counts surface in the Studio banner after a notify
   click. The JS that writes ``Notified N subscribers and emailed M``
   into ``#notify-status`` runs end-to-end and the substrings are
   observable without a reload.
2. Member toggles workshop emails off on ``/account/`` and the off state
   survives ``page.reload()`` (the JS POST to
   ``/account/api/email-preferences`` actually persisted).
3. Opted-out member gets the bell notification (count >= 1) but zero
   ``EmailLog`` rows after a notify.
4. Subscribed member ends up with exactly one ``EmailLog`` row whose
   ``ses_message_id`` is the ``ses-disabled-noop`` literal returned by
   ``EmailService._send_ses`` when ``SES_ENABLED=False`` (the conftest
   forces this for Playwright runs).
5. Globally unsubscribed (``user.unsubscribed=True``) user gets zero
   ``EmailLog`` rows after a notify.

The conftest forces ``SES_ENABLED=False`` (see
``playwright_tests/conftest.py`` lines 53-61); no real SES traffic is
sent and no ``send_mail`` patches are needed.
"""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


def _clear_state():
    """Reset workshops, notifications, AND workshop-announcement EmailLog
    rows between scenarios so per-test ``EmailLog.objects.count()``
    assertions are deterministic regardless of run order.
    """
    from content.models import Workshop, WorkshopPage
    from email_app.models import EmailLog
    from events.models import Event
    from notifications.models import EventReminderLog, Notification

    EmailLog.objects.filter(email_type='workshop_announcement').delete()
    Notification.objects.all().delete()
    EventReminderLog.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.filter(kind='workshop').delete()
    connection.close()


def _create_workshop(
    slug='build-a-rag-app',
    title='Build a RAG App',
    status='published',
    landing=10,
    pages=10,
    recording=20,
    date=None,
):
    """Create a Workshop row for the scenario under test.

    Defaults to ``landing=10`` so the admin staff user (created without
    a tier) is excluded from the audience -- that keeps the
    ``Notified 3`` and ``emailed 2`` counts in Scenario 1 exact instead
    of being inflated by the operator who triggered the click.
    """
    from content.models import Workshop

    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        date=date or datetime.date(2026, 4, 21),
        description='Hands-on intro to RAG.',
        tags=['agents'],
        status=status,
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
    )
    connection.close()
    return workshop


def _direct_notify_post(context, django_server, workshop_id):
    """POST the workshop notify endpoint directly using the auth context.

    Used by scenarios 3-5 where we only need the side-effects
    (Notification + EmailLog rows) and don't need to assert on the
    Studio JS banner. Faster than driving the UI: ~200ms vs ~1s.
    """
    # Visit a public page first so the browser context picks up a real
    # csrftoken cookie. Django's CSRF middleware requires the cookie
    # and the X-CSRFToken header to match.
    page = context.new_page()
    page.goto(f'{django_server}/', wait_until='domcontentloaded')
    cookies = context.cookies(django_server)
    csrf_value = next(
        (c['value'] for c in cookies if c['name'] == 'csrftoken'),
        'e2e-test-csrf-token-value',
    )

    response = context.request.post(
        f'{django_server}/studio/workshops/{workshop_id}/notify',
        headers={'X-CSRFToken': csrf_value, 'Referer': django_server},
    )
    page.close()
    return response


# ---------------------------------------------------------------
# Scenario 1: Operator sees both counts in the Studio banner
# ---------------------------------------------------------------

class TestStudioNotifyBanner:
    @pytest.mark.core
    def test_staff_sees_notified_and_emailed_in_studio_banner(
        self, django_server, browser,
    ):
        from email_app.models import EmailLog

        _ensure_tiers()
        _clear_state()
        _create_staff_user('admin@test.com')
        workshop = _create_workshop(
            slug='build-a-rag-app', title='Build a RAG App',
            status='published', landing=10, pages=10, recording=20,
        )
        # Three eligible verified users at basic tier (clears landing=10).
        # One opted out of workshop_emails; two opted in.
        _create_user(
            'opted-out@test.com',
            tier_slug='basic',
            email_verified=True,
        )
        _create_user(
            'opted-in-1@test.com',
            tier_slug='basic',
            email_verified=True,
        )
        _create_user(
            'opted-in-2@test.com',
            tier_slug='basic',
            email_verified=True,
        )

        # Set workshop_emails=False for the opt-out user. The admin has
        # no tier so they're excluded from the audience -- keeps the
        # banner counts at exactly 3 / 2.
        from accounts.models import User
        opted_out = User.objects.get(email='opted-out@test.com')
        opted_out.email_preferences = {'workshop_emails': False}
        opted_out.save(update_fields=['email_preferences'])
        connection.close()

        staff_ctx = _auth_context(browser, 'admin@test.com')
        staff_page = staff_ctx.new_page()

        staff_page.goto(
            f'{django_server}/studio/workshops/{workshop.pk}/edit',
            wait_until='domcontentloaded',
        )

        notify_btn = staff_page.locator('#notify-subscribers-btn')
        assert notify_btn.count() == 1
        notify_btn.click()

        status = staff_page.locator('#notify-status')
        status.wait_for(state='visible', timeout=10000)
        # Both substrings must surface in the same banner line, written
        # by the JS fetch -- no page reload involved.
        staff_page.wait_for_function(
            """() => {
                var el = document.getElementById('notify-status');
                return el
                    && el.textContent.includes('Notified 3')
                    && el.textContent.includes('emailed 2');
            }""",
            timeout=10000,
        )
        status_text = status.inner_text()
        assert 'Notified 3' in status_text
        assert 'emailed 2' in status_text

        # Backend assertion: 2 EmailLog rows were created (one per
        # opted-in user), the opt-out user has none.
        connection.close()
        assert EmailLog.objects.filter(
            email_type='workshop_announcement',
        ).count() == 2

        staff_ctx.close()


# ---------------------------------------------------------------
# Scenario 2: Member toggles workshop emails off; off state persists
# ---------------------------------------------------------------

class TestAccountWorkshopEmailToggle:
    @pytest.mark.core
    def test_member_toggles_workshop_emails_on_account(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_state()
        # Verified main-tier user with no workshop_emails override
        # (default: opted in).
        _create_user(
            'main@test.com',
            tier_slug='main',
            email_verified=True,
        )

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(f'{django_server}/account/', wait_until='domcontentloaded')

        status = page.locator('#workshop-emails-status')
        status.wait_for(state='visible', timeout=10000)
        assert (
            status.inner_text().strip()
            == 'You will receive workshop announcement emails.'
        )

        # Click the toggle and wait for the JS to swap the status text.
        page.locator('#workshop-emails-toggle').click()
        page.wait_for_function(
            """() => {
                var el = document.getElementById('workshop-emails-status');
                return el && el.textContent.trim()
                    === 'You will not receive workshop announcement emails.';
            }""",
            timeout=10000,
        )
        assert (
            status.inner_text().strip()
            == 'You will not receive workshop announcement emails.'
        )

        # Reload the page: the off state must come from the DB, not
        # from in-memory JS state.
        page.reload(wait_until='domcontentloaded')
        status = page.locator('#workshop-emails-status')
        status.wait_for(state='visible', timeout=10000)
        assert (
            status.inner_text().strip()
            == 'You will not receive workshop announcement emails.'
        )

        # Belt-and-suspenders: confirm the JSONField actually flipped.
        from accounts.models import User
        connection.close()
        user = User.objects.get(email='main@test.com')
        assert user.email_preferences.get('workshop_emails') is False

        ctx.close()


# ---------------------------------------------------------------
# Scenario 3: Opted-out member gets bell but no email
# ---------------------------------------------------------------

class TestWorkshopOptOut:
    @pytest.mark.core
    def test_opted_out_member_gets_bell_but_not_email(
        self, django_server, browser,
    ):
        from accounts.models import User
        from email_app.models import EmailLog

        _ensure_tiers()
        _clear_state()
        _create_staff_user('admin@test.com')
        workshop = _create_workshop(
            slug='opt-out-rag', title='Opt-out RAG Workshop',
            status='published', landing=10, pages=10, recording=20,
        )
        _create_user(
            'opt-out@test.com',
            tier_slug='basic',
            email_verified=True,
        )
        opt_out_user = User.objects.get(email='opt-out@test.com')
        opt_out_user.email_preferences = {'workshop_emails': False}
        opt_out_user.save(update_fields=['email_preferences'])
        connection.close()

        # Trigger the notify via a direct POST as the admin; the UI
        # banner is covered by Scenario 1.
        staff_ctx = _auth_context(browser, 'admin@test.com')
        response = _direct_notify_post(
            staff_ctx, django_server, workshop.pk,
        )
        assert response.status == 200, (
            f'expected 200 from notify endpoint, got {response.status}'
        )
        staff_ctx.close()

        # The opted-out user still gets the bell notification but
        # zero EmailLog rows.
        opt_out_user = User.objects.get(email='opt-out@test.com')

        connection.close()
        assert EmailLog.objects.filter(
            user=opt_out_user,
            email_type='workshop_announcement',
        ).count() == 0

        # Visit the home page as the opted-out user and confirm the
        # bell badge shows at least one unread.
        user_ctx = _auth_context(browser, 'opt-out@test.com')
        user_page = user_ctx.new_page()
        user_page.goto(f'{django_server}/', wait_until='domcontentloaded')

        badge = user_page.locator('#notification-badge')
        badge.wait_for(state='visible', timeout=10000)
        # ``int(badge.inner_text()) >= 1`` is the AC contract.
        assert int(badge.inner_text()) >= 1

        # Open the dropdown and verify the workshop notification points
        # at the right landing URL.
        user_page.locator('#notification-bell-btn').click()
        dropdown = user_page.locator('#notification-dropdown')
        dropdown.wait_for(state='visible', timeout=5000)

        user_page.wait_for_function(
            """() => {
                var list = document.getElementById('notification-list');
                return list && !list.textContent.includes('Loading');
            }""",
            timeout=10000,
        )

        # Issue #750: workshop notification href is the canonical
        # /workshops/<YYYY-MM-DD>-<slug>; the workshop factory pins the
        # date to 2026-04-21.
        link = user_page.locator(
            '#notification-list a[href="/workshops/opt-out-rag"]',
        )
        assert link.count() >= 1
        assert 'New workshop:' in dropdown.inner_text()

        user_ctx.close()


# ---------------------------------------------------------------
# Scenario 4: Subscribed member sees the email row land with the
# noop SES message id
# ---------------------------------------------------------------

class TestWorkshopSubscribedEmail:
    @pytest.mark.core
    def test_subscribed_member_sees_email_arrive(
        self, django_server, browser,
    ):
        from accounts.models import User
        from email_app.models import EmailLog

        _ensure_tiers()
        _clear_state()
        _create_staff_user('admin@test.com')
        workshop = _create_workshop(
            slug='subscribed-rag', title='Subscribed RAG Workshop',
            status='published', landing=10, pages=10, recording=20,
        )
        _create_user(
            'subscriber@test.com',
            tier_slug='basic',
            email_verified=True,
            unsubscribed=False,
        )

        staff_ctx = _auth_context(browser, 'admin@test.com')
        response = _direct_notify_post(
            staff_ctx, django_server, workshop.pk,
        )
        assert response.status == 200, (
            f'expected 200 from notify endpoint, got {response.status}'
        )
        staff_ctx.close()

        subscriber = User.objects.get(email='subscriber@test.com')
        connection.close()

        logs = EmailLog.objects.filter(
            user=subscriber,
            email_type='workshop_announcement',
        )
        assert logs.count() == 1, (
            f'expected exactly one EmailLog row, got {logs.count()}'
        )
        # The conftest forces SES_ENABLED=False, so _send_ses returns
        # the noop literal and that's what lands in the DB.
        assert logs.first().ses_message_id == 'ses-disabled-noop'


# ---------------------------------------------------------------
# Scenario 5: Globally unsubscribed user gets zero EmailLog rows
# ---------------------------------------------------------------

class TestWorkshopGloballyUnsubscribed:
    @pytest.mark.core
    def test_globally_unsubscribed_user_receives_no_workshop_email(
        self, django_server, browser,
    ):
        from accounts.models import User
        from email_app.models import EmailLog

        _ensure_tiers()
        _clear_state()
        _create_staff_user('admin@test.com')
        workshop = _create_workshop(
            slug='unsub-rag', title='Unsubscribed RAG Workshop',
            status='published', landing=10, pages=10, recording=20,
        )
        _create_user(
            'unsub@test.com',
            tier_slug='basic',
            email_verified=True,
            unsubscribed=True,
        )

        staff_ctx = _auth_context(browser, 'admin@test.com')
        response = _direct_notify_post(
            staff_ctx, django_server, workshop.pk,
        )
        assert response.status == 200, (
            f'expected 200 from notify endpoint, got {response.status}'
        )
        staff_ctx.close()

        unsub_user = User.objects.get(email='unsub@test.com')
        connection.close()

        assert EmailLog.objects.filter(
            user=unsub_user,
            email_type='workshop_announcement',
        ).count() == 0
