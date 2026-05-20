"""Playwright E2E for issue #668: attendee-count social-proof chip.

Covers the headline scenario from the spec:

- An upcoming event with five registered users renders
  ``5 people are going`` on its public detail page, with the chip
  visible in the initial HTML response (server-rendered, no JS).

Usage:
    uv run pytest playwright_tests/test_event_attendee_count_668.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _clear_events():
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_event_with_registrations(slug, title, count):
    """Create an upcoming event and register ``count`` distinct users."""
    from events.models import Event, EventRegistration

    event = Event.objects.create(
        slug=slug,
        title=title,
        description='An event with many sign-ups.',
        start_datetime=timezone.now() + datetime.timedelta(days=7),
        status='upcoming',
    )
    for i in range(count):
        user = _create_user(
            f'{slug}-attendee-{i}@test.com', tier_slug='free',
        )
        EventRegistration.objects.create(event=event, user=user)
    connection.close()
    return event


@pytest.mark.django_db(transaction=True)
class TestAttendeeCountChip:
    """Issue #668: chip renders correct copy and is server-rendered."""

    @pytest.mark.core
    def test_upcoming_event_with_five_registrations_shows_plural_copy(
        self, django_server, page,
    ):
        _clear_events()
        _ensure_tiers()
        event = _create_event_with_registrations(
            'popular-ai-meetup', 'Popular AI Meetup', count=5,
        )

        # Issue #673: canonical event URL is ``/events/<id>/<slug>``.
        response = page.goto(
            f'{django_server}{event.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        assert response.status == 200

        chip = page.locator('[data-testid="event-attendee-count"]')
        assert chip.count() == 1
        text = chip.inner_text()
        # Exact spec copy: "5 people are going".
        assert '5 people are going' in text
        # And no leak of the singular or "attended" copy.
        assert 'person is going' not in text
        assert 'attended' not in text
