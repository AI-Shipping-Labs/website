"""Playwright E2E for content-author comment notifications (issue #1341).

When a member posts a comment or reply on a course unit lesson or a workshop
tutorial page, each distinct linked content author (``Instructor.user``) gets
an in-app ``content_comment`` notification deep-linking to the content's Q&A
section. The commenter is never notified about their own comment.

Covers the 8 groomed scenarios:

1. Course instructor alerted when a student asks a question on their lesson.
2. Workshop author hears about a reply on their tutorial page.
3. Author commenting on their own lesson is not notified about themselves.
4. Comment on content with no linked author posts cleanly (no notification).
5. Co-taught workshop notifies every linked author, but not the commenter.
6. Author who is also the commenter on co-taught content is skipped; the
   co-author is notified.
7. Operator links an instructor to an account; future comments start notifying.
8. Commenting on a sprint plan does not fire a content-author notification.

Usage:
    uv run pytest playwright_tests/test_content_comment_notifications.py -v
"""

import datetime
import os
import uuid

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Local-only: DB seeding + session-cookie injection. See testing-guidelines.
pytestmark = pytest.mark.local_only


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------


def _clear():
    """Reset the content + comment + notification surface between scenarios."""
    from comments.models import Comment, CommentVote
    from content.models import (
        Course,
        Instructor,
        Module,
        Unit,
        Workshop,
        WorkshopPage,
    )
    from notifications.models import Notification

    Notification.objects.all().delete()
    CommentVote.objects.all().delete()
    Comment.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    Instructor.objects.all().delete()
    connection.close()


def _make_instructor(instructor_id, name, user_email=None):
    from accounts.models import User
    from content.models import Instructor

    user = User.objects.get(email=user_email) if user_email else None
    inst = Instructor.objects.create(
        instructor_id=instructor_id, name=name, status='published', user=user,
    )
    connection.close()
    return inst


def _make_course_unit(instructor_ids=(), unit_title='Intro to ML'):
    """Preview unit (level 0) so any member can read + comment."""
    from content.models import Course, Instructor, Module, Unit

    course = Course.objects.create(
        title='ML Zoomcamp', slug='ml-zoomcamp', status='published',
    )
    module = Module.objects.create(
        course=course, title='Module 1', slug='module-1', sort_order=1,
    )
    unit = Unit.objects.create(
        module=module, title=unit_title, slug='intro', sort_order=1,
        is_preview=True, content_id=uuid.uuid4(), body='Lesson body',
    )
    for iid in instructor_ids:
        course.instructors.add(Instructor.objects.get(instructor_id=iid))
    connection.close()
    return course, module, unit


def _make_workshop_page(instructor_ids=(), page_title='Setup'):
    from content.models import Instructor, Workshop, WorkshopPage

    workshop = Workshop.objects.create(
        title='RAG Workshop', slug='rag-workshop',
        date=datetime.date(2026, 4, 21), status='published',
        landing_required_level=0, pages_required_level=0,
    )
    page = WorkshopPage.objects.create(
        workshop=workshop, slug='setup', title=page_title,
        sort_order=1, body='# Body', content_id=uuid.uuid4(),
    )
    for iid in instructor_ids:
        workshop.instructors.add(Instructor.objects.get(instructor_id=iid))
    connection.close()
    return workshop, page


def _seed_comment(content_id, user_email, body, parent=None):
    from accounts.models import User
    from comments.models import Comment

    comment = Comment.objects.create(
        content_id=content_id,
        user=User.objects.get(email=user_email),
        body=body,
        parent=parent,
    )
    connection.close()
    return comment


def _content_comment_count():
    from notifications.models import Notification

    n = Notification.objects.filter(notification_type='content_comment').count()
    connection.close()
    return n


def _post_question(page, django_server, url, text):
    page.goto(f'{django_server}{url}', wait_until='networkidle')
    page.locator('#qa-new-question').fill(text)
    page.locator('#qa-post-btn').click()
    page.wait_for_function(
        "document.getElementById('qa-count').textContent === '1'",
        timeout=8000,
    )


