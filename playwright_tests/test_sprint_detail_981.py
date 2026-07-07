"""Playwright coverage for the redesigned sprint detail page (#981)."""

import datetime
import os

import pytest
from freezegun import freeze_time

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_session_for_user,
    ensure_site_config_tiers,
    ensure_tiers,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only
FROZEN_CALL_NOW = "2026-06-15T12:00:00Z"


def _clear_sprint_detail_data():
    from django.db import connection

    from events.models import Event, EventSeries
    from plans.models import Plan, Sprint, SprintEnrollment

    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _create_member(email="main@test.com", *, preferred_timezone=""):
    from django.db import connection

    user = _create_user(email, tier_slug="main")
    if preferred_timezone:
        user.preferred_timezone = preferred_timezone
        user.save(update_fields=["preferred_timezone"])
    connection.close()
    return user


def _create_series(slug="issue-981-calls"):
    from django.db import connection

    from events.models import EventSeries

    series = EventSeries.objects.create(
        name="Sprint calls",
        slug=slug,
        cadence="weekly",
        day_of_week=2,
        start_time=datetime.time(18, 0),
        timezone="Europe/Berlin",
    )
    connection.close()
    return series


def _create_sprint(slug="issue-981-sprint", *, series=None, min_tier_level=20):
    from django.db import connection

    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name="Issue 981 Sprint",
        slug=slug,
        start_date=datetime.date.today() - datetime.timedelta(days=7),
        duration_weeks=6,
        status="active",
        min_tier_level=min_tier_level,
        event_series=series,
    )
    connection.close()
    return sprint


def _future_call_start(days=1):
    day = datetime.date.today() + datetime.timedelta(days=days)
    return datetime.datetime.combine(
        day,
        datetime.time(18, 0),
        tzinfo=datetime.timezone.utc,
    )


def _utc_attr(value):
    return value.isoformat().replace("+00:00", "Z")


def _enroll(email, sprint):
    from django.db import connection

    from accounts.models import User
    from plans.models import SprintEnrollment

    SprintEnrollment.objects.get_or_create(
        sprint=sprint,
        user=User.objects.get(email=email),
    )
    connection.close()


def _create_pending_feedback_response(sprint, email="main@test.com"):
    """Distribute a feedback questionnaire and return the member's pending Response id."""
    from django.db import connection

    from accounts.models import User
    from plans.models import SprintFeedbackRequest
    from plans.services import distribute_sprint_feedback
    from questionnaires.models import Question, Questionnaire, Response

    questionnaire = Questionnaire.objects.create(
        title="Sprint feedback",
        slug=f"{sprint.slug}-feedback",
        purpose="feedback",
    )
    Question.objects.create(
        questionnaire=questionnaire,
        question_type="long_text",
        prompt="How did this sprint go for you?",
        order=0,
        is_required=True,
    )
    request = SprintFeedbackRequest.objects.create(
        sprint=sprint, questionnaire=questionnaire,
    )
    distribute_sprint_feedback(request)
    response = Response.objects.get(
        questionnaire=questionnaire,
        respondent=User.objects.get(email=email),
    )
    response_id = response.pk
    connection.close()
    return response_id


def _create_event(series, *, title, slug, start, end=None, zoom_url=""):
    from django.db import connection

    from events.models import Event

    event = Event.objects.create(
        title=title,
        slug=slug,
        description="Sprint call",
        kind="standard",
        platform="zoom",
        start_datetime=start,
        end_datetime=end,
        timezone="Europe/Berlin",
        status="upcoming",
        origin="studio",
        event_series=series,
        location="Zoom",
        zoom_join_url=zoom_url,
        published=True,
    )
    connection.close()
    return event


