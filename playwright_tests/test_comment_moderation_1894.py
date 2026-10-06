"""Staff moderation of public Q&A threads in the browser (issue #1894).

Covers the issue's Playwright scenarios against the shared
``comments/_qa_section.html`` + ``_qa_script.html`` surface on a regular
course unit thread (the same partial serves homework, lesson, workshop,
plan, and Book Club threads):

- Staff sees Edit/Delete (data-testid ``qa-edit``/``qa-delete``) next to
  Reply on every top-level comment and reply; members and authors never do.
- Staff Edit/Save rewrites the body, shows the muted ``Edited`` marker, and
  keeps the body inert plain text; Cancel and blank saves leave it alone.
- Staff Delete asks the documented confirm() question, hides the card on
  accept, updates the heading count, and keeps everything on dismiss.
- A hidden comment disappears for members on reload; hiding a reply keeps
  the parent visible.
"""

import os
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import create_session_for_user, create_user
from playwright_tests.test_workshop_comments import (
    _clear_workshops_and_courses,
    _create_course_with_unit,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = [pytest.mark.core, pytest.mark.local_only]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1894")

COURSE_SLUG = "buildcamp-mod-1894"
MODULE_SLUG = "foundation"
UNIT_SLUG = "homework"
UNIT_URL = f"/courses/{COURSE_SLUG}/{MODULE_SLUG}/{UNIT_SLUG}"

SPOILER = "I had 7944 for unstructured so I choose 186"
REPLY_LEAK = "I choose 186 as its the closest one"
DELETE_QUESTION = "Remove this comment from the thread?"
DELETE_WITH_REPLIES = (
    "Remove this comment from the thread? "
    "Replies will also leave the thread until the comment is restored."
)


def _auth_context(browser, email):
    session_key = create_session_for_user(email)
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    context.add_cookies(
        [
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
        ]
    )
    return context


def _seed_thread(*, top_body=SPOILER, reply_body=REPLY_LEAK):
    """Published course unit thread authored by a plain member.

    Returns ``(unit, top, reply)``; ``reply`` is ``None`` when
    ``reply_body`` is empty so each journey controls its own shape.
    """
    _, _, unit = _create_course_with_unit(
        course_slug=COURSE_SLUG,
        module_slug=MODULE_SLUG,
        unit_slug=UNIT_SLUG,
    )
    author = create_user(
        "spoiler-author-1894@test.com", tier_slug="basic", first_name="Mia",
    )
    create_user(
        "mod-staff-1894@test.com", tier_slug="basic", is_staff=True,
        first_name="Ops",
    )
    create_user(
        "thread-viewer-1894@test.com", tier_slug="basic", first_name="Vera",
    )

    from comments.models import Comment

    top = Comment.objects.create(
        content_id=unit.content_id, user=author, body=top_body,
    )
    reply = None
    if reply_body:
        reply = Comment.objects.create(
            content_id=unit.content_id, user=author, parent=top,
            body=reply_body,
        )
    connection.close()
    return unit, top, reply


def _add_top_level(unit, body):
    """One more member question on the same unit thread."""
    from django.contrib.auth import get_user_model

    from comments.models import Comment

    author = get_user_model().objects.get(email="spoiler-author-1894@test.com")
    comment = Comment.objects.create(
        content_id=unit.content_id, user=author, body=body,
    )
    connection.close()
    return comment


def _add_lesson_unit_with_comment(body):
    """A second, regular lesson unit (week-1 / intro) with one comment.

    The homework stepper never mounts for this course slug, so both units
    render the same shared Q&A partial on ordinary unit pages; this journey
    proves moderation on one content_id-keyed thread leaves the other
    untouched (issue #1894 scenario "not only homework").
    """
    from django.contrib.auth import get_user_model

    from comments.models import Comment
    from content.models import Course, Module, Unit

    course = Course.objects.get(slug=COURSE_SLUG)
    module = Module.objects.create(
        course=course, title="Week 1", slug="week-1", sort_order=2,
    )
    unit = Unit.objects.create(
        module=module,
        title="Intro",
        slug="intro",
        sort_order=1,
        is_preview=True,
        content_id=uuid.uuid4(),
        body="Lesson body",
    )
    author = get_user_model().objects.get(email="spoiler-author-1894@test.com")
    comment = Comment.objects.create(
        content_id=unit.content_id, user=author, body=body,
    )
    connection.close()
    return comment


def _open_thread(page, django_server):
    page.goto(
        f"{django_server}{UNIT_URL}",
        wait_until="domcontentloaded",
    )
    page.locator(".qa-card").first.wait_for(state="visible")


def _card(page, comment):
    return page.locator(f'.qa-card[data-comment-id="{comment.pk}"]')


def _card_button(page, test_id, comment):
    """One exact control for one card.

    A top-level card contains its nested reply cards, so scoping by card
    alone matches the reply's controls too; the buttons carry their own
    ``data-comment-id``.
    """
    return page.locator(
        f'[data-testid="{test_id}"][data-comment-id="{comment.pk}"]',
    )


def _card_body(card):
    """The card's own body paragraph, not a nested reply's."""
    return card.locator(".qa-body").first


def _card_marker(card):
    """The card's own Edited marker (it carries no comment id)."""
    return card.get_by_test_id("qa-edited")


def _confirm_dialog(page, trigger, *, accept):
    """Click ``trigger`` and answer the blocking confirm() dialog.

    The handler must be registered before the click: confirm() halts page
    JavaScript mid-click, so an expect_event-style click would deadlock.
    Returns the dialog message so tests assert the exact issue copy.
    """
    seen = {}

    def _on_dialog(dialog):
        seen['message'] = dialog.message
        if accept:
            dialog.accept()
        else:
            dialog.dismiss()

    page.once('dialog', _on_dialog)
    trigger.click()
    assert 'message' in seen, 'confirm() dialog never appeared'
    return seen['message']


def _capture(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"), full_page=True)


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_sees_edit_delete_on_every_card_and_member_does_not(
    browser,
    django_server,
):
    _clear_workshops_and_courses()
    _unit, top, reply = _seed_thread()

    staff_ctx = _auth_context(browser, "mod-staff-1894@test.com")
    staff_page = staff_ctx.new_page()
    try:
        _open_thread(staff_page, django_server)
        # One Edit and one Delete per card: the top-level comment and its reply.
        expect(staff_page.get_by_test_id("qa-edit")).to_have_count(2)
        expect(staff_page.get_by_test_id("qa-delete")).to_have_count(2)
        # Controls sit next to the existing Reply link, not in a new card.
        expect(_card_button(staff_page, "qa-edit", top)).to_be_visible()
        expect(_card_button(staff_page, "qa-edit", reply)).to_be_visible()
        expect(
            _card(staff_page, top).get_by_role("button", name="Reply"),
        ).to_be_visible()
        expect(staff_page.get_by_test_id("qa-edited")).to_have_count(0)
        _capture(staff_page, "staff-sees-moderation-controls")
    finally:
        staff_ctx.close()

    author_ctx = _auth_context(browser, "spoiler-author-1894@test.com")
    author_page = author_ctx.new_page()
    try:
        _open_thread(author_page, django_server)
        expect(author_page.get_by_test_id("qa-edit")).to_have_count(0)
        expect(author_page.get_by_test_id("qa-delete")).to_have_count(0)
        # Members keep reply and vote affordances.
        expect(
            _card(author_page, top).get_by_role("button", name="Reply"),
        ).to_be_visible()
    finally:
        author_ctx.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_edit_save_rewrites_body_and_shows_edited_marker(
    browser,
    django_server,
):
    _clear_workshops_and_courses()
    _unit, top, _reply = _seed_thread(reply_body=None)
    replacement = (
        "How should token counts be compared without posting the numbers? "
        "<script>window.pwned=true</script>"
    )

    ctx = _auth_context(browser, "mod-staff-1894@test.com")
    page = ctx.new_page()
    try:
        _open_thread(page, django_server)
        top_card = _card(page, top)

        _card_button(page, "qa-edit", top).click()
        textarea = top_card.locator(".qa-edit-textarea")
        expect(textarea).to_be_visible()
        assert textarea.input_value() == SPOILER

        textarea.fill(replacement)
        _card_button(page, "qa-save-edit", top).click()

        expect(_card_body(top_card)).to_have_text(replacement)
        expect(_card_marker(top_card)).to_have_text("Edited")
        expect(_card_button(page, "qa-edit", top)).to_be_visible()

        # The staff-written body renders as inert plain text.
        assert page.evaluate("window.pwned") is None
        assert top_card.locator("script").count() == 0

        page.reload(wait_until="domcontentloaded")
        top_card = _card(page, top)
        expect(_card_body(top_card)).to_have_text(replacement)
        expect(_card_marker(top_card)).to_be_visible()
        _capture(page, "staff-edited-body-with-marker")
    finally:
        ctx.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_cancel_and_blank_save_leave_original_body(
    browser,
    django_server,
):
    _clear_workshops_and_courses()
    _unit, top, _reply = _seed_thread(reply_body=None)

    ctx = _auth_context(browser, "mod-staff-1894@test.com")
    page = ctx.new_page()
    try:
        _open_thread(page, django_server)
        top_card = _card(page, top)

        _card_button(page, "qa-edit", top).click()
        top_card.locator(".qa-edit-textarea").fill("A rewrite nobody keeps")
        _card_button(page, "qa-cancel-edit", top).click()
        expect(_card_body(top_card)).to_have_text(SPOILER)
        expect(page.get_by_test_id("qa-edited")).to_have_count(0)

        # A whitespace-only save never persists: the server is never called,
        # so a reload still shows the original body without an Edited marker.
        _card_button(page, "qa-edit", top).click()
        top_card.locator(".qa-edit-textarea").fill("   ")
        _card_button(page, "qa-save-edit", top).click()
        page.wait_for_timeout(300)
        page.reload(wait_until="domcontentloaded")
        top_card = _card(page, top)
        expect(_card_body(top_card)).to_have_text(SPOILER)
        expect(page.get_by_test_id("qa-edited")).to_have_count(0)
    finally:
        ctx.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_delete_confirm_hides_card_updates_count_and_member_reload(
    browser,
    django_server,
):
    _clear_workshops_and_courses()
    unit, top, reply = _seed_thread()
    other = _add_top_level(unit, "I had 7917 but chosen the closest option")

    ctx = _auth_context(browser, "mod-staff-1894@test.com")
    page = ctx.new_page()
    try:
        _open_thread(page, django_server)
        count = page.locator(".qa-count")
        expect(count).to_have_text("2")

        message = _confirm_dialog(
            page,
            _card_button(page, "qa-delete", top),
            accept=True,
        )
        assert message == DELETE_WITH_REPLIES

        expect(_card(page, top)).to_have_count(0)
        expect(page.get_by_text(SPOILER)).to_have_count(0)
        expect(page.get_by_text(REPLY_LEAK)).to_have_count(0)
        expect(count).to_have_text("1")

        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_text(SPOILER)).to_have_count(0)
        expect(_card_body(_card(page, other))).to_have_text(
            "I had 7917 but chosen the closest option",
        )
        _capture(page, "staff-deleted-top-level-comment")
    finally:
        ctx.close()

    viewer_ctx = _auth_context(browser, "thread-viewer-1894@test.com")
    viewer_page = viewer_ctx.new_page()
    try:
        _open_thread(viewer_page, django_server)
        expect(viewer_page.get_by_text(SPOILER)).to_have_count(0)
        expect(viewer_page.get_by_text(REPLY_LEAK)).to_have_count(0)
        expect(_card(viewer_page, other)).to_be_visible()
        expect(viewer_page.get_by_test_id("qa-delete")).to_have_count(0)
    finally:
        viewer_ctx.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_delete_cancel_keeps_comment_untouched(
    browser,
    django_server,
):
    _clear_workshops_and_courses()
    _unit, top, _reply = _seed_thread(reply_body=None)

    ctx = _auth_context(browser, "mod-staff-1894@test.com")
    page = ctx.new_page()
    try:
        _open_thread(page, django_server)
        top_card = _card(page, top)
        message = _confirm_dialog(
            page,
            _card_button(page, "qa-delete", top),
            accept=False,
        )
        assert message == DELETE_QUESTION

        expect(_card_body(top_card)).to_have_text(SPOILER)
        expect(top_card).to_be_visible()
        expect(page.get_by_test_id("qa-edited")).to_have_count(0)
    finally:
        ctx.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_delete_reply_keeps_parent_visible(
    browser,
    django_server,
):
    _clear_workshops_and_courses()
    _unit, top, reply = _seed_thread()

    ctx = _auth_context(browser, "mod-staff-1894@test.com")
    page = ctx.new_page()
    try:
        _open_thread(page, django_server)
        reply_card = _card(page, reply)
        expect(_card_body(reply_card)).to_have_text(REPLY_LEAK)

        message = _confirm_dialog(
            page,
            _card_button(page, "qa-delete", reply),
            accept=True,
        )
        assert message == DELETE_QUESTION

        expect(_card(page, reply)).to_have_count(0)
        parent_card = _card(page, top)
        expect(_card_body(parent_card)).to_have_text(SPOILER)
        expect(_card_marker(parent_card)).to_have_count(0)
        _capture(page, "staff-deleted-reply-parent-stays")
    finally:
        ctx.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_moderates_lesson_thread_and_homework_thread_stays_intact(
    browser,
    django_server,
):
    """Issue #1894 scenario: the same Edit/Delete works on a regular lesson
    unit Q&A, and hiding there leaves the homework unit thread untouched."""
    _clear_workshops_and_courses()
    _unit, top, _reply = _seed_thread()
    lesson_comment = _add_lesson_unit_with_comment(
        "Paste this solution into the quiz",
    )
    lesson_url = f"{django_server}/courses/{COURSE_SLUG}/week-1/intro"

    ctx = _auth_context(browser, "mod-staff-1894@test.com")
    page = ctx.new_page()
    try:
        page.goto(lesson_url, wait_until="domcontentloaded")
        lesson_card = _card(page, lesson_comment)
        lesson_card.wait_for(state="visible")
        expect(_card_button(page, "qa-edit", lesson_comment)).to_be_visible()
        expect(_card_button(page, "qa-delete", lesson_comment)).to_be_visible()

        message = _confirm_dialog(
            page,
            _card_button(page, "qa-delete", lesson_comment),
            accept=True,
        )
        assert message == DELETE_QUESTION
        expect(page.get_by_text("Paste this solution into the quiz"))\
            .to_have_count(0)

        # The homework unit thread is a different content_id and keeps its
        # own comment; the hidden lesson body appears nowhere on it.
        page.goto(f"{django_server}{UNIT_URL}", wait_until="domcontentloaded")
        expect(_card_body(_card(page, top))).to_have_text(SPOILER)
        expect(
            page.get_by_text("Paste this solution into the quiz"),
        ).to_have_count(0)
        _capture(page, "staff-moderated-lesson-homework-intact")
    finally:
        ctx.close()
