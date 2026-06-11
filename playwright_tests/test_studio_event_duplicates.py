"""End-to-end coverage for the Studio duplicate-event merge UI (issue #881).

Exercises the irreversible preview -> confirm flow from a real browser: the
candidate pair listing, the preview (a guaranteed no-op), the confirm that folds
the duplicate into the canonical event and retires the duplicate, and the
non-staff 403 gate.

Usage:
    uv run pytest playwright_tests/test_studio_event_duplicates.py -v
"""

import datetime as dt
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

pytestmark = pytest.mark.local_only

MAY19 = dt.datetime(2026, 5, 19, 0, 0, tzinfo=dt.timezone.utc)
MAY19_STUDIO = dt.datetime(2026, 5, 19, 15, 0, tzinfo=dt.timezone.utc)


def _make_pair():
    """Create the May-19-style duplicate pair and return ``(canonical_pk, dup_pk)``."""
    from events.models import Event

    Event.objects.filter(slug__in=["may19-studio", "may19-github"]).delete()
    canonical = Event.objects.create(
        slug="may19-studio", title="May 19 Workshop",
        start_datetime=MAY19_STUDIO, origin="studio", source_repo="",
        status="upcoming", published=True)
    duplicate = Event.objects.create(
        slug="may19-github", title="May 19 Workshop",
        start_datetime=MAY19, origin="github", source_repo="workshops-content",
        kind="workshop", status="completed", published=True)
    ids = (canonical.pk, duplicate.pk)
    connection.close()
    return ids


def _register(email, event_pk):
    from accounts.models import User
    from events.models import EventRegistration

    EventRegistration.objects.get_or_create(
        event_id=event_pk, user=User.objects.get(email=email))
    connection.close()


def _duplicate_state(pk):
    """Return ``(status, published, exists)`` for the duplicate."""
    from events.models import Event

    event = Event.objects.filter(pk=pk).first()
    state = (
        (event.status, event.published, True) if event else (None, None, False)
    )
    connection.close()
    return state


def _canonical_registration_count(pk):
    from events.models import EventRegistration

    n = EventRegistration.objects.filter(event_id=pk).count()
    connection.close()
    return n


@pytest.mark.django_db(transaction=True)
class TestPreviewThenConfirm:
    """Staff previews a duplicate merge, reviews the plan, and commits it."""

    @pytest.mark.core
    def test_full_preview_confirm_flow(self, django_server, browser):
        _ensure_tiers()
        staff_email = "dup-admin@test.com"
        _create_staff_user(staff_email)
        _create_user("member@test.com", tier_slug="free")
        canonical_pk, duplicate_pk = _make_pair()
        _register("member@test.com", duplicate_pk)

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/events/duplicates/",
            wait_until="domcontentloaded",
        )

        # The candidate pair is listed.
        assert page.locator('[data-testid="event-duplicate-row"]').count() >= 1

        # Preview the merge.
        page.locator(
            '[data-testid="event-duplicate-preview-submit"]'
        ).first.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator('[data-testid="event-merge-preview"]').count() == 1

        # Preview is a no-op: the duplicate is still completed + published.
        assert _duplicate_state(duplicate_pk) == ("completed", True, True)
        assert _canonical_registration_count(canonical_pk) == 0

        # Confirm.
        page.once("dialog", lambda d: d.accept())
        page.locator('[data-testid="event-merge-confirm-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Success headline shown.
        headline = page.locator('[data-testid="event-merge-result-headline"]')
        assert headline.count() == 1

        # Registration moved; duplicate retired (cancelled + unpublished, not
        # deleted).
        assert _canonical_registration_count(canonical_pk) == 1
        status, published, exists = _duplicate_state(duplicate_pk)
        assert exists is True
        assert status == "cancelled"
        assert published is False

        context.close()


@pytest.mark.django_db(transaction=True)
class TestNonStaffBlocked:
    """A non-staff member cannot reach the duplicates tool."""

    def test_member_gets_403(self, django_server, browser):
        _ensure_tiers()
        staff_email = "dup-gate-admin@test.com"
        _create_staff_user(staff_email)
        _create_user("plain@test.com", tier_slug="free")

        context = _auth_context(browser, "plain@test.com")
        response = context.request.get(
            f"{django_server}/studio/events/duplicates/", max_redirects=0
        )
        assert response.status == 403
        context.close()
