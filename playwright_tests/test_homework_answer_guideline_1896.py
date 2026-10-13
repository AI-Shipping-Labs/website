"""No-answer guideline while typing homework comments (issue #1896).

On a homework-kind unit the Q&A composer reveals a helper caption only
while the member's field is non-empty: the top-level composer and each
reply composer independently. It is a guideline, not a filter: posting
still succeeds. Lesson units never show it. Which threads opt in (workshop,
plan, Book Club, anonymous) is owned by
``comments/tests/test_answer_guideline_1896.py``.
"""

import datetime
import os
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

GUIDELINE = (
    "Don't share homework answers here. If you have a question, discuss it "
    "in general terms without revealing the answer."
)
OTHER_COMMENT = 'I had 7917 but chosen the closest option'


def _learner_context(browser, email):
    context = auth_context(browser, email)
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'domain': '127.0.0.1', 'path': '/',
    }])
    return context


def _create_course():
    """One stepper homework, one all-questions homework, and one lesson.

    The all-questions homework already has a comment from another member
    so a reply composer is available.
    """
    from django.contrib.auth import get_user_model
    from django.db import connection
    from django.utils import timezone

    from comments.models import Comment
    from content.models import Course, Module, Unit
    from content.models.cohort import Cohort
    from content.models.homework import Homework, Question, QuestionType

    course = Course.objects.create(
        title='Guideline course', slug='guideline-1896',
        status='published', required_level=0,
    )
    module = Module.objects.create(
        course=course, title='Foundation', slug='foundation',
    )
    today = timezone.now().date()
    cohort = Cohort.objects.create(
        course=course, name='Cohort 4', external_key='4',
        start_date=today - datetime.timedelta(days=2),
        end_date=today + datetime.timedelta(days=30),
    )
    markdown = (
        'Read this first.\n\n'
        '## Question 1. Unstructured\nHow many tokens?\n'
        '## Question 2. Structured\nAnd now?\n'
    )
    units = {}
    for slug, stepper in (('homework', True), ('homework-form', False)):
        unit = Unit.objects.create(
            module=module, title=slug.replace('-', ' ').capitalize(),
            slug=slug, kind='homework', content_id=uuid.uuid4(),
            homework=markdown,
        )
        homework = Homework.objects.create(
            cohort=cohort, slug=slug, title=unit.title,
            content_id=unit.content_id, stepper_enabled=stepper,
        )
        Question.objects.create(
            homework=homework, source_question_id='q1-first', text='First',
            question_type=QuestionType.FREE_FORM,
        )
        Question.objects.create(
            homework=homework, source_question_id='q2-second',
            text='Second', question_type=QuestionType.FREE_FORM,
        )
        units[slug] = unit
    units['lesson'] = Unit.objects.create(
        module=module, title='Lesson', slug='lesson', kind='lesson',
        content_id=uuid.uuid4(), body='Watch the video.',
    )

    create_user('other-1896@test.com')
    Comment.objects.create(
        content_id=units['homework-form'].content_id,
        user=get_user_model().objects.get(email='other-1896@test.com'),
        body=OTHER_COMMENT,
    )
    connection.close()
    return units


def _composer_caption(page):
    return page.locator('.qa-composer').first.get_by_test_id(
        'qa-answer-guideline',
    )


