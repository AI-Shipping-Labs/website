"""Playwright E2E tests for the workshop-page Q&A surface (issue #305).

Covers the user-flow scenarios listed in the issue:
- Authenticated workshop reader posts a question and sees it appear.
- Anonymous visitor sees the sign-in prompt instead of the textarea.
- Free user gated below pages_required_level never sees the Q&A surface.
- Reader replies to another member's question.
- Reader upvotes a question; the count persists across reloads.
- Course unit Q&A still works after the partial extraction (regression).
- Course-unit and workshop-page comments are isolated by content_id.

Usage:
    uv run pytest playwright_tests/test_workshop_comments.py -v
"""

import datetime
import os
import uuid

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

# ----------------------------------------------------------------------
# Shared fixtures
# ----------------------------------------------------------------------


def _clear_workshops_and_courses():
    """Delete every workshop, page, course unit, and comment so each
    scenario starts from a known state."""
    from comments.models import Comment, CommentVote
    from content.models import Course, Module, Unit, Workshop, WorkshopPage
    from events.models import Event
    CommentVote.objects.all().delete()
    Comment.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_workshop_with_pages(
    slug='prod-agents',
    title='Production Agents',
    pages=10,
    pages_data=None,
):
    """Create a workshop with pages that all have a populated content_id.

    The sync pipeline derives a UUIDv5 per page in production; in tests
    we set a random UUID so the Q&A section renders.
    """
    from django.utils.text import slugify

    from content.models import Instructor, Workshop, WorkshopInstructor, WorkshopPage

    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        date=datetime.date(2026, 4, 21),
        status='published',
        landing_required_level=0,
        pages_required_level=pages,
        recording_required_level=20,
        description='Workshop description.',
    )
    instructor_name = 'Alexey'
    instructor, _ = Instructor.objects.get_or_create(
        instructor_id=slugify(instructor_name)[:200] or 'test-instructor',
        defaults={
            'name': instructor_name,
            'status': 'published',
        },
    )
    WorkshopInstructor.objects.get_or_create(
        workshop=workshop,
        instructor=instructor,
        defaults={'position': 0},
    )

    pages_data = pages_data or [
        ('intro', 'Introduction', '# Welcome'),
        ('setup', 'Setup', '## Step 1'),
    ]
    created = []
    for i, (s, t, body) in enumerate(pages_data, start=1):
        created.append(WorkshopPage.objects.create(
            workshop=workshop, slug=s, title=t,
            sort_order=i, body=body,
            content_id=uuid.uuid4(),
        ))

    connection.close()
    return workshop, created


def _create_course_with_unit(
    course_slug='intro-course',
    course_title='Intro Course',
    module_slug='m1',
    unit_slug='u1',
    unit_title='Unit One',
):
    """Create a published course with a single preview unit with a
    populated content_id, so any user (including anonymous and free)
    can read it."""
    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title=course_title, slug=course_slug, status='published',
    )
    module = Module.objects.create(
        course=course, title='Module 1', slug=module_slug, sort_order=1,
    )
    unit = Unit.objects.create(
        module=module,
        title=unit_title,
        slug=unit_slug,
        sort_order=1,
        is_preview=True,
        content_id=uuid.uuid4(),
        body='Unit body',
    )
    connection.close()
    return course, module, unit


