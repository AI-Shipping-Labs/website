"""
Playwright E2E tests for Events and Calendar (Issue #83).

Tests cover all 12 BDD scenarios from the issue:
- Visitor browses upcoming events and reads event details
- Anonymous visitor wants to register for an event but is directed to sign in
- Eligible member registers for an event and sees confirmation
- Registered member cancels their event registration
- Member tries to register for a full event and learns it is at capacity
- Free member on a gated event sees the upgrade path
- Registered member returns shortly before event start and sees the Zoom join link
- Registered member checks an event that is still far away and Zoom link is hidden
- Visitor views a completed event and finds the recording
- Visitor views a completed event that has no recording yet
- Draft events are not visible to the public
- Visitor spots a cancelled event in the past events section

Usage:
    uv run pytest playwright_tests/test_events_calendar.py -v
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


def _clear_events():
    """Delete all events and registrations to ensure clean state."""
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_event(
    title,
    slug,
    description="",
    start_datetime=None,
    end_datetime=None,
    tz="Europe/Berlin",
    zoom_meeting_id="",
    zoom_join_url="",
    location="",
    tags=None,
    required_level=0,
    max_participants=None,
    status="upcoming",
    recording_url="",
):
    """Create an Event via ORM."""
    from events.models import Event

    if tags is None:
        tags = []
    if start_datetime is None:
        start_datetime = timezone.now() + datetime.timedelta(days=7)

    event = Event(
        title=title,
        slug=slug,
        description=description,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        timezone=tz,
        zoom_meeting_id=zoom_meeting_id,
        zoom_join_url=zoom_join_url,
        location=location,
        tags=tags,
        required_level=required_level,
        max_participants=max_participants,
        status=status,
        recording_url=recording_url,
    )
    event.save()
    connection.close()
    return event


def _register_user_for_event(user, event):
    """Register a user for an event."""
    from events.models import EventRegistration

    reg, created = EventRegistration.objects.get_or_create(
        event=event,
        user=user,
    )
    connection.close()
    return reg


def _create_recording(title, slug, date=None):
    """Create a completed event with a recording via ORM.

    The events/recordings unification merged the legacy Recording model into
    Event. To represent a "past recording" fixture we use status='completed'
    and a past start_datetime so it shows up on /events?filter=past.
    """
    from events.models import Event

    if date is None:
        date = datetime.date.today()

    start_dt = timezone.make_aware(
        datetime.datetime.combine(date, datetime.time(12, 0))
    )

    recording = Event(
        title=title,
        slug=slug,
        start_datetime=start_dt,
        status="completed",
        published=True,
    )
    recording.save()
    connection.close()
    return recording


# ---------------------------------------------------------------
# Scenario 1: Visitor browses upcoming events and reads event
#              details
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1VisitorBrowsesEventsAndReadsDetails:
    """Visitor browses upcoming events and reads event details."""

    @pytest.mark.core
    def test_visitor_sees_upcoming_and_past_events_then_clicks_detail(
        self, django_server
    , page):
        """Given an anonymous visitor. Two events exist: an upcoming event
        and a completed event. The listing shows both in the correct
        sections without event type badges. Clicking the upcoming event
        shows the full detail page."""
        _clear_events()
        _ensure_tiers()

        now = timezone.now()

        _create_event(
            title="AI Prompt Engineering Workshop",
            slug="ai-prompt-engineering-workshop",
            description="Learn prompt engineering for AI models.",
            start_datetime=now + datetime.timedelta(days=7),
            location="Zoom",
            max_participants=20,
            tags=["python", "ai"],
            status="upcoming",
        )

        _create_event(
            title="Intro to LLMs",
            slug="intro-to-llms",
            description="An introduction to large language models.",
            start_datetime=now - datetime.timedelta(days=7),
            status="completed",
        )

        # Step 1: Navigate to /events
        page.goto(
            f"{django_server}/events",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: The page shows two sections -- "Upcoming" and "Past"
        assert "Upcoming" in body
        assert "Past" in body

        # "AI Prompt Engineering Workshop" appears in the Upcoming section
        upcoming_section = page.locator("h2:has-text('Upcoming')").locator("..")
        upcoming_text = upcoming_section.inner_text()
        assert "AI Prompt Engineering Workshop" in upcoming_text

        # Event type badges were removed by #389.
        assert "Live" not in upcoming_text
        assert "Async" not in upcoming_text

        # Location "Zoom" and "20 spots remaining"
        assert "Zoom" in body
        assert "20 spots remaining" in body

        # "Intro to LLMs" appears in the Past section
        past_section = page.locator("h2:has-text('Past')").locator("..")
        past_text = past_section.inner_text()
        assert "Intro to LLMs" in past_text

        # Step 2: Click on "AI Prompt Engineering Workshop"
        page.click(
            'a[href="/events/ai-prompt-engineering-workshop"]'
        )
        page.wait_for_load_state("domcontentloaded")

        # Then: Visitor lands on the detail page
        assert "/events/ai-prompt-engineering-workshop" in page.url
        body = page.content()

        # Title, description, location, timezone, and tags
        assert "AI Prompt Engineering Workshop" in body
        assert "Zoom" in body
        assert "Europe/Berlin" in body
        assert "python" in body
        assert "ai" in body

        # "Back to Events" link is available
        back_link = page.locator('a:has-text("Back to Events")')
        assert back_link.count() >= 1
# ---------------------------------------------------------------
# Scenario 2: Anonymous visitor wants to register for an event
#              but is directed to sign in
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2AnonymousDirectedToSignIn:
    """Anonymous visitor wants to register for an event but is directed
    to sign in."""

    def test_anonymous_sees_email_only_form_on_open_event(
        self, django_server
    , page):
        """Issue #513: anonymous visitors on a free upcoming event see an
        inline email-only registration form. The 'Already have an account?
        Sign in' link below the form preserves the event-detail return URL
        for returning users.
        """
        _clear_events()
        _ensure_tiers()

        _create_event(
            title="Open Workshop",
            slug="open-workshop",
            required_level=0,
            status="upcoming",
        )

        # Step 1: Navigate to /events/open-workshop
        response = page.goto(
            f"{django_server}/events/open-workshop",
            wait_until="domcontentloaded",
        )

        # Then: Event detail page loads (HTTP 200, no redirect)
        assert response.status == 200
        assert "/events/open-workshop" in page.url

        # The email-only registration form is the entry point.
        form = page.locator('[data-testid="event-anonymous-email-form"]')
        assert form.count() == 1
        assert page.locator('#event-anon-email').count() == 1
        assert page.locator('#event-anon-submit-btn').count() == 1

        # Returning users still get a sign-in link with `next=` preserved.
        sign_in_link = page.locator(
            'a[href*="/accounts/login/?next="]'
        )
        assert sign_in_link.count() >= 1
        href = sign_in_link.first.get_attribute("href")
        assert "next" in href
        assert "open-workshop" in href

        # Step 2: Click the "Sign In" link to verify it routes correctly.
        sign_in_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Taken to /accounts/login/ with next parameter
        assert "/accounts/login/" in page.url
# ---------------------------------------------------------------
# Scenario 3: Eligible member registers for an event and sees
#              confirmation
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3EligibleMemberRegisters:
    """Eligible member registers for an event and sees confirmation."""

    @pytest.mark.core
    def test_free_member_registers_for_open_event(
        self, django_server
    , browser):
        """Given a user logged in as free@test.com (Free tier). An upcoming
        open event exists with max_participants=10. The user sees a Register
        button and '10 spots remaining'. After clicking Register, the page
        reloads showing 'You're registered!' and a Cancel Registration button."""
        _clear_events()
        _ensure_tiers()
        _create_user("free@test.com", tier_slug="free")

        _create_event(
            title="Coding Session",
            slug="coding-session",
            required_level=0,
            max_participants=10,
            status="upcoming",
        )

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /events/coding-session
        page.goto(
            f"{django_server}/events/coding-session",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: User sees a "Register" button and "10 spots remaining"
        register_btn = page.locator("#register-btn")
        assert register_btn.count() >= 1
        assert "Register" in register_btn.inner_text()
        assert "10 spots remaining" in body

        # Step 2: Click the "Register" button
        register_btn.click()

        # The JS calls fetch then window.location.reload().
        # Wait for the "You're registered!" text to appear.
        page.wait_for_selector(
            'text="You\'re registered!"',
            timeout=10000,
        )

        body = page.content()

        # Then: Shows "You're registered!" with a green check
        assert "You're registered!" in body

        # The "Register" button is replaced by "Cancel Registration"
        cancel_btn = page.locator("#unregister-btn")
        assert cancel_btn.count() >= 1
        # Issue #484: button copy was lower-cased ("Cancel registration").
        assert "Cancel registration" in cancel_btn.inner_text()

        # Spots count updates (9 remaining or 1/10 spots taken)
        assert "1/10 spots taken" in body
# ---------------------------------------------------------------
# Scenario 4: Registered member cancels their event registration
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4RegisteredMemberCancels:
    """Registered member cancels their event registration."""

    @pytest.mark.core
    def test_registered_member_cancels_registration(
        self, django_server
    , browser):
        """Given a user logged in as free@test.com who is already
        registered for an upcoming event. The detail page shows
        'You're registered!' and a 'Cancel Registration' button.
        Clicking Cancel shows a confirmation dialog, and after
        confirming, the page reloads with the Register button back."""
        _clear_events()
        _ensure_tiers()
        user = _create_user("free@test.com", tier_slug="free")

        event = _create_event(
            title="Cancel Test Event",
            slug="cancel-test",
            required_level=0,
            status="upcoming",
        )
        _register_user_for_event(user, event)

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /events/cancel-test
        page.goto(
            f"{django_server}/events/cancel-test",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Shows "You're registered!" and "Cancel Registration"
        assert "You're registered!" in body
        cancel_btn = page.locator("#unregister-btn")
        assert cancel_btn.count() >= 1
        # Issue #484: button copy was lower-cased ("Cancel registration").
        assert "Cancel registration" in cancel_btn.inner_text()

        # Step 2: Click "Cancel Registration"
        # The JS shows confirm() dialog. Accept it.
        page.on("dialog", lambda dialog: dialog.accept())
        cancel_btn.click()

        # Step 3: After confirmation, page reloads
        # Wait for the Register button to appear
        page.wait_for_selector(
            "#register-btn",
            timeout=10000,
        )

        body = page.content()

        # Then: "You're registered!" is gone and Register button is back
        assert "You're registered!" not in body
        register_btn = page.locator("#register-btn")
        assert register_btn.count() >= 1
        assert "Register" in register_btn.inner_text()
# ---------------------------------------------------------------
# Scenario 5: Member tries to register for a full event and
#              learns it is at capacity
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5FullEventCapacity:
    """Member tries to register for a full event and learns it is
    at capacity."""

    def test_full_event_shows_event_is_full(
        self, django_server
    , browser):
        """Given a user logged in as free@test.com (Free tier). An upcoming
        open event with max_participants=1 and one other user already
        registered. The detail page shows 'Event is full' and 'This event
        has reached its maximum capacity.' with no Register button."""
        _clear_events()
        _ensure_tiers()
        _create_user("free@test.com", tier_slug="free")

        event = _create_event(
            title="Full Workshop",
            slug="full-workshop",
            required_level=0,
            max_participants=1,
            status="upcoming",
        )

        # Fill the event with another user
        other_user = _create_user("other@test.com", tier_slug="free")
        _register_user_for_event(other_user, event)

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /events/full-workshop
        page.goto(
            f"{django_server}/events/full-workshop",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Shows "Event is full" and capacity message
        assert "Event is full" in body
        assert "This event has reached its maximum capacity" in body

        # No Register button
        register_btn = page.locator("#register-btn")
        assert register_btn.count() == 0
# ---------------------------------------------------------------
# Scenario 6: Free member on a gated event sees the upgrade path
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6FreeMemberGatedEventUpgradePath:
    """Free member on a gated event sees the upgrade path."""

    def test_free_member_sees_upgrade_cta_on_premium_event(
        self, django_server
    , browser):
        """Given a user logged in as free@test.com (Free tier, level=0).
        An upcoming event with required_level=30 (Premium). The detail
        page is visible but shows 'Upgrade to Premium to attend' with
        a lock icon and 'View Pricing' link. Clicking it goes to /pricing."""
        _clear_events()
        _ensure_tiers()
        _create_user("free@test.com", tier_slug="free")

        _create_event(
            title="Premium Masterclass",
            slug="premium-masterclass",
            description="An exclusive premium masterclass.",
            required_level=30,
            status="upcoming",
        )

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /events/premium-masterclass
        page.goto(
            f"{django_server}/events/premium-masterclass",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Event detail page is visible (title, description, date)
        assert "Premium Masterclass" in body

        # Shows "Upgrade to Premium to attend"
        assert "Upgrade to Premium to attend" in body

        # "View Pricing" link is present
        pricing_link = page.locator('a:has-text("View Pricing")')
        assert pricing_link.count() >= 1

        # Step 2: Click "View Pricing"
        pricing_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Lands on /pricing
        assert "/pricing" in page.url
# ---------------------------------------------------------------
# Scenario 7: Registered member returns shortly before event
#              start and sees the Zoom join link
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7ZoomLinkVisibleBeforeEvent:
    """Registered member returns shortly before event start and sees
    the Zoom join link."""

    def test_zoom_link_shown_within_15_minutes_of_start(
        self, django_server
    , browser):
        """Given a user logged in as free@test.com who is registered for
        an upcoming Zoom event starting 10 minutes from now. The detail
        page shows 'You're registered!' and a 'Join the event' section
        with the internal join redirect link."""
        _clear_events()
        _ensure_tiers()
        user = _create_user("free@test.com", tier_slug="free")

        now = timezone.now()
        event = _create_event(
            title="Imminent Workshop",
            slug="imminent-workshop",
            zoom_join_url="https://zoom.us/j/123456",
            start_datetime=now + datetime.timedelta(minutes=10),
            required_level=0,
            status="upcoming",
        )
        _register_user_for_event(user, event)

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /events/imminent-workshop
        page.goto(
            f"{django_server}/events/imminent-workshop",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Shows "You're registered!"
        assert "You're registered!" in body

        # "Join the event" section appears with the internal redirect link.
        assert "Join the event" in body
        assert "https://zoom.us/j/123456" not in body

        # The raw Zoom URL is hidden behind the join redirect endpoint.
        join_link = page.locator(
            'a[href="/events/imminent-workshop/join"]'
        )
        assert join_link.count() >= 1
# ---------------------------------------------------------------
# Scenario 8: Registered member checks an event that is still
#              far away and Zoom link is hidden
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8ZoomLinkHiddenFarFromEvent:
    """Registered member checks an event that is still far away and
    Zoom link is hidden."""

    def test_zoom_link_hidden_when_event_is_far_away(
        self, django_server
    , browser):
        """Given a user logged in as free@test.com who is registered for
        an upcoming Zoom event starting 2 hours from now. The detail page
        shows 'You're registered!' but no 'Join the event' section and
        the Zoom URL is not displayed."""
        _clear_events()
        _ensure_tiers()
        user = _create_user("free@test.com", tier_slug="free")

        now = timezone.now()
        event = _create_event(
            title="Future Workshop",
            slug="future-workshop",
            zoom_join_url="https://zoom.us/j/999000",
            start_datetime=now + datetime.timedelta(hours=2),
            required_level=0,
            status="upcoming",
        )
        _register_user_for_event(user, event)

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /events/future-workshop
        page.goto(
            f"{django_server}/events/future-workshop",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Shows "You're registered!"
        assert "You're registered!" in body

        # No "Join the event" section
        assert "Join the event" not in body

        # The Zoom URL is not displayed anywhere on the page
        assert "https://zoom.us/j/999000" not in body
# ---------------------------------------------------------------
# Scenario 9: Visitor views a completed event and finds the
#              recording
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9CompletedEventNoInlineRecording:
    """Issue #426: completed event detail page does not embed the recording.

    Recording playback lives on the linked Workshop's video page. The event
    detail page is announcement-only — it shows the title and description
    but no inline player.
    """

    def test_completed_event_omits_inline_recording_block(
        self, django_server
    , page):
        """Given an anonymous visitor on a completed event with a
        recording_url, the event detail page renders the title and
        description without an inline player or recording block."""
        _clear_events()
        _ensure_tiers()

        now = timezone.now()
        _create_event(
            title="Past Workshop",
            slug="past-workshop",
            description="A workshop that already happened.",
            start_datetime=now - datetime.timedelta(days=14),
            status="completed",
            recording_url="https://www.youtube.com/watch?v=past123",
        )

        # Step 1: Navigate to /events/past-workshop
        page.goto(
            f"{django_server}/events/past-workshop",
            wait_until="domcontentloaded",
        )

        body = page.content()

        # The announcement copy is visible.
        assert "Past Workshop" in body
        assert "A workshop that already happened." in body

        # Then: No inline recording block, no video player.
        recording_block = page.locator(
            '[data-testid="event-recording-block"]'
        )
        assert recording_block.count() == 0
        assert 'data-source="youtube"' not in body
# ---------------------------------------------------------------
# Scenario 10: Visitor views a completed event that has no
#               recording yet
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10CompletedEventNoRecording:
    """Visitor views a completed event that has no recording yet."""

    def test_completed_event_without_recording_shows_no_recording_link(
        self, django_server
    , page):
        """Given an anonymous visitor. A completed event with no recording.
        The detail page loads but there is no 'Watch the recording' link
        and no 'This event has been recorded' message."""
        _clear_events()
        _ensure_tiers()

        now = timezone.now()
        _create_event(
            title="Unrecorded Session",
            slug="unrecorded-session",
            description="A session without a recording.",
            start_datetime=now - datetime.timedelta(days=7),
            status="completed",
        )

        # Step 1: Navigate to /events/unrecorded-session
        page.goto(
            f"{django_server}/events/unrecorded-session",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Event info is visible
        assert "Unrecorded Session" in body

        # No inline recording block (event has no recording_url, so
        # has_recording is False and the block is not rendered).
        recording_block = page.locator(
            '[data-testid="event-recording-block"]'
        )
        assert recording_block.count() == 0

        # No video player iframe in main content
        assert 'data-source="youtube"' not in body
# ---------------------------------------------------------------
# Scenario 11: Draft events are not visible to the public
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11DraftEventsNotVisible:
    """Draft events are not visible to the public."""

    def test_draft_event_not_on_listing_and_404_on_direct_access(
        self, django_server
    , page):
        """Given an anonymous visitor. A draft event exists. It does not
        appear on the /events listing page. Navigating directly to its
        detail page returns a 404."""
        _clear_events()
        _ensure_tiers()

        _create_event(
            title="Secret Draft Event",
            slug="secret-draft",
            status="draft",
        )

        # Also create an upcoming event so the listing is not empty
        _create_event(
            title="Public Event",
            slug="public-event",
            status="upcoming",
        )

        # Step 1: Navigate to /events
        page.goto(
            f"{django_server}/events",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: "Secret Draft Event" does not appear
        assert "Secret Draft Event" not in body

        # The public event is visible
        assert "Public Event" in body

        # Step 2: Navigate directly to /events/secret-draft
        response = page.goto(
            f"{django_server}/events/secret-draft",
            wait_until="domcontentloaded",
        )

        # Then: Returns a 404
        assert response.status == 404
# ---------------------------------------------------------------
# Scenario 12: Visitor spots a cancelled event in the past events
#               section
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario12CancelledEventInPastSection:
    """Visitor spots a cancelled event in the past events section."""

    def test_cancelled_event_shows_in_past_with_cancelled_badge(
        self, django_server
    , page):
        """Given an anonymous visitor. A cancelled event exists. It appears
        in the Past section of /events with a visible 'Cancelled' badge."""
        _clear_events()
        _ensure_tiers()

        now = timezone.now()
        _create_event(
            title="Cancelled AI Meetup",
            slug="cancelled-meetup",
            status="cancelled",
            start_datetime=now - datetime.timedelta(days=1),
        )

        # Step 1: Navigate to /events
        page.goto(
            f"{django_server}/events",
            wait_until="domcontentloaded",
        )
        page.content()

        # Then: "Cancelled AI Meetup" appears in the Past section
        past_section = page.locator(
            "h2:has-text('Past')"
        ).locator("..")
        past_text = past_section.inner_text()
        assert "Cancelled AI Meetup" in past_text

        # With a visible "Cancelled" badge
        assert "Cancelled" in past_text