@browser_journey
@pytest.mark.core
def test_homework_learner_sees_guideline_only_while_typing_and_can_still_post(
    django_server, browser,
):
    create_user('learner-1896@test.com')
    units = _create_course()

    context = _learner_context(browser, 'learner-1896@test.com')
    page = context.new_page()
    page.goto(
        f"{django_server}{units['homework-form'].get_absolute_url()}",
        wait_until='networkidle',
    )
    composer = page.locator('#qa-new-question')
    post = page.get_by_role('button', name='Post question', exact=True)
    caption = _composer_caption(page)
    expect(page.locator('.qa-list').get_by_text(OTHER_COMMENT)).to_be_visible()

    # Idle, then focused-but-empty: no caption.
    expect(composer).to_have_attribute(
        'placeholder', 'Ask a question about this lesson...',
    )
    expect(caption).to_be_hidden()
    composer.focus()
    expect(caption).to_be_hidden()

    # Typing reveals the caption under this composer, inside a polite
    # status region; the post button stays available.
    composer.press_sequentially('I got 7944 for unstructured')
    expect(caption).to_be_visible()
    expect(caption).to_have_text(GUIDELINE)
    expect(page.locator('.qa-composer').first.get_by_role('status')).to_have_text(GUIDELINE)
    expect(post).to_be_enabled()

    # Deleting the draft hides it again.
    composer.press('ControlOrMeta+a')
    composer.press('Delete')
    expect(composer).to_have_value('')
    expect(caption).to_be_hidden()

    # A pasted single space counts as content.
    page.keyboard.insert_text(' ')
    expect(caption).to_be_visible()
    composer.fill('')
    expect(caption).to_be_hidden()

    # Guideline, not a filter: an answer-like body still posts, and the
    # emptied composer hides the caption.
    answer = 'i got Input tokens: 7,926 Output tokens: 296'
    composer.fill(answer)
    expect(caption).to_be_visible()
    post.click()
    expect(page.locator('.qa-list').get_by_text(answer, exact=True)).to_be_visible()
    expect(composer).to_have_value('')
    expect(caption).to_be_hidden()

    # Reply: opening an empty reply shows nothing; typing shows the caption
    # under the reply only, and the reply still posts.
    card = page.locator('.qa-card', has_text=OTHER_COMMENT)
    card.get_by_role('button', name='Reply', exact=True).click()
    reply_form = card.locator('.qa-reply-form')
    reply_box = reply_form.locator('textarea')
    reply_caption = reply_form.get_by_test_id('qa-answer-guideline')
    expect(reply_box).to_be_visible()
    expect(reply_caption).to_be_hidden()
    reply_box.press_sequentially('I chose 186 as the closest option')
    expect(reply_caption).to_be_visible()
    expect(reply_caption).to_have_text(GUIDELINE)
    expect(caption).to_be_hidden()
    reply_form.get_by_role('button', name='Post reply', exact=True).click()
    expect(
        page.locator('.qa-list').get_by_text(
            'I chose 186 as the closest option', exact=True,
        ),
    ).to_be_visible()
    expect(page.get_by_test_id('qa-answer-guideline').filter(visible=True)).to_have_count(0)

    context.close()


@browser_journey
@pytest.mark.core
def test_stepper_and_all_questions_homework_both_show_the_guideline(
    django_server, browser,
):
    create_user('stepper-1896@test.com')
    units = _create_course()

    context = _learner_context(browser, 'stepper-1896@test.com')
    page = context.new_page()
    question = 'What does unstructured mean here?'
    for url in (
        f"{units['homework'].get_absolute_url()}/q1-first",
        units['homework-form'].get_absolute_url(),
    ):
        page.goto(f'{django_server}{url}', wait_until='networkidle')
        caption = _composer_caption(page)
        expect(caption).to_be_hidden()
        page.locator('#qa-new-question').fill(question)
        expect(caption).to_be_visible()
        expect(caption).to_have_text(GUIDELINE)

    context.close()


@browser_journey
@pytest.mark.core
def test_lesson_learner_is_not_shown_the_homework_guideline(
    django_server, browser,
):
    create_user('lesson-1896@test.com')
    units = _create_course()

    context = _learner_context(browser, 'lesson-1896@test.com')
    page = context.new_page()
    page.goto(
        f"{django_server}{units['lesson'].get_absolute_url()}",
        wait_until='networkidle',
    )
    question = 'Where is the notebook for this video?'
    page.locator('#qa-new-question').fill(question)
    page.get_by_role('button', name='Post question', exact=True).click()
    expect(page.locator('.qa-list').get_by_text(question, exact=True)).to_be_visible()
    expect(page.get_by_text("Don't share homework answers here", exact=False)).to_have_count(0)
    expect(page.get_by_test_id('qa-answer-guideline')).to_have_count(0)

    context.close()