# ----------------------------------------------------------------------
# Scenario 1: Authenticated reader asks a question on a workshop page.
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestWorkshopReaderAsksQuestion:
    def test_basic_user_posts_question_and_sees_it_in_list(
        self, browser, django_server,
    ):
        _clear_workshops_and_courses()
        _create_workshop_with_pages()
        _create_user('basic@test.com', tier_slug='basic', first_name='Bea')

        ctx = _auth_context(browser, 'basic@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/prod-agents/tutorial/intro',
            wait_until='networkidle',
        )

        # Section + count both visible.
        body = page.content()
        assert 'id="qa-section"' in body
        assert 'Questions &amp; Answers' in body
        assert page.locator('#qa-count').inner_text() == '0'

        # Post the question.
        page.locator('#qa-new-question').fill(
            'Why does the agent restart on cold boot?',
        )
        page.locator('#qa-post-btn').click()

        # Wait for the JS to reload the list with the new entry.
        page.wait_for_function(
            "document.getElementById('qa-count')."
            "textContent === '1'",
            timeout=5000,
        )
        body = page.content()
        assert 'Why does the agent restart on cold boot?' in body
        assert 'Bea' in body

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 2: Anonymous visitor sees the sign-in prompt.
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestAnonymousVisitorSeesSignInPrompt:
    def test_logged_out_visitor_on_open_workshop_page(
        self, django_server, page,
    ):
        _clear_workshops_and_courses()
        _create_workshop_with_pages(
            slug='intro-ws', title='Intro Workshop', pages=0,
            pages_data=[('welcome', 'Welcome', '# Welcome body')],
        )

        page.goto(
            f'{django_server}/workshops/intro-ws/tutorial/welcome',
            wait_until='domcontentloaded',
        )
        body = page.content()

        # Heading and sign-in link visible; textarea/button not rendered.
        assert 'id="qa-section"' in body
        assert 'Questions &amp; Answers' in body
        assert 'href="/accounts/login/"' in body
        assert 'to ask questions' in body
        assert 'id="qa-new-question"' not in body
        assert 'id="qa-post-btn"' not in body