@pytest.mark.django_db(transaction=True)
class TestSprintDetailRedesign981:
    @freeze_time(FROZEN_CALL_NOW)
    def test_member_opens_upcoming_call_detail_and_sees_past_call_muted(
        self, django_server, browser, django_db_blocker
    ):
        now = datetime.datetime.now(datetime.timezone.utc)
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            series = _create_series()
            sprint = _create_sprint(series=series)
            _enroll("main@test.com", sprint)
            _create_event(
                series,
                title="Past sprint call",
                slug="past-sprint-call",
                start=now - datetime.timedelta(hours=2),
                end=now - datetime.timedelta(hours=1),
                zoom_url="https://zoom.example.com/past",
            )
            upcoming = _create_event(
                series,
                title="Upcoming sprint call",
                slug="upcoming-sprint-call",
                start=now + datetime.timedelta(days=1),
                zoom_url="https://zoom.example.com/future",
            )
            sprint_slug = sprint.slug
            upcoming_url = upcoming.get_absolute_url()

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")

        rows = page.locator('[data-testid="sprint-call-entry"]')
        assert rows.count() == 2
        upcoming_row = rows.filter(has_text="Upcoming sprint call")
        past_row = rows.filter(has_text="Past sprint call")
        assert "next call" in upcoming_row.locator('[data-testid="sprint-call-status"]').inner_text().lower()
        assert "past" in past_row.locator('[data-testid="sprint-call-status"]').inner_text().lower()
        assert past_row.locator('[data-testid="sprint-call-join"]').count() == 0

        upcoming_row.locator('[data-testid="sprint-call-entry-link"]').click()
        page.wait_for_url(f"**{upcoming_url}")
        ctx.close()

    @freeze_time(FROZEN_CALL_NOW)
    def test_call_starting_soon_uses_tracked_join_link(
        self, django_server, browser, django_db_blocker
    ):
        now = datetime.datetime.now(datetime.timezone.utc)
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            series = _create_series()
            sprint = _create_sprint(series=series)
            _enroll("main@test.com", sprint)
            _create_event(
                series,
                title="Live sprint call",
                slug="live-sprint-call",
                start=now + datetime.timedelta(minutes=4),
                zoom_url="https://zoom.example.com/raw-live-link",
            )
            sprint_slug = sprint.slug

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")

        join = page.locator('[data-testid="sprint-call-join"]')
        # Issue #1082: id-canonical /events/<id>/<slug>/join URL.
        join_href = join.get_attribute("href")
        assert join_href.endswith("/live-sprint-call/join")
        assert "/events/" in join_href
        assert join.get_attribute("target") == "_blank"
        assert "zoom.example.com/raw-live-link" not in page.content()
        ctx.close()

    @freeze_time(FROZEN_CALL_NOW)
    def test_not_open_call_and_empty_calls_states(
        self, django_server, browser, django_db_blocker
    ):
        now = datetime.datetime.now(datetime.timezone.utc)
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            series = _create_series()
            sprint = _create_sprint(series=series)
            _enroll("main@test.com", sprint)
            _create_event(
                series,
                title="Future sprint call",
                slug="future-sprint-call",
                start=now + datetime.timedelta(days=2),
                zoom_url="https://zoom.example.com/raw-future-link",
            )
            empty_sprint = _create_sprint(slug="issue-981-empty")
            _enroll("main@test.com", empty_sprint)
            sprint_slug = sprint.slug
            empty_slug = empty_sprint.slug

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")
        assert page.locator('[data-testid="sprint-call-join-not-open"]').is_visible()
        assert "Join link opens ~5 min before start" in page.locator(
            '[data-testid="sprint-call-join-not-open"]'
        ).inner_text()
        assert page.locator('[data-testid="sprint-call-join"]').count() == 0
        assert page.locator('[data-testid="sprint-call-details"]').get_attribute("href")

        page.goto(f"{django_server}/sprints/{empty_slug}", wait_until="domcontentloaded")
        assert page.locator('[data-testid="sprint-calls-empty"]').is_visible()
        assert page.locator('[data-testid="sprint-cta-enrolled"]').is_visible()
        ctx.close()

    def test_saved_timezone_payload_uses_event_time_display_partial(
        self, django_server, browser, django_db_blocker
    ):
        start = _future_call_start(days=1)
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member(preferred_timezone="America/New_York")
            series = _create_series()
            sprint = _create_sprint(series=series)
            _enroll("main@test.com", sprint)
            _create_event(
                series,
                title="Timezone sprint call",
                slug="timezone-sprint-call",
                start=start,
                end=start + datetime.timedelta(hours=1),
            )
            sprint_slug = sprint.slug

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")

        time_display = page.locator('[data-event-time-display]')
        assert time_display.get_attribute("data-start-utc") == _utc_attr(start)
        assert time_display.get_attribute("data-default-timezone") == "America/New_York"
        assert time_display.get_attribute("data-browser-timezone-enabled") == "false"
        assert "America/New_York" in time_display.inner_text()
        ctx.close()

    def test_browser_timezone_localizes_call_time_without_saved_timezone(
        self, django_server, browser, django_db_blocker
    ):
        start = _future_call_start(days=1)
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            series = _create_series()
            sprint = _create_sprint(series=series)
            _enroll("main@test.com", sprint)
            _create_event(
                series,
                title="Browser timezone first call",
                slug="browser-timezone-first-call",
                start=start,
                end=start + datetime.timedelta(hours=1),
            )
            _create_event(
                series,
                title="Browser timezone second call",
                slug="browser-timezone-second-call",
                start=start + datetime.timedelta(days=1),
                end=start + datetime.timedelta(days=1, hours=1),
            )
            sprint_slug = sprint.slug
            session_key = create_session_for_user("main@test.com")

        ctx = browser.new_context(
            timezone_id="America/Los_Angeles",
            viewport={"width": 1280, "height": 720},
        )
        ctx.add_cookies([
            {
                "name": "sessionid",
                "value": session_key,
                "domain": "127.0.0.1",
                "path": "/",
            },
            {
                "name": "csrftoken",
                "value": "e2e-test-csrf-token-value",
                "domain": "127.0.0.1",
                "path": "/",
            },
        ])
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="networkidle")

        time_displays = page.locator('[data-event-time-display]')
        assert time_displays.count() == 2
        for index in range(2):
            time_display = time_displays.nth(index)
            assert time_display.get_attribute("data-browser-timezone-enabled") == "true"
            assert time_display.get_attribute("data-default-timezone") == "Europe/Berlin"
            assert "America/Los_Angeles" in time_display.inner_text()
            assert "Europe/Berlin" not in time_display.inner_text()
        ctx.close()

    def test_eligible_member_can_join_near_top(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            sprint = _create_sprint()
            sprint_slug = sprint.slug

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")

        action_panel = page.locator('[data-testid="sprint-primary-action"]')
        assert action_panel.locator('[data-testid="sprint-cta-join"]').is_visible()
        action_panel.locator('[data-testid="sprint-cta-join"]').click()
        page.wait_for_url(f"**/sprints/{sprint_slug}/board")
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")
        assert page.locator('[data-testid="sprint-cta-enrolled"]').is_visible()
        assert page.locator('[data-testid="sprint-calls-empty"]').is_visible()
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestSprintDetailDesignSystem1138:
    """Design-system alignment for the sprint detail page (#1138)."""

    def test_empty_calls_state_renders_shared_component(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            sprint = _create_sprint()
            _enroll("main@test.com", sprint)
            sprint_slug = sprint.slug

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")

        empty = page.locator('[data-testid="sprint-calls-empty"]')
        assert empty.is_visible()
        # Shared component card look: bg-card + centered, not the old dashed box.
        card_class = empty.get_attribute("class")
        assert "bg-card" in card_class
        assert "border-dashed" not in card_class
        assert empty.get_attribute("data-empty-kind") == "fresh"
        assert empty.locator('[data-lucide]').count() >= 1
        assert "No calls scheduled yet" in empty.inner_text()
        assert "post the schedule here" in empty.inner_text()
        # Inner regression marker exposed by the shared component.
        assert page.locator('[data-testid="member-empty-state"]').count() >= 1
        ctx.close()

    def test_eyebrows_use_accent_class(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            sprint = _create_sprint()
            _enroll("main@test.com", sprint)
            sprint_slug = sprint.slug

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")

        for label in ["Sprint", "Accountability", "Calls"]:
            eyebrow = page.get_by_text(label, exact=True).first
            cls = eyebrow.get_attribute("class")
            assert "text-accent" in cls, f"{label} eyebrow missing text-accent: {cls}"
            assert "tracking-widest" in cls, f"{label} eyebrow missing tracking-widest: {cls}"
            assert "font-medium" in cls, f"{label} eyebrow missing font-medium: {cls}"
            assert "text-muted-foreground" not in cls
            assert "tracking-wide " not in f"{cls} " or "tracking-widest" in cls
        ctx.close()

    def test_section_titles_use_smaller_section_scale(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            sprint = _create_sprint()
            _enroll("main@test.com", sprint)
            sprint_slug = sprint.slug

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")

        for title in ["Sprint calls", "Your partners"]:
            heading = page.get_by_role("heading", name=title, exact=True).first
            cls = heading.get_attribute("class")
            assert "text-2xl" in cls, f"{title} missing text-2xl: {cls}"
            assert "sm:text-3xl" in cls, f"{title} missing sm:text-3xl: {cls}"
            assert "tracking-tight" in cls
            assert "text-xl" not in cls
        ctx.close()

    @freeze_time(FROZEN_CALL_NOW)
    def test_populated_calls_list_not_regressed(
        self, django_server, browser, django_db_blocker
    ):
        start = datetime.datetime(2026, 6, 20, 18, 0, tzinfo=datetime.timezone.utc)
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            series = _create_series()
            sprint = _create_sprint(series=series)
            _enroll("main@test.com", sprint)
            _create_event(
                series,
                title="Upcoming sprint call",
                slug="upcoming-sprint-call",
                start=start,
                end=start + datetime.timedelta(hours=1),
            )
            sprint_slug = sprint.slug

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")

        assert page.locator('[data-testid="sprint-call-entry"]').count() >= 1
        assert page.locator('[data-testid="sprint-calls-empty"]').count() == 0
        assert page.locator('[data-testid="sprint-call-details"]').first.get_attribute("href")
        ctx.close()

    def test_feedback_cta_is_canonical_primary_button(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            sprint = _create_sprint()
            _enroll("main@test.com", sprint)
            response_id = _create_pending_feedback_response(sprint)
            sprint_slug = sprint.slug

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")

        cta = page.locator('[data-testid="sprint-feedback-cta-link"]')
        assert cta.is_visible()
        cls = cta.get_attribute("class")
        assert "bg-accent" in cls
        assert "hover:bg-accent/90" in cls
        assert "hover:opacity-90" not in cls
        assert cta.inner_text().strip() == "Share your sprint feedback"
        assert f"/sprints/{sprint_slug}/feedback/{response_id}" in cta.get_attribute("href")

        cta.click()
        page.wait_for_url(f"**/sprints/{sprint_slug}/feedback/{response_id}**")
        assert "How did this sprint go for you?" in page.content()
        ctx.close()
