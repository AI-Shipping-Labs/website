"""Playwright E2E for the Sprint <-> EventSeries link (issue #565,
renamed from event-group in #575).

Covers the eight BDD scenarios from the spec:

1. Staff links an existing event series to an existing sprint.
2. Staff unlinks an event series and the series survives.
3. One event series backs two different sprints at the same time.
4. Member discovers the meeting schedule on a sprint's public page.
5. Sprint with no event series hides the meeting schedule section.
6. Sprint linked to an empty event series also hides the schedule.
7. Staff edits the sprint form and the invalid event series is rejected.
8. Anonymous visitor can see the meeting schedule without logging in.
9. (Bonus, from the spec) Staff jumps from sprint detail straight into
   linking a series via the "Link an event series" CTA.

Server-side artefact assertions (FK persistence, ``SET_NULL`` semantics,
query count) live in the Django ``TestCase`` suites
``plans.tests.test_sprint_event_series`` and
``studio.tests.test_sprint_event_series`` -- per Rule 15 those are NOT
duplicated here. These tests assert the user-visible flow only.
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


def _clear_data():
    from events.models import Event, EventSeries
    from plans.models import (
        Checkpoint,
        Deliverable,
        InterviewNote,
        NextStep,
        Plan,
        Resource,
        Sprint,
        SprintEnrollment,
        Week,
    )

    Checkpoint.objects.all().delete()
    Week.objects.all().delete()
    Resource.objects.all().delete()
    Deliverable.objects.all().delete()
    NextStep.objects.all().delete()
    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _make_event_series(name, slug, *, events=0):
    from events.models import Event, EventSeries

    series = EventSeries.objects.create(
        name=name,
        slug=slug,
        cadence='weekly',
        cadence_weeks=1,
        day_of_week=2,
        start_time=datetime.time(18, 0),
        timezone='Europe/Berlin',
    )
    base = datetime.datetime(
        2026, 5, 6, 18, 0, tzinfo=datetime.timezone.utc,
    )
    for i in range(1, events + 1):
        start = base + datetime.timedelta(days=7 * (i - 1))
        Event.objects.create(
            title=f'{name} — Session {i}',
            slug=f'{slug}-session-{i}',
            description='',
            kind='standard',
            platform='zoom',
            start_datetime=start,
            timezone='Europe/Berlin',
            status='upcoming',
            origin='studio',
            event_series=series,
            series_position=i,
            location='Zoom',
            published=True,
        )
    connection.close()
    return series


def _make_sprint(name, slug, *, event_series=None, min_tier_level=0):
    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=datetime.date(2026, 5, 1),
        status='active',
        min_tier_level=min_tier_level,
        event_series=event_series,
    )
    connection.close()
    return sprint


# ---------------------------------------------------------------------------
# Scenario 1: Staff links an existing event series to an existing sprint.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaffLinksEventSeries:
    def test_staff_picks_event_series_and_sees_occurrences(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_data()
        _create_staff_user("staff@test.com")
        series = _make_event_series(
            "Wednesday office hours, May 2026",
            "wed-oh-may-2026",
            events=6,
        )
        sprint = _make_sprint("May 2026 sprint", "may-2026-sprint")

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/edit",
            wait_until="domcontentloaded",
        )

        # The Event series select renders with "— None —" pre-selected.
        select = page.locator('[data-testid="sprint-event-series"]')
        select.wait_for(state="visible")
        selected_value = page.evaluate(
            """() => {
                const sel = document.querySelector(
                    '[data-testid="sprint-event-series"]'
                );
                return sel ? sel.value : null;
            }"""
        )
        assert selected_value == ""

        # Pick the series by id and submit.
        select.select_option(str(series.pk))
        page.locator('button[type="submit"]').click()

        # Lands on the detail page with a success flash.
        page.wait_for_url(
            f"{django_server}/studio/sprints/{sprint.pk}/",
        )
        # The event-series section now shows the series name + 6 rows.
        page.locator(
            '[data-testid="sprint-event-series-link"]'
        ).wait_for(state="visible")
        rows = page.locator(
            '[data-testid="sprint-event-series-row"]'
        )
        assert rows.count() == 6

        # The count text mentions 6 occurrences.
        count_text = page.locator(
            '[data-testid="sprint-event-series-count"]'
        ).inner_text()
        assert "6" in count_text

        context.close()


# ---------------------------------------------------------------------------
# Scenario 2: Staff unlinks an event series and the series survives.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaffUnlinksEventSeries:
    def test_unlinking_preserves_series_and_events(
        self, django_server, browser,
    ):
        from events.models import Event, EventSeries

        _ensure_tiers()
        _clear_data()
        _create_staff_user("staff@test.com")
        series = _make_event_series(
            "Wednesday office hours", "wed-oh", events=6,
        )
        sprint = _make_sprint(
            "May 2026", "may-2026", event_series=series,
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/edit",
            wait_until="domcontentloaded",
        )

        # The series is pre-selected.
        selected_value = page.evaluate(
            """() => document.querySelector(
                '[data-testid="sprint-event-series"]'
            ).value"""
        )
        assert selected_value == str(series.pk)

        # Switch to "— None —" and save.
        page.locator(
            '[data-testid="sprint-event-series"]'
        ).select_option("")
        page.locator('button[type="submit"]').click()

        page.wait_for_url(
            f"{django_server}/studio/sprints/{sprint.pk}/",
        )

        # Empty-state copy + CTA are visible.
        page.locator(
            '[data-testid="sprint-event-series-empty"]'
        ).wait_for(state="visible")
        page.locator(
            '[data-testid="sprint-event-series-link-cta"]'
        ).wait_for(state="visible")

        # The series and all 6 events still exist.
        assert EventSeries.objects.filter(pk=series.pk).exists()
        assert Event.objects.filter(event_series=series).count() == 6
        connection.close()

        context.close()


# ---------------------------------------------------------------------------
# Scenario 3: One series backs two different sprints at the same time.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestOneSeriesBacksTwoSprints:
    def test_same_series_assignable_to_two_sprints(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_data()
        _create_staff_user("staff@test.com")
        series = _make_event_series(
            "Wednesday office hours, Q2 2026", "wed-oh-q2", events=6,
        )
        may = _make_sprint("May cohort", "may-cohort")
        june = _make_sprint("June cohort", "june-cohort")

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        for sprint_pk in (may.pk, june.pk):
            page.goto(
                f"{django_server}/studio/sprints/{sprint_pk}/edit",
                wait_until="domcontentloaded",
            )
            page.locator(
                '[data-testid="sprint-event-series"]'
            ).select_option(str(series.pk))
            page.locator('button[type="submit"]').click()
            page.wait_for_url(
                f"{django_server}/studio/sprints/{sprint_pk}/",
            )
            # No error banner appeared on either submit.
            assert page.locator(
                '.bg-destructive\\/10'
            ).count() == 0
            # The series is displayed for this sprint.
            page.locator(
                '[data-testid="sprint-event-series-link"]'
            ).wait_for(state="visible")
            rows = page.locator(
                '[data-testid="sprint-event-series-row"]'
            )
            assert rows.count() == 6

        context.close()


# ---------------------------------------------------------------------------
# Scenario 4: Member discovers the meeting schedule on the public page.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestMemberSeesMeetingSchedule:
    def test_member_sees_schedule_and_can_click_through(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_data()
        _create_user(
            "main@test.com", tier_slug="main", email_verified=True,
        )
        series = _make_event_series(
            "May office hours", "may-oh", events=6,
        )
        _make_sprint(
            "May 2026 sprint", "may-2026-sprint", event_series=series,
        )

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/sprints/may-2026-sprint",
            wait_until="domcontentloaded",
        )

        page.locator(
            '[data-testid="sprint-meeting-schedule"]'
        ).wait_for(state="visible")
        rows = page.locator(
            '[data-testid="sprint-meeting-schedule-row"]'
        )
        assert rows.count() == 6

        # Each row exposes a date, time and a location field.
        page.locator(
            '[data-testid="sprint-meeting-schedule-date"]'
        ).first.wait_for(state="visible")
        page.locator(
            '[data-testid="sprint-meeting-schedule-time"]'
        ).first.wait_for(state="visible")
        page.locator(
            '[data-testid="sprint-meeting-schedule-location"]'
        ).first.wait_for(state="visible")

        # Click the first occurrence -> lands on /events/<first-slug>.
        first_link = page.locator(
            '[data-testid="sprint-meeting-schedule-link"]'
        ).first
        href = first_link.get_attribute("href")
        assert href is not None and href.startswith("/events/may-oh-session-")
        first_link.click()
        page.wait_for_url(f"{django_server}{href}")

        context.close()


# ---------------------------------------------------------------------------
# Scenario 5: No event series -> meeting schedule section hidden.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestNoEventSeriesHidesSchedule:
    def test_unlinked_sprint_hides_section(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_data()
        _create_user(
            "main@test.com", tier_slug="main", email_verified=True,
        )
        _make_sprint("Solo sprint", "solo-sprint")

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/sprints/solo-sprint",
            wait_until="domcontentloaded",
        )

        # The schedule section must NOT be in the DOM at all.
        assert page.locator(
            '[data-testid="sprint-meeting-schedule"]'
        ).count() == 0

        # The Join CTA still resolves for an eligible member.
        page.locator(
            '[data-testid="sprint-cta-join"]'
        ).wait_for(state="visible")

        context.close()


# ---------------------------------------------------------------------------
# Scenario 6: Linked but empty event series -> section also hidden.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestEmptyEventSeriesHidesSchedule:
    def test_linked_but_empty_series_hides_section(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_data()
        _create_user(
            "main@test.com", tier_slug="main", email_verified=True,
        )
        empty_series = _make_event_series("Empty", "empty", events=0)
        _make_sprint(
            "Empty sprint", "empty-sprint", event_series=empty_series,
        )

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/sprints/empty-sprint",
            wait_until="domcontentloaded",
        )

        # Schedule section is not rendered.
        assert page.locator(
            '[data-testid="sprint-meeting-schedule"]'
        ).count() == 0
        # And there is no leak of staff-only "no meetings yet" copy.
        body_text = page.locator("body").inner_text()
        assert "no meetings yet" not in body_text.lower()

        context.close()


# ---------------------------------------------------------------------------
# Scenario 7: Crafted POST with an invalid event_series id is rejected.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestInvalidEventSeriesRejected:
    def test_invalid_event_series_post_returns_400_and_preserves_fk(
        self, django_server, browser,
    ):

        _ensure_tiers()
        _clear_data()
        _create_staff_user("staff@test.com")
        series = _make_event_series("Original", "original", events=1)
        sprint = _make_sprint(
            "May sprint", "may", event_series=series,
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        # Load the edit form to get a valid CSRF token cookie.
        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/edit",
            wait_until="domcontentloaded",
        )

        # Inject an option with the bad id so we can pick it client-side
        # without bypassing the browser. The server is what enforces the
        # invariant; the option-injection just defeats the HTML select's
        # "must be one of these" guard.
        page.evaluate(
            """(badId) => {
                const sel = document.querySelector(
                    '[data-testid="sprint-event-series"]'
                );
                const opt = document.createElement('option');
                opt.value = badId;
                opt.text = 'Bogus series';
                sel.appendChild(opt);
                sel.value = badId;
            }""",
            "99999",
        )

        page.locator('button[type="submit"]').click()

        # The error message renders inline (HTTP 400 from the view).
        page.wait_for_selector(
            'text=Selected event series does not exist.'
        )

        # The sprint's FK is unchanged in the database.
        sprint.refresh_from_db()
        assert sprint.event_series_id == series.pk
        connection.close()

        context.close()


# ---------------------------------------------------------------------------
# Scenario 8: Anonymous viewer sees the meeting schedule.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonymousSeesSchedule:
    def test_anonymous_sees_schedule_and_can_click_through(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_data()
        series = _make_event_series(
            "May office hours", "may-oh", events=6,
        )
        _make_sprint(
            "May 2026 sprint", "may-2026-sprint", event_series=series,
        )

        page = browser.new_context().new_page()

        page.goto(
            f"{django_server}/sprints/may-2026-sprint",
            wait_until="domcontentloaded",
        )

        # Anonymous viewer still sees the meeting schedule and the
        # 6 occurrences.
        page.locator(
            '[data-testid="sprint-meeting-schedule"]'
        ).wait_for(state="visible")
        assert page.locator(
            '[data-testid="sprint-meeting-schedule-row"]'
        ).count() == 6

        # The login CTA is preserved.
        page.locator(
            '[data-testid="sprint-cta-login"]'
        ).wait_for(state="visible")

        # Clicking an occurrence lands on the event detail page.
        first = page.locator(
            '[data-testid="sprint-meeting-schedule-link"]'
        ).first
        href = first.get_attribute("href")
        assert href is not None and href.startswith("/events/")
        first.click()
        page.wait_for_url(f"{django_server}{href}")


# ---------------------------------------------------------------------------
# Scenario 9: Staff jumps from sprint detail straight into linking.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSprintDetailLinkAnEventSeriesCTA:
    def test_empty_state_cta_routes_to_edit_with_anchor(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_data()
        _create_staff_user("staff@test.com")
        series = _make_event_series(
            "Wednesday office hours", "wed-oh", events=2,
        )
        sprint = _make_sprint("Solo sprint", "solo-sprint")

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/",
            wait_until="domcontentloaded",
        )

        # Empty-state CTA is the entry point.
        cta = page.locator(
            '[data-testid="sprint-event-series-link-cta"]'
        )
        cta.wait_for(state="visible")
        href = cta.get_attribute("href")
        assert href is not None
        assert href.endswith("#event-series-field")

        # Click it -> the form loads with the anchored field present.
        cta.click()
        page.wait_for_url(
            f"{django_server}/studio/sprints/{sprint.pk}/edit"
            "#event-series-field",
        )
        page.locator(
            '[data-testid="sprint-event-series"]'
        ).wait_for(state="visible")

        # Pick the series and save -> the detail page now shows the
        # link and the 2 occurrences.
        page.locator(
            '[data-testid="sprint-event-series"]'
        ).select_option(str(series.pk))
        page.locator('button[type="submit"]').click()
        page.wait_for_url(
            f"{django_server}/studio/sprints/{sprint.pk}/",
        )
        page.locator(
            '[data-testid="sprint-event-series-link"]'
        ).wait_for(state="visible")
        assert page.locator(
            '[data-testid="sprint-event-series-row"]'
        ).count() == 2

        context.close()
