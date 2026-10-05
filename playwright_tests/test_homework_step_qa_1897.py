"""Homework stepper Q&A threads bind to the current page (issue #1897).

A comment posted on one stepper step must appear only on that step's page:
question, learning-in-public, review, and intro (which keeps the unit's own
thread, including the pre-existing unit-thread comments) each own one Q&A
thread; the all-questions homework keeps one page-level thread.
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

QUESTION_BODY = 'I got 7944 for unstructured'
INTRO_BODY = 'Where do I find the notebook?'
UNIT_LEFTOVER_BODY = 'I had 7944 for unstructured and 8102 for structured'
LIP_BODY = 'Shared my notes at example.com'
REVIEW_BODY = 'Ready to submit'


def _learner_context(browser, email):
    context = auth_context(browser, email)
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'domain': '127.0.0.1', 'path': '/',
    }])
    return context


def _create_stepper_homework(*, with_learning_in_public=True):
    from django.db import connection
    from django.utils import timezone

    from comments.models import Comment
    from content.models import Course, Module, Unit
    from content.models.cohort import Cohort
    from content.models.homework import Homework, Question, QuestionType

    course = Course.objects.create(
        title='Stepper QA course', slug='stepper-qa-1897',
        status='published', required_level=0,
    )
    module = Module.objects.create(
        course=course, title='Foundation', slug='foundation',
    )
    today = timezone.now().date()
    Cohort.objects.create(
        course=course, name='Cohort 4',
        start_date=today - datetime.timedelta(days=2),
        end_date=today + datetime.timedelta(days=30),
    )
    content_id = uuid.uuid4()
    homework_markdown = (
        'Read this first.\n\n'
        '## Question 1. Unstructured\nHow many tokens?\n'
        '## Question 2. Structured\nAnd now?\n'
    )
    if with_learning_in_public:
        homework_markdown += (
            '## Learning in Public\nShare your progress if you like.\n'
        )
    unit = Unit.objects.create(
        module=module, title='Homework', slug='homework', kind='homework',
        content_id=content_id, homework=homework_markdown,
    )
    homework = Homework.objects.create(
        cohort=Cohort.objects.get(course=course), slug='homework',
        title='Homework', content_id=content_id, stepper_enabled=True,
        learning_in_public_cap=3 if with_learning_in_public else 0,
    )
    Question.objects.create(
        homework=homework, source_question_id='q1-first', text='First',
        question_type=QuestionType.FREE_FORM,
    )
    Question.objects.create(
        homework=homework, source_question_id='q2-second', text='Second',
        question_type=QuestionType.FREE_FORM,
    )
    # A pre-existing comment on the unit content_id: the spoiler-shaped
    # leftover that today leaks onto every step and after the fix must be
    # visible on intro only.
    create_user('qa-earlier@test.com')
    Comment.objects.create(
        content_id=content_id,
        user=_django_user('qa-earlier@test.com'),
        body=UNIT_LEFTOVER_BODY,
    )
    connection.close()
    return course, unit


def _create_lesson(course):
    from django.db import connection

    from content.models import Unit

    lesson = Unit.objects.create(
        module=course.modules.first(), title='Lesson', slug='lesson',
        kind='lesson', content_id=uuid.uuid4(), body='Lesson body.',
    )
    connection.close()
    return lesson


def _django_user(email):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.get(email=email)


def _post_question(page, body):
    page.locator('#qa-new-question').fill(body)
    page.locator('#qa-post-btn').click()


def _wait_for_count(page, count):
    page.wait_for_function(
        "document.getElementById('qa-count') && "
        "document.getElementById('qa-count').textContent === '%s'" % count,
        timeout=5000,
    )


def _step_url(server, unit, step):
    return f'{server}{unit.get_absolute_url()}/{step}'


@browser_journey
@pytest.mark.core
def test_question_comment_is_visible_only_on_its_step(
    django_server, browser,
):
    """Post on q1; it is absent on q2 and survives reload/navigation."""
    create_user('qa-member@test.com')
    _course, unit = _create_stepper_homework()

    context = _learner_context(browser, 'qa-member@test.com')
    page = context.new_page()

    page.goto(_step_url(django_server, unit, 'q1-first'),
              wait_until='networkidle')
    # The q1 thread starts empty: the unit-thread leftover must not ride
    # along on question pages.
    assert page.locator('#qa-count').inner_text() == '0'
    _post_question(page, QUESTION_BODY)
    _wait_for_count(page, '1')
    assert QUESTION_BODY in page.content()

    page.goto(_step_url(django_server, unit, 'q2-second'),
              wait_until='networkidle')
    _wait_for_count(page, '0')
    assert QUESTION_BODY not in page.content()

    # Reload and come back: the comment persists on its own step.
    page.goto(_step_url(django_server, unit, 'q1-first'),
              wait_until='networkidle')
    _wait_for_count(page, '1')
    assert QUESTION_BODY in page.content()

    context.close()


@browser_journey
@pytest.mark.core
def test_intro_discussion_stays_on_intro_with_unit_thread_leftovers(
    django_server, browser,
):
    """Intro mounts the unit thread: pre-existing unit comments show there
    and nowhere else; a fresh intro comment stays intro-only."""
    create_user('qa-intro@test.com')
    _course, unit = _create_stepper_homework()

    context = _learner_context(browser, 'qa-intro@test.com')
    page = context.new_page()

    page.goto(_step_url(django_server, unit, 'intro'),
              wait_until='networkidle')
    _wait_for_count(page, '1')
    assert UNIT_LEFTOVER_BODY in page.content()

    _post_question(page, INTRO_BODY)
    _wait_for_count(page, '2')

    page.goto(_step_url(django_server, unit, 'q1-first'),
              wait_until='networkidle')
    _wait_for_count(page, '0')
    assert INTRO_BODY not in page.content()
    assert UNIT_LEFTOVER_BODY not in page.content()

    page.goto(_step_url(django_server, unit, 'review'),
              wait_until='networkidle')
    _wait_for_count(page, '0')
    assert UNIT_LEFTOVER_BODY not in page.content()

    context.close()


@browser_journey
@pytest.mark.core
def test_learning_in_public_and_review_comments_stay_on_their_pages(
    django_server, browser,
):
    create_user('qa-lip@test.com')
    _course, unit = _create_stepper_homework()

    context = _learner_context(browser, 'qa-lip@test.com')
    page = context.new_page()

    page.goto(_step_url(django_server, unit, 'learning-in-public'),
              wait_until='networkidle')
    assert page.locator('#qa-count').inner_text() == '0'
    _post_question(page, LIP_BODY)
    _wait_for_count(page, '1')

    page.goto(_step_url(django_server, unit, 'review'),
              wait_until='networkidle')
    assert page.locator('#qa-count').inner_text() == '0'
    _post_question(page, REVIEW_BODY)
    _wait_for_count(page, '1')

    page.goto(_step_url(django_server, unit, 'q1-first'),
              wait_until='networkidle')
    assert REVIEW_BODY not in page.content()
    assert LIP_BODY not in page.content()

    page.goto(_step_url(django_server, unit, 'intro'),
              wait_until='networkidle')
    assert REVIEW_BODY not in page.content()
    assert LIP_BODY not in page.content()

    page.goto(_step_url(django_server, unit, 'learning-in-public'),
              wait_until='networkidle')
    _wait_for_count(page, '1')
    assert LIP_BODY in page.content()
    assert REVIEW_BODY not in page.content()

    context.close()


@browser_journey
@pytest.mark.core
def test_all_questions_homework_keeps_one_page_thread(
    django_server, browser,
):
    """Stepper off: one Q&A thread for the page, none per question."""
    create_user('qa-allinone@test.com')
    _course, unit = _create_stepper_homework(with_learning_in_public=False)

    from django.db import connection

    from content.models.homework import Homework

    homework = Homework.objects.get(
        content_id=unit.content_id, stepper_enabled=True,
    )
    homework.stepper_enabled = False
    homework.save(update_fields=['stepper_enabled'])
    connection.close()

    context = _learner_context(browser, 'qa-allinone@test.com')
    page = context.new_page()
    page.goto(f'{django_server}{unit.get_absolute_url()}',
              wait_until='networkidle')

    body = page.content()
    assert 'id="qa-section"' in body
    assert page.locator('.qa-thread').count() == 1
    _post_question(page, QUESTION_BODY)
    _wait_for_count(page, '1')

    context.close()


@browser_journey
@pytest.mark.core
def test_lesson_thread_does_not_mix_with_homework_steps(
    django_server, browser,
):
    create_user('qa-lesson@test.com')
    course, unit = _create_stepper_homework()
    lesson = _create_lesson(course)

    context = _learner_context(browser, 'qa-lesson@test.com')
    page = context.new_page()

    page.goto(f'{django_server}{lesson.get_absolute_url()}',
              wait_until='networkidle')
    assert page.locator('#qa-count').inner_text() == '0'
    _post_question(page, 'Where is the video notebook?')
    _wait_for_count(page, '1')

    page.goto(_step_url(django_server, unit, 'q1-first'),
              wait_until='networkidle')
    _wait_for_count(page, '0')
    assert 'Where is the video notebook?' not in page.content()

    page.goto(f'{django_server}{lesson.get_absolute_url()}',
              wait_until='networkidle')
    _wait_for_count(page, '1')
    assert QUESTION_BODY not in page.content()

    context.close()


@browser_journey
@pytest.mark.core
def test_instructor_notification_opens_the_step_page(
    django_server, browser,
):
    """A comment on q2 notifies the linked instructor with a bell URL that
    opens the q2 page, not the bare unit URL."""
    from django.db import connection

    from content.models import Instructor

    create_user('qa-instructor@test.com', is_staff=True)
    create_user('qa-asker@test.com')
    course, unit = _create_stepper_homework()

    instructor_model = Instructor.objects.create(
        instructor_id='qa-ada', name='Ada', status='published',
        user=_django_user('qa-instructor@test.com'),
    )
    course.instructors.add(instructor_model)
    connection.close()

    asker = _learner_context(browser, 'qa-asker@test.com')
    page = asker.new_page()
    page.goto(_step_url(django_server, unit, 'q2-second'),
              wait_until='networkidle')
    assert page.locator('#qa-count').inner_text() == '0'
    _post_question(page, 'What does unstructured mean here?')
    _wait_for_count(page, '1')
    asker.close()

    instructor = _learner_context(browser, 'qa-instructor@test.com')
    page = instructor.new_page()
    page.goto(f'{django_server}/notifications', wait_until='networkidle')
    expected_suffix = f'{unit.get_absolute_url()}/q2-second#qa-section'
    target = page.locator(
        f'[data-notification-target][href$="{expected_suffix}"]',
    )
    expect(target.first).to_be_visible()

    target.first.click()
    page.wait_for_url(f'**{expected_suffix}')
    expect(page.locator('#qa-section')).to_be_visible()
    page.wait_for_function(
        "document.getElementById('qa-count') && "
        "document.getElementById('qa-count').textContent === '1'",
        timeout=5000,
    )
    assert 'What does unstructured mean here?' in page.content()

    instructor.close()
