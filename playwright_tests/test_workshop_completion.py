"""Playwright E2E tests for workshop page completion (issue #365).

Covers the user-flow scenarios in the groomed spec:

1. Member marks a workshop page finished and the choice persists
   across reloads (server-side state, not JS-only).
2. Started workshop appears on the dashboard's Continue Learning
   widget with the correct progress and a Continue link to the next
   unfinished page.
3. Marking the final page completed removes the workshop from
   Continue Learning, but in-progress courses for the same user are
   unaffected.
4. Course-only users see the same Continue Learning behaviour they
   saw before this issue (regression guard).
5. Anonymous visitors do not see the Mark as completed button on a
   workshop page that is otherwise visible to them.

Usage:
    uv run pytest playwright_tests/test_workshop_completion.py -v
"""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402


def _clear_workshops_and_progress():
    """Drop workshop / course / completion rows so each scenario starts
    from a known state."""
    from content.models import (
        Course,
        Enrollment,
        Module,
        Unit,
        UserContentCompletion,
        UserCourseProgress,
        Workshop,
        WorkshopPage,
    )
    UserContentCompletion.objects.all().delete()
    UserCourseProgress.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Enrollment.objects.all().delete()
    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    connection.close()


def _create_three_page_workshop(slug='ws-complete', pages_required_level=0):
    from content.models import Workshop, WorkshopPage
    workshop = Workshop.objects.create(
        slug=slug,
        title='Production Agents',
        date=datetime.date(2026, 4, 21),
        status='published',
        landing_required_level=0,
        pages_required_level=pages_required_level,
        recording_required_level=max(pages_required_level, 20),
        description='Description body.',
    )
    pages = [
        WorkshopPage.objects.create(
            workshop=workshop, slug='intro', title='Introduction',
            sort_order=1, body='Welcome',
        ),
        WorkshopPage.objects.create(
            workshop=workshop, slug='setup', title='Setup',
            sort_order=2, body='Setup body',
        ),
        WorkshopPage.objects.create(
            workshop=workshop, slug='deploy', title='Deploy',
            sort_order=3, body='Deploy body',
        ),
    ]
    connection.close()
    return workshop, pages


def _mark_page_completed(user_email, page):
    """Pre-create a completion row from the test side so we can stage
    state without round-tripping through the API."""
    from django.utils import timezone

    from accounts.models import User
    from content.models import UserContentCompletion
    from content.models.completion import CONTENT_TYPE_WORKSHOP_PAGE
    user = User.objects.get(email=user_email)
    UserContentCompletion.objects.create(
        user=user,
        content_type=CONTENT_TYPE_WORKSHOP_PAGE,
        object_id=page.pk,
        completed_at=timezone.now(),
    )
    connection.close()


