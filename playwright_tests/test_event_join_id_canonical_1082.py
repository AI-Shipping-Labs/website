"""
Playwright E2E tests for the id-canonical event join route (issue #1082).

Scenarios:
- Registered member appends /join to the detail URL and reaches the session.
- Member clicks the on-page Join button and it uses the canonical URL.
- Old slug-only join link from a stale email still works.
- Cosmetic slug in the join URL is corrected.
- Anonymous visitor is sent to login and returned to the join URL.
- Unregistered member is guided back to the event page.

Usage:
    uv run pytest playwright_tests/test_event_join_id_canonical_1082.py -v
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
from django.db import connection

# Local-only: seeds the DB and injects session cookies; cannot run against
# the deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _clear_events():
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_event(slug, *, minutes_from_now, zoom_url=""):
    from events.models import Event

    event = Event.objects.create(
        title=f"Event {slug}",
        slug=slug,
        start_datetime=timezone.now()
        + datetime.timedelta(minutes=minutes_from_now),
        status="upcoming",
        zoom_join_url=zoom_url,
        required_level=0,
    )
    connection.close()
    return event


def _register(user, event):
    from events.models import EventRegistration

    EventRegistration.objects.get_or_create(event=event, user=user)
    connection.close()


def _join_target(django_server, token):
    """Local target for join redirects; avoids external network in Playwright."""
    return f"{django_server}/__test_join_target__/{token}"


@pytest.mark.django_db(transaction=True)
class TestEventJoinIdCanonical:
    def test_appending_join_to_detail_url_reaches_session(
        self, django_server, browser,
    ):
        """A registered member appends /join to the canonical detail URL and
        is redirected to Zoom inside the live window (no 404)."""
        _clear_events()
        _ensure_tiers()
        user = _create_user("main@test.com", tier_slug="free")
        event = _create_event(
            "append-join-event",
            minutes_from_now=1,
            zoom_url=_join_target(django_server, "append-canon"),
        )
        _register(user, event)
        canonical_join = event.get_join_url()

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        # Navigate to detail URL then append /join.
        resp = page.goto(
            f"{django_server}{canonical_join}",
            wait_until="domcontentloaded",
        )
        # Followed the redirect to the configured live-session URL; final URL
        # is the raw join link and the request never 404'd.
        assert resp is not None
        assert page.url.startswith(_join_target(django_server, "append-canon"))
        ctx.close()

    def test_on_page_join_button_uses_canonical_url(
        self, django_server, browser,
    ):
        """The on-page Join button href is the canonical id+slug join URL."""
        _clear_events()
        _ensure_tiers()
        user = _create_user("main@test.com", tier_slug="free")
        event = _create_event(
            "button-join-event",
            minutes_from_now=4,
            zoom_url="https://zoom.us/j/button-canon",
        )
        _register(user, event)
        canonical_join = event.get_join_url()

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        join_link = page.locator(f'a[href="{canonical_join}"]')
        assert join_link.count() >= 1
        ctx.close()

    def test_legacy_slug_only_join_link_still_works(
        self, django_server, browser,
    ):
        """The legacy /events/<slug>/join link (stale email) is not broken."""
        _clear_events()
        _ensure_tiers()
        user = _create_user("main@test.com", tier_slug="free")
        event = _create_event(
            "legacy-join-event",
            minutes_from_now=1,
            zoom_url=_join_target(django_server, "legacy-canon"),
        )
        _register(user, event)

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/events/{event.slug}/join",
            wait_until="domcontentloaded",
        )
        assert page.url.startswith(_join_target(django_server, "legacy-canon"))
        ctx.close()

    def test_cosmetic_wrong_slug_is_corrected(
        self, django_server, browser,
    ):
        """A wrong slug on the canonical join URL lands on the right slug."""
        _clear_events()
        _ensure_tiers()
        user = _create_user("main@test.com", tier_slug="free")
        event = _create_event(
            "real-slug-event",
            minutes_from_now=1,
            zoom_url=_join_target(django_server, "cosmetic-canon"),
        )
        _register(user, event)

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/events/{event.pk}/totally-wrong-slug/join",
            wait_until="domcontentloaded",
        )
        # 301 to the canonical join URL, then on to Zoom inside the window.
        assert page.url.startswith(_join_target(django_server, "cosmetic-canon"))
        ctx.close()

    def test_anonymous_visitor_sent_to_login_and_returned(
        self, django_server, browser,
    ):
        """An anonymous visitor on the canonical join URL is sent to login,
        then returned to the join URL after authenticating."""
        _clear_events()
        _ensure_tiers()
        user = _create_user("main@test.com", tier_slug="free")
        event = _create_event(
            "anon-join-event",
            minutes_from_now=1,
            zoom_url="https://zoom.us/j/anon-canon",
        )
        _register(user, event)
        canonical_join = event.get_join_url()

        # Anonymous context (no auth cookies).
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(
            f"{django_server}{canonical_join}",
            wait_until="domcontentloaded",
        )
        # Landed on the login page with next= pointing at the join URL.
        assert "/accounts/login/" in page.url
        assert canonical_join in page.url
        ctx.close()

    def test_unregistered_member_redirected_to_detail(
        self, django_server, browser,
    ):
        """A logged-in member who is NOT registered is sent to the event
        detail page, not a broken page."""
        _clear_events()
        _ensure_tiers()
        _create_user("free@test.com", tier_slug="free")
        event = _create_event(
            "unreg-join-event",
            minutes_from_now=1,
            zoom_url="https://zoom.us/j/unreg-canon",
        )
        canonical_join = event.get_join_url()

        ctx = _auth_context(browser, "free@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}{canonical_join}",
            wait_until="domcontentloaded",
        )
        # Redirected to the canonical detail page.
        assert page.url.rstrip("/").endswith(event.get_absolute_url())
        assert "zoom.us" not in page.url
        ctx.close()
