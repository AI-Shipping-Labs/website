"""Playwright E2E for the Sprint <-> EventGroup link (issue #565).

Covers the eight BDD scenarios from the spec:

1. Staff links an existing event group to an existing sprint.
2. Staff unlinks an event group and the group survives.
3. One event group backs two different sprints at the same time.
4. Member discovers the meeting schedule on a sprint's public page.
5. Sprint with no event group hides the meeting schedule section.
6. Sprint linked to an empty event group also hides the schedule.
7. Staff edits the sprint form and the invalid event group is rejected.
8. Anonymous visitor can see the meeting schedule without logging in.
9. (Bonus, from the spec) Staff jumps from sprint detail straight into
   linking a group via the "Link an event group" CTA.

Server-side artefact assertions (FK persistence, ``SET_NULL`` semantics,
query count) live in the Django ``TestCase`` suites
``plans.tests.test_sprint_event_group`` and
``studio.tests.test_sprint_event_group`` -- per Rule 15 those are NOT
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
    from events.models import Event, EventGroup
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
    EventGroup.objects.all().delete()
    connection.close()


def _make_event_group(name, slug, *, events=0):
    from events.models import Event, EventGroup

    group = EventGroup.objects.create(
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
            event_group=group,
            series_position=i,
            location='Zoom',
            published=True,
        )
    connection.close()
    return group


def _make_sprint(name, slug, *, event_group=None, min_tier_level=0):
    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=datetime.date(2026, 5, 1),
        status='active',
        min_tier_level=min_tier_level,
        event_group=event_group,
    )
    connection.close()
    return sprint


# ---------------------------------------------------------------------------
# Scenario 1: Staff links an existing event group to an existing sprint.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaffLinksEventGroup:
    def test_staff_picks_event_group_and_sees_occurrences(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_data()
        _create_staff_user("staff@test.com")
        group = _make_event_group(
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

        # The Event group select renders with "— None —" pre-selected.
        select = page.locator('[data-testid="sprint-event-group"]')
        select.wait_for(state="visible")
        selected_value = page.evaluate(
            """() => {
                const sel = document.querySelector(
                    '[data-testid="sprint-event-group"]'
                );
                return sel ? sel.value : null;
            }"""
        )
        assert selected_value == ""

        # Pick the group by id and submit.
        select.select_option(str(group.pk))
        page.locator('button[type="submit"]').click()

        # Lands on the detail page with a success flash.
        page.wait_for_url(
            f"{django_server}/studio/sprints/{sprint.pk}/",
        )
        # The event-group section now shows the group name + 6 rows.
        page.locator(
            '[data-testid="sprint-event-group-link"]'
        ).wait_for(state="visible")
        rows = page.locator(
            '[data-testid="sprint-event-group-row"]'
        )
        assert rows.count() == 6

        # The count text mentions 6 occurrences.
        count_text = page.locator(
            '[data-testid="sprint-event-group-count"]'
        ).inner_text()
        assert "6" in count_text

        context.close()


# ---------------------------------------------------------------------------
# Scenario 2: Staff unlinks an event group and the group survives.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaffUnlinksEventGroup:
    def test_unlinking_preserves_group_and_events(
        self, django_server, browser,
    ):
        from events.models import Event, EventGroup

        _ensure_tiers()
        _clear_data()
        _create_staff_user("staff@test.com")
        group = _make_event_group(
            "Wednesday office hours", "wed-oh", events=6,
        )
        sprint = _make_sprint(
            "May 2026", "may-2026", event_group=group,
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/edit",
            wait_until="domcontentloaded",
        )

        # The group is pre-selected.
        selected_value = page.evaluate(
            """() => document.querySelector(
                '[data-testid="sprint-event-group"]'
            ).value"""
        )
        assert selected_value == str(group.pk)

        # Switch to "— None —" and save.
        page.locator(
            '[data-testid="sprint-event-group"]'
        ).select_option("")
        page.locator('button[type="submit"]').click()

        page.wait_for_url(
            f"{django_server}/studio/sprints/{sprint.pk}/",
        )

        # Empty-state copy + CTA are visible.
        page.locator(
            '[data-testid="sprint-event-group-empty"]'
        ).wait_for(state="visible")
        page.locator(
            '[data-testid="sprint-event-group-link-cta"]'
        ).wait_for(state="visible")

        # The group and all 6 events still exist.
        assert EventGroup.objects.filter(pk=group.pk).exists()
        assert Event.objects.filter(event_group=group).count() == 6
        connection.close()

        context.close()


# ---------------------------------------------------------------------------
# Scenario 3: One group backs two different sprints at the same time.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestOneGroupBacksTwoSprints:
    def test_same_group_assignable_to_two_sprints(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_data()
        _create_staff_user("staff@test.com")
        group = _make_event_group(
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
                '[data-testid="sprint-event-group"]'
            ).select_option(str(group.pk))
            page.locator('button[type="submit"]').click()
            page.wait_for_url(
                f"{django_server}/studio/sprints/{sprint_pk}/",
            )
            # No error banner appeared on either submit.
            assert page.locator(
                '.bg-destructive\\/10'
            ).count() == 0
            # The group is displayed for this sprint.
            page.locator(
                '[data-testid="sprint-event-group-link"]'
            ).wait_for(state="visible")
            rows = page.locator(
                '[data-testid="sprint-event-group-row"]'
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
        group = _make_event_group(
            "May office hours", "may-oh", events=6,
        )
        _make_sprint(
            "May 2026 sprint", "may-2026-sprint", event_group=group,
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
# Scenario 5: No event group -> meeting schedule section hidden.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestNoEventGroupHidesSchedule:
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
# Scenario 6: Linked but empty event group -> section also hidden.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestEmptyEventGroupHidesSchedule:
    def test_linked_but_empty_group_hides_section(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_data()
        _create_user(
            "main@test.com", tier_slug="main", email_verified=True,
        )
        empty_group = _make_event_group("Empty", "empty", events=0)
        _make_sprint(
            "Empty sprint", "empty-sprint", event_group=empty_group,
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
# Scenario 7: Crafted POST with an invalid event_group id is rejected.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestInvalidEventGroupRejected:
    def test_invalid_event_group_post_returns_400_and_preserves_fk(
        self, django_server, browser,
    ):

        _ensure_tiers()
        _clear_data()
        _create_staff_user("staff@test.com")
        group = _make_event_group("Original", "original", events=1)
        sprint = _make_sprint(
            "May sprint", "may", event_group=group,
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
                    '[data-testid="sprint-event-group"]'
                );
                const opt = document.createElement('option');
                opt.value = badId;
                opt.text = 'Bogus group';
                sel.appendChild(opt);
                sel.value = badId;
            }""",
            "99999",
        )

        page.locator('button[type="submit"]').click()

        # The error message renders inline (HTTP 400 from the view).
        page.wait_for_selector(
            'text=Selected event group does not exist.'
        )

        # The sprint's FK is unchanged in the database.
        sprint.refresh_from_db()
        assert sprint.event_group_id == group.pk
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
        group = _make_event_group(
            "May office hours", "may-oh", events=6,
        )
        _make_sprint(
            "May 2026 sprint", "may-2026-sprint", event_group=group,
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
class TestSprintDetailLinkAnEventGroupCTA:
    def test_empty_state_cta_routes_to_edit_with_anchor(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_data()
        _create_staff_user("staff@test.com")
        group = _make_event_group(
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
            '[data-testid="sprint-event-group-link-cta"]'
        )
        cta.wait_for(state="visible")
        href = cta.get_attribute("href")
        assert href is not None
        assert href.endswith("#event-group-field")

        # Click it -> the form loads with the anchored field present.
        cta.click()
        page.wait_for_url(
            f"{django_server}/studio/sprints/{sprint.pk}/edit"
            "#event-group-field",
        )
        page.locator(
            '[data-testid="sprint-event-group"]'
        ).wait_for(state="visible")

        # Pick the group and save -> the detail page now shows the
        # link and the 2 occurrences.
        page.locator(
            '[data-testid="sprint-event-group"]'
        ).select_option(str(group.pk))
        page.locator('button[type="submit"]').click()
        page.wait_for_url(
            f"{django_server}/studio/sprints/{sprint.pk}/",
        )
        page.locator(
            '[data-testid="sprint-event-group-link"]'
        ).wait_for(state="visible")
        assert page.locator(
            '[data-testid="sprint-event-group-row"]'
        ).count() == 2

        context.close()