# ----------------------------------------------------------------------
# Scenario 3: Free member gated below pages tier never sees Q&A.
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestGatedUserDoesNotSeeQA:
    def test_free_user_below_pages_tier_sees_paywall_not_qa(
        self, browser, django_server,
    ):
        _clear_workshops_and_courses()
        # pages_required_level=10 (Basic) — Free user is gated.
        _create_workshop_with_pages(
            slug='paid-ws', title='Paid Workshop', pages=10,
            pages_data=[('intro', 'Intro', 'body')],
        )
        _create_user('free@test.com', tier_slug='free')

        ctx = _auth_context(browser, 'free@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/paid-ws/tutorial/intro',
            wait_until='domcontentloaded',
        )
        body = page.content()

        assert 'data-testid="page-paywall"' in body
        assert 'id="qa-section"' not in body
        assert 'Questions &amp; Answers' not in body

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 4: Reader replies to another member's question.
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestReaderReplies:
    def test_user_b_replies_to_user_a_question(
        self, browser, django_server,
    ):
        _clear_workshops_and_courses()
        _, pages = _create_workshop_with_pages(
            slug='prod-agents',
            pages_data=[('intro', 'Intro', '# Body')],
        )
        intro_page = pages[0]

        # Pre-seed a question by user A.
        user_a = _create_user(
            'usera@test.com', tier_slug='basic', first_name='Anna',
        )
        _create_user('userb@test.com', tier_slug='basic', first_name='Bob')

        from comments.models import Comment
        Comment.objects.create(
            content_id=intro_page.content_id,
            user=user_a,
            body='How do I cache the model?',
        )
        connection.close()

        # User B logs in and replies.
        ctx = _auth_context(browser, 'userb@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/prod-agents/tutorial/intro',
            wait_until='networkidle',
        )

        # Wait for the existing question to render.
        page.wait_for_selector('.qa-reply-toggle', timeout=5000)
        page.locator('.qa-reply-toggle').first.click()
        page.locator(
            '.qa-reply-form:not(.hidden) textarea',
        ).fill('Try clearing the cache.')
        page.locator(
            '.qa-reply-form:not(.hidden) .qa-reply-btn',
        ).click()

        # JS reloads after reply post; wait for the new reply to appear.
        page.wait_for_function(
            "document.querySelectorAll('.qa-reply-toggle').length >= 1 && "
            "document.body.textContent.includes('Try clearing the cache.')",
            timeout=5000,
        )
        body = page.content()
        assert 'Try clearing the cache.' in body
        assert 'Bob' in body

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 5: Reader upvotes a question; count persists across reloads.
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestUpvotePersistsAcrossReload:
    def test_upvote_count_and_active_state_persist(
        self, browser, django_server,
    ):
        _clear_workshops_and_courses()
        _, pages = _create_workshop_with_pages(
            slug='prod-agents',
            pages_data=[('intro', 'Intro', '# Body')],
        )
        intro_page = pages[0]

        user_a = _create_user(
            'usera@test.com', tier_slug='basic', first_name='Anna',
        )
        _create_user('voter@test.com', tier_slug='basic', first_name='Vee')

        from comments.models import Comment
        Comment.objects.create(
            content_id=intro_page.content_id,
            user=user_a,
            body='Is this idempotent?',
        )
        connection.close()

        ctx = _auth_context(browser, 'voter@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/prod-agents/tutorial/intro',
            wait_until='networkidle',
        )

        page.wait_for_selector('.qa-vote-btn', timeout=10000)
        # Count is 0 before voting.
        assert (
            page.locator('.qa-vote-count').first.inner_text() == '0'
        )

        # Click and wait for the vote POST to complete before reloading
        # so the vote is persisted. The CommentVote row is what we're
        # really verifying — the in-place DOM patch is best-effort.
        with page.expect_response(
            lambda r: '/vote' in r.url and r.status == 200,
            timeout=10000,
        ):
            page.locator('.qa-vote-btn').first.click()

        # Reload — the server-side vote count + the user_voted flag drive
        # the rendered count and the active "text-accent" class. This is
        # the assertion that matters: the persisted vote shows up after
        # a fresh page load.
        page.reload(wait_until='networkidle')
        page.wait_for_function(
            "document.querySelector('.qa-vote-count') && "
            "document.querySelector('.qa-vote-count').textContent === '1'",
            timeout=10000,
        )
        assert 'text-accent' in (
            page.locator('.qa-vote-btn').first.get_attribute('class') or ''
        )

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 6: Course unit Q&A regression — partial extraction must
# leave the existing course-unit surface working.
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestCourseUnitQARegression:
    def test_user_posts_question_on_course_unit(
        self, browser, django_server,
    ):
        _clear_workshops_and_courses()
        _create_course_with_unit()
        _create_user(
            'student@test.com', tier_slug='basic', first_name='Stu',
        )

        ctx = _auth_context(browser, 'student@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/courses/intro-course/m1/u1',
            wait_until='networkidle',
        )

        body = page.content()
        assert 'id="qa-section"' in body
        assert 'Questions &amp; Answers' in body
        assert 'id="qa-new-question"' in body
        assert 'id="qa-post-btn"' in body

        # Count starts at 0.
        assert page.locator('#qa-count').inner_text() == '0'
        page.locator('#qa-new-question').fill('How do I install the deps?')
        page.locator('#qa-post-btn').click()

        page.wait_for_function(
            "document.getElementById('qa-count')."
            "textContent === '1'",
            timeout=5000,
        )
        body = page.content()
        assert 'How do I install the deps?' in body

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 7: Course unit and workshop page comments are isolated
# by content_id.
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestContentIsolationAcrossSurfaces:
    def test_unit_and_page_questions_dont_leak_between_surfaces(
        self, browser, django_server,
    ):
        _clear_workshops_and_courses()
        _, pages = _create_workshop_with_pages(
            slug='ws-iso',
            pages_data=[('intro', 'Intro', '# Body')],
        )
        intro_page = pages[0]
        _, _, unit = _create_course_with_unit(
            course_slug='c-iso', module_slug='m-iso', unit_slug='u-iso',
        )

        author = _create_user(
            'author@test.com', tier_slug='basic', first_name='Alex',
        )

        from comments.models import Comment
        Comment.objects.create(
            content_id=unit.content_id,
            user=author,
            body='UNIT_QUESTION_FINGERPRINT',
        )
        Comment.objects.create(
            content_id=intro_page.content_id,
            user=author,
            body='WORKSHOP_QUESTION_FINGERPRINT',
        )
        connection.close()

        # On the course unit, only the unit question is visible.
        ctx = _auth_context(browser, 'author@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/courses/c-iso/m-iso/u-iso',
            wait_until='networkidle',
        )
        page.wait_for_function(
            "document.getElementById('qa-count')."
            "textContent === '1'",
            timeout=5000,
        )
        body = page.content()
        assert 'UNIT_QUESTION_FINGERPRINT' in body
        assert 'WORKSHOP_QUESTION_FINGERPRINT' not in body

        # On the workshop page, only the workshop question is visible.
        page.goto(
            f'{django_server}/workshops/ws-iso/tutorial/intro',
            wait_until='networkidle',
        )
        page.wait_for_function(
            "document.getElementById('qa-count')."
            "textContent === '1'",
            timeout=5000,
        )
        body = page.content()
        assert 'WORKSHOP_QUESTION_FINGERPRINT' in body
        assert 'UNIT_QUESTION_FINGERPRINT' not in body

        ctx.close()