def _open_bell(page, django_server):
    """Open the bell dropdown on the homepage and return its inner text."""
    page.goto(f'{django_server}/', wait_until='domcontentloaded')
    page.locator('#notification-bell-btn').click()
    dropdown = page.locator('#notification-dropdown')
    dropdown.wait_for(state='visible', timeout=5000)
    page.wait_for_function(
        """() => {
            var list = document.getElementById('notification-list');
            return list && !list.textContent.includes('Loading');
        }""",
        timeout=10000,
    )
    return dropdown


# ----------------------------------------------------------------------
# Scenario 1: Course instructor alerted on a lesson question.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCourseInstructorAlerted:
    def test_instructor_gets_notification_and_it_links_to_qa(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_user('instructor@test.com', tier_slug='free')
        _create_user('main@test.com', tier_slug='basic', first_name='Mia')
        _make_instructor('ada', 'Ada', user_email='instructor@test.com')
        _make_course_unit(instructor_ids=['ada'])

        # main@test.com posts a question on the lesson.
        member_ctx = _auth_context(browser, 'main@test.com')
        member_page = member_ctx.new_page()
        _post_question(
            member_page, django_server,
            '/courses/ml-zoomcamp/module-1/intro',
            'How do I install the deps?',
        )
        assert 'How do I install the deps?' in member_page.content()
        member_ctx.close()

        # instructor@test.com sees the notification in the bell.
        inst_ctx = _auth_context(browser, 'instructor@test.com')
        inst_page = inst_ctx.new_page()
        dropdown = _open_bell(inst_page, django_server)
        assert 'New comment on Intro to ML' in dropdown.inner_text()

        # Clicking the notification lands on the unit's Q&A section.
        link = inst_page.locator(
            '#notification-list a[href="'
            '/courses/ml-zoomcamp/module-1/intro#qa-section"]'
        )
        assert link.count() >= 1
        link.first.click()
        inst_page.wait_for_url('**/courses/ml-zoomcamp/module-1/intro**')
        assert 'id="qa-section"' in inst_page.content()
        inst_ctx.close()


# ----------------------------------------------------------------------
# Scenario 2: Workshop author hears about a reply on their tutorial page.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestWorkshopAuthorRepliedTo:
    def test_reply_creates_reply_notification_linking_to_qa(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_user('instructor@test.com', tier_slug='free')
        _create_user('main@test.com', tier_slug='basic', first_name='Mia')
        _make_instructor('ada', 'Ada', user_email='instructor@test.com')
        _, ws_page = _make_workshop_page(instructor_ids=['ada'])

        # An existing top-level question from another member.
        _create_user('asker@test.com', tier_slug='basic', first_name='Asa')
        question = _seed_comment(
            ws_page.content_id, 'asker@test.com', 'Original question',
        )

        # main@test.com replies to it via the UI.
        member_ctx = _auth_context(browser, 'main@test.com')
        member_page = member_ctx.new_page()
        member_page.goto(
            f'{django_server}/workshops/rag-workshop/tutorial/setup',
            wait_until='networkidle',
        )
        member_page.wait_for_selector('.qa-reply-toggle', timeout=8000)
        member_page.locator('.qa-reply-toggle').first.click()
        member_page.locator(
            '.qa-reply-form:not(.hidden) textarea',
        ).fill('Here is a helpful reply.')
        member_page.locator(
            '.qa-reply-form:not(.hidden) .qa-reply-btn',
        ).click()
        member_page.wait_for_function(
            "document.body.textContent.includes('Here is a helpful reply.')",
            timeout=8000,
        )
        member_ctx.close()
        assert question is not None

        # instructor@test.com sees the reply notification on /notifications.
        inst_ctx = _auth_context(browser, 'instructor@test.com')
        inst_page = inst_ctx.new_page()
        inst_page.goto(
            f'{django_server}/notifications', wait_until='domcontentloaded',
        )
        target = inst_page.locator(
            '[data-notification-target]',
            has_text='New reply on Setup',
        )
        assert target.count() >= 1
        target.first.click()
        inst_page.wait_for_url('**/workshops/**/tutorial/setup**')
        assert 'id="qa-section"' in inst_page.content()
        inst_ctx.close()


# ----------------------------------------------------------------------
# Scenario 3: Author commenting on their own lesson is not notified.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestNoSelfNotify:
    def test_author_comment_on_own_unit_creates_no_notification(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_user('instructor@test.com', tier_slug='basic', first_name='Ada')
        _make_instructor('ada', 'Ada', user_email='instructor@test.com')
        _make_course_unit(instructor_ids=['ada'])

        ctx = _auth_context(browser, 'instructor@test.com')
        page = ctx.new_page()
        _post_question(
            page, django_server, '/courses/ml-zoomcamp/module-1/intro',
            'Testing my own lesson.',
        )

        # No badge should appear for the author's own comment.
        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        badge = page.locator('#notification-badge')
        page.wait_for_function(
            """() => {
                var b = document.getElementById('notification-badge');
                return b && b.classList.contains('hidden');
            }""",
            timeout=8000,
        )
        assert badge.evaluate("el => el.classList.contains('hidden')")
        assert _content_comment_count() == 0
        ctx.close()


# ----------------------------------------------------------------------
# Scenario 4: Comment on content with no linked author posts cleanly.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestNoLinkedAuthorPostsCleanly:
    def test_comment_saved_and_zero_notifications(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_user('main@test.com', tier_slug='basic', first_name='Mia')
        _make_instructor('unlinked', 'Unlinked', user_email=None)
        _make_course_unit(instructor_ids=['unlinked'])

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        _post_question(
            page, django_server, '/courses/ml-zoomcamp/module-1/intro',
            'Question with no author linked.',
        )
        # Saved, shown, no error page.
        assert 'Question with no author linked.' in page.content()
        assert _content_comment_count() == 0
        ctx.close()


# ----------------------------------------------------------------------
# Scenario 5: Co-taught workshop notifies every linked author, not the
#             commenter.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCoTaughtFanOut:
    def test_both_authors_notified_commenter_not(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_user('alice@test.com', tier_slug='free')
        _create_user('bob@test.com', tier_slug='free')
        _create_user('carol@test.com', tier_slug='basic', first_name='Carol')
        _make_instructor('alice', 'Alice', user_email='alice@test.com')
        _make_instructor('bob', 'Bob', user_email='bob@test.com')
        _make_workshop_page(instructor_ids=['alice', 'bob'])

        carol_ctx = _auth_context(browser, 'carol@test.com')
        carol_page = carol_ctx.new_page()
        _post_question(
            carol_page, django_server,
            '/workshops/rag-workshop/tutorial/setup',
            'A shared question for both instructors.',
        )
        carol_ctx.close()

        for email in ('alice@test.com', 'bob@test.com'):
            ctx = _auth_context(browser, email)
            page = ctx.new_page()
            dropdown = _open_bell(page, django_server)
            assert 'New comment on Setup' in dropdown.inner_text(), email
            ctx.close()

        # Carol (the commenter) has no such notification -> no badge.
        carol_ctx2 = _auth_context(browser, 'carol@test.com')
        carol_page2 = carol_ctx2.new_page()
        carol_page2.goto(f'{django_server}/', wait_until='domcontentloaded')
        carol_page2.wait_for_function(
            """() => {
                var b = document.getElementById('notification-badge');
                return b && b.classList.contains('hidden');
            }""",
            timeout=8000,
        )
        assert carol_page2.locator('#notification-badge').evaluate(
            "el => el.classList.contains('hidden')"
        )
        carol_ctx2.close()


# ----------------------------------------------------------------------
# Scenario 6: Author who is also the commenter on co-taught content is
#             skipped; the co-author is notified.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCoAuthorCommenterSkipped:
    def test_commenting_author_skipped_coauthor_notified(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_user('alice@test.com', tier_slug='basic', first_name='Alice')
        _create_user('bob@test.com', tier_slug='free')
        _make_instructor('alice', 'Alice', user_email='alice@test.com')
        _make_instructor('bob', 'Bob', user_email='bob@test.com')
        _make_workshop_page(instructor_ids=['alice', 'bob'])

        # Alice (an author) posts the question.
        alice_ctx = _auth_context(browser, 'alice@test.com')
        alice_page = alice_ctx.new_page()
        _post_question(
            alice_page, django_server,
            '/workshops/rag-workshop/tutorial/setup',
            'Alice asks her co-author.',
        )
        # Alice sees no badge for her own comment.
        alice_page.goto(f'{django_server}/', wait_until='domcontentloaded')
        alice_page.wait_for_function(
            """() => {
                var b = document.getElementById('notification-badge');
                return b && b.classList.contains('hidden');
            }""",
            timeout=8000,
        )
        assert alice_page.locator('#notification-badge').evaluate(
            "el => el.classList.contains('hidden')"
        )
        alice_ctx.close()

        # Bob (co-author) has exactly one notification.
        bob_ctx = _auth_context(browser, 'bob@test.com')
        bob_page = bob_ctx.new_page()
        dropdown = _open_bell(bob_page, django_server)
        assert 'New comment on Setup' in dropdown.inner_text()
        bob_ctx.close()

        from notifications.models import Notification
        assert Notification.objects.filter(
            notification_type='content_comment',
        ).count() == 1
        connection.close()


# ----------------------------------------------------------------------
# Scenario 7: Operator links an instructor to an account, then future
#             comments start notifying.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestOperatorLinksThenNotifies:
    def test_admin_link_enables_future_notifications(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_staff_user('admin@test.com')
        instructor_user = _create_user('instructor@test.com', tier_slug='free')
        _create_user('main@test.com', tier_slug='basic', first_name='Mia')
        inst = _make_instructor('ada', 'Ada', user_email=None)
        _make_course_unit(instructor_ids=['ada'])

        # Operator links the instructor to the account via Django admin.
        admin_ctx = _auth_context(browser, 'admin@test.com')
        admin_page = admin_ctx.new_page()
        admin_page.goto(
            f'{django_server}/admin/content/instructor/{inst.pk}/change/',
            wait_until='domcontentloaded',
        )
        # raw_id_fields renders a plain text input for the user pk.
        admin_page.locator('#id_user').fill(str(instructor_user.pk))
        admin_page.locator('input[name="_save"]').click()
        admin_page.wait_for_url('**/admin/content/instructor/**')
        admin_ctx.close()

        # A new comment now notifies the freshly-linked instructor.
        member_ctx = _auth_context(browser, 'main@test.com')
        member_page = member_ctx.new_page()
        _post_question(
            member_page, django_server,
            '/courses/ml-zoomcamp/module-1/intro',
            'Now that the account is linked.',
        )
        member_ctx.close()

        inst_ctx = _auth_context(browser, 'instructor@test.com')
        inst_page = inst_ctx.new_page()
        dropdown = _open_bell(inst_page, django_server)
        assert 'New comment on Intro to ML' in dropdown.inner_text()
        inst_ctx.close()


# ----------------------------------------------------------------------
# Scenario 8: Commenting on a sprint plan does not fire a content-author
#             notification.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPlanThreadExcluded:
    def test_plan_comment_creates_no_content_comment_notification(
        self, django_server, browser,
    ):
        from django.utils import timezone

        from accounts.models import User
        from plans.models import Plan, Sprint, SprintEnrollment

        _ensure_tiers()
        _clear()
        from comments.models import Comment
        from notifications.models import Notification
        Comment.objects.all().delete()
        Notification.objects.all().delete()
        Plan.objects.all().delete()
        Sprint.objects.all().delete()
        connection.close()

        _create_user('main@test.com', tier_slug='free', first_name='Mia')
        owner = User.objects.get(email='main@test.com')
        sprint = Sprint.objects.create(
            name='Current Sprint', slug='may-2026',
            start_date=timezone.localdate() - datetime.timedelta(days=1),
        )
        SprintEnrollment.objects.get_or_create(sprint=sprint, user=owner)
        plan = Plan.objects.create(
            member=owner, sprint=sprint, visibility='cohort',
        )
        content_id = str(plan.comment_content_id)
        plan_id = plan.pk
        connection.close()

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/sprints/may-2026/plan/{plan_id}',
            wait_until='domcontentloaded',
        )
        section = page.locator('[data-testid="plan-comments-section"]')
        section.locator('#qa-new-question').fill('A plan discussion note')
        with page.expect_response(
            lambda r: f'/api/comments/{content_id}' in r.url
            and r.request.method == 'POST'
        ) as resp_info:
            section.locator('#qa-post-btn').click()
        assert resp_info.value.status == 201

        # Comment saved, but no content_comment notification anywhere.
        assert _content_comment_count() == 0
        ctx.close()
