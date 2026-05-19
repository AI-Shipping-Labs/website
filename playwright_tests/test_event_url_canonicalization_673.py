"""End-to-end coverage for the ``/events/<id>/<slug>`` URL switch (issue #673).

Covers:
- Canonical id+slug URL renders the event page.
- Visiting ``/events/<id>`` (no slug) 301s to canonical.
- Visiting ``/events/<id>/wrong-slug`` 301s to canonical.
- Old slug-only ``/events/<slug>`` URLs return 404 (legacy fallback was
  intentionally not added; one-off active events go through a
  ``Redirect`` row authored in Studio).
- After an admin renames an event's slug in Studio, the series page
  emits a link with the NEW slug — proving the canonical URL helper is
  the single source of truth.

Usage:
    uv run pytest playwright_tests/test_event_url_canonicalization_673.py -v
"""

import datetime
import os

import pytest
from django.db import connection
from django.utils import timezone

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

ADMIN_PASSWORD = "adminpass"


def _clear_events():
    from events.models import Event, EventSeries

    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _ensure_staff(email="staff@test.com"):
    from accounts.models import User

    user = User.objects.filter(email=email).first()
    if user is None:
        user = User.objects.create_user(
            email=email, password=ADMIN_PASSWORD, email_verified=True,
        )
    user.is_staff = True
    user.is_superuser = True
    user.email_verified = True
    user.save()
    connection.close()
    return user


def _create_event(slug, title="Test Event"):
    from events.models import Event

    event = Event.objects.create(
        title=title,
        slug=slug,
        description="An event for canonical-URL testing.",
        start_datetime=timezone.now() + datetime.timedelta(days=7),
        status="upcoming",
    )
    connection.close()
    return event


def _login_admin_via_browser(page, base_url, email):
    page.goto(f"{base_url}/admin/login/", wait_until="domcontentloaded")
    page.fill("#id_username", email)
    page.fill("#id_password", ADMIN_PASSWORD)
    page.click('input[type="submit"]')
    page.wait_for_load_state("domcontentloaded")


@pytest.mark.django_db(transaction=True)
class TestCanonicalUrlReturns200:
    def test_canonical_id_slug_url_renders_event_page(
        self, django_server, page,
    ):
        _clear_events()
        event = _create_event(slug="my-live-qa", title="My Live Q and A")

        response = page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        assert response.status == 200
        body = page.content()
        assert "My Live Q and A" in body


@pytest.mark.django_db(transaction=True)
class TestIdOnlyRedirectsToCanonical:
    def test_id_only_url_redirects_to_canonical(self, django_server, page):
        _clear_events()
        event = _create_event(slug="my-live-qa", title="My Live Q and A")

        response = page.goto(
            f"{django_server}/events/{event.id}",
            wait_until="domcontentloaded",
        )
        # Final landing URL (after the 301) is the canonical id+slug.
        assert response.status == 200
        assert page.url.endswith(event.get_absolute_url())
        body = page.content()
        assert "My Live Q and A" in body


@pytest.mark.django_db(transaction=True)
class TestWrongSlugRedirectsToCanonical:
    def test_wrong_slug_url_redirects_to_canonical(self, django_server, page):
        _clear_events()
        event = _create_event(slug="my-live-qa", title="My Live Q and A")

        response = page.goto(
            f"{django_server}/events/{event.id}/wrong-slug-from-an-old-tweet",
            wait_until="domcontentloaded",
        )
        # Final landing URL is the canonical form with the correct slug.
        assert response.status == 200
        assert page.url.endswith(event.get_absolute_url())


@pytest.mark.django_db(transaction=True)
class TestLegacySlugOnlyReturns404:
    def test_legacy_slug_only_url_returns_404(self, django_server, page):
        """Issue #673: ``/events/<slug>`` (slug-only) 404s.

        No silent redirect to the homepage and no fallback view — the
        one currently-active event gets a manual ``Redirect`` row in
        Studio. New external links should all use the canonical
        ``/events/<id>/<slug>`` shape.
        """
        _clear_events()
        _create_event(slug="my-live-qa", title="My Live Q and A")

        response = page.goto(
            f"{django_server}/events/my-live-qa",
            wait_until="domcontentloaded",
        )
        assert response.status == 404


@pytest.mark.django_db(transaction=True)
class TestRenameEmitsNewSlug:
    """After renaming an event in Studio, the series page links to the
    NEW slug (proves ``Event.get_absolute_url`` is the single source of
    truth — no hand-built slug strings linger).
    """

    def test_series_listing_picks_up_renamed_slug(
        self, django_server, browser,
    ):
        from events.models import Event, EventSeries

        _clear_events()
        _ensure_staff(email="staff@test.com")

        series = EventSeries.objects.create(
            name="Q and A Series",
            slug="q-and-a-series",
            start_time=datetime.time(18, 0),
        )
        event = Event.objects.create(
            title="Q and A April",
            slug="q-and-a-april",
            description="Monthly Q and A.",
            start_datetime=timezone.now() + datetime.timedelta(days=7),
            status="upcoming",
            event_series=series,
            series_position=1,
            origin="studio",
        )
        connection.close()

        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
        )
        admin = context.new_page()
        _login_admin_via_browser(admin, django_server, "staff@test.com")

        # Rename the event's slug via the Studio edit form.
        admin.goto(
            f"{django_server}/studio/events/{event.id}/edit",
            wait_until="domcontentloaded",
        )
        slug_input = admin.locator('input[name="slug"]')
        slug_input.fill("q-and-a-may")
        admin.locator('button[type="submit"]').first.click()
        admin.wait_for_load_state("domcontentloaded")

        # Confirm the rename actually landed in the DB.
        Event.objects.filter(pk=event.pk).update()  # noop refresh hint
        renamed = Event.objects.get(pk=event.pk)
        assert renamed.slug == "q-and-a-may", (
            f"Studio rename did not persist; slug={renamed.slug!r}"
        )
        connection.close()

        # As an anonymous visitor in a fresh context, load the series
        # page and confirm the link points at the NEW slug.
        public_ctx = browser.new_context(
            viewport={"width": 1280, "height": 720},
        )
        public = public_ctx.new_page()
        public.goto(
            f"{django_server}/events/groups/q-and-a-series",
            wait_until="domcontentloaded",
        )
        link = public.locator('[data-testid="series-event-link"]').first
        link.wait_for()
        href = link.get_attribute("href")
        # Issue #673: canonical URL is ``/events/<id>/q-and-a-may``.
        assert href == renamed.get_absolute_url(), (
            f"Series page should emit canonical id+slug URL with the "
            f"new slug; got {href!r}"
        )
        assert "q-and-a-may" in href
        assert "q-and-a-april" not in href

        context.close()
        public_ctx.close()