# ----------------------------------------------------------------------
# Scenario 1: Mark complete persists across reload (server-side).
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestMarkCompletedPersistsAcrossReload:
    def test_button_state_persists_on_reload(
        self, browser, django_server,
    ):
        _clear_workshops_and_progress()
        workshop, pages = _create_three_page_workshop()
        _create_user('main@test.com', tier_slug='main')

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/{workshop.slug}/tutorial/intro',
            wait_until='domcontentloaded',
        )
        # Initial state: button visible, in default styling.
        btn = page.locator('[data-testid="mark-page-complete-btn"]')
        assert btn.count() == 1
        btn.first.wait_for(state='visible')
        # Click and wait for the toggle response; the JS swaps the
        # innerHTML to 'Completed', so wait for that text.
        btn.first.click()
        page.wait_for_function(
            "document.querySelector('[data-testid=\"mark-page-complete-btn\"]')"
            ".textContent.includes('Completed')",
        )

        # Reload — the server-rendered HTML must show the completed
        # state without any JS interaction.
        page.reload(wait_until='domcontentloaded')
        body = page.content()
        # The button HTML rendered server-side should now contain the
        # green-completed classes and the Completed copy. We grep the
        # button substring rather than the whole page so the JS
        # toggle's class list (which mentions both states) doesn't
        # confuse the assertion.
        import re
        match = re.search(
            r'<button[^>]*data-testid="mark-page-complete-btn"[^>]*>'
            r'(.*?)</button>',
            body,
            re.DOTALL,
        )
        assert match is not None, 'button missing from reloaded page'
        btn_inner = match.group(0)
        assert 'border-green-500/30' in btn_inner
        assert 'Completed' in btn_inner

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 2: Workshop appears in Continue Learning after first
# completion.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestWorkshopShowsInContinueLearning:
    def test_workshop_card_appears_with_progress(
        self, browser, django_server,
    ):
        _clear_workshops_and_progress()
        workshop, pages = _create_three_page_workshop()
        _create_user('main@test.com', tier_slug='main')
        # Stage one completed page (intro). 1/3 progress.
        _mark_page_completed('main@test.com', pages[0])

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        body = page.content()
        # Workshop card is rendered with workshop-specific test id.
        assert 'data-testid="continue-learning-workshop"' in body
        assert workshop.title in body
        assert '1/3 pages completed' in body
        # CTA href points to the next unfinished page (setup).
        cta = page.locator(
            '[data-testid="continue-learning-workshop-cta"]',
        ).first
        assert cta.get_attribute('href') == (
            f'/workshops/{workshop.slug}/tutorial/setup'
        )

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 3: Finishing the last page removes the workshop from
# Continue Learning.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFinishingLastPageRemovesFromContinueLearning:
    def test_workshop_drops_off_after_final_page(
        self, browser, django_server,
    ):
        _clear_workshops_and_progress()
        workshop, pages = _create_three_page_workshop()
        _create_user('main@test.com', tier_slug='main')
        _mark_page_completed('main@test.com', pages[0])
        _mark_page_completed('main@test.com', pages[1])

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        # Visit the third page and click Mark as completed.
        page.goto(
            f'{django_server}/workshops/{workshop.slug}/tutorial/deploy',
            wait_until='domcontentloaded',
        )
        btn = page.locator('[data-testid="mark-page-complete-btn"]').first
        btn.click()
        page.wait_for_function(
            "document.querySelector('[data-testid=\"mark-page-complete-btn\"]')"
            ".textContent.includes('Completed')",
        )

        # Dashboard should no longer list the workshop.
        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        body = page.content()
        assert 'data-testid="continue-learning-workshop"' not in body
        # The empty-state copy renders since this user has no other
        # in-progress items.
        assert 'No courses in progress yet' in body

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 4: Course-only behaviour is unchanged (regression guard).
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCourseOnlyBehaviourUnchanged:
    def test_course_only_user_sees_course_card_unchanged(
        self, browser, django_server,
    ):
        _clear_workshops_and_progress()

        from django.utils import timezone

        from accounts.models import User
        from content.models import (
            Course,
            Enrollment,
            Module,
            Unit,
            UserCourseProgress,
        )

        _create_user('main@test.com', tier_slug='main')

        course = Course.objects.create(
            title='AI Basics',
            slug='ai-basics',
            status='published',
        )
        module = Module.objects.create(
            course=course, title='Mod 1', slug='mod-1', sort_order=0,
        )
        unit_a = Unit.objects.create(
            module=module, title='U1', slug='u1', sort_order=0,
        )
        Unit.objects.create(
            module=module, title='U2', slug='u2', sort_order=1,
        )
        user = User.objects.get(email='main@test.com')
        Enrollment.objects.create(user=user, course=course)
        UserCourseProgress.objects.create(
            user=user, unit=unit_a, completed_at=timezone.now(),
        )
        connection.close()

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        body = page.content()

        # Course card present; workshop card absent (no completions).
        assert 'AI Basics' in body
        assert '1/2 units completed' in body
        assert 'data-testid="continue-learning-workshop"' not in body

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 5: Anonymous user does NOT see the button.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonymousCannotSeeButton:
    def test_anonymous_visitor_no_mark_completed_button(
        self, django_server, page,
    ):
        _clear_workshops_and_progress()
        # pages_required_level=0 so the body is visible to anon and we
        # are testing button gating, not page gating.
        workshop, pages = _create_three_page_workshop(
            pages_required_level=0,
        )
        page.goto(
            f'{django_server}/workshops/{workshop.slug}/tutorial/intro',
            wait_until='domcontentloaded',
        )
        body = page.content()
        # Body renders, button hidden.
        assert 'data-testid="page-body"' in body
        assert 'data-testid="mark-page-complete-btn"' not in body
