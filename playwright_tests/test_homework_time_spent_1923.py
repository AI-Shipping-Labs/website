"""Homework time-spent fields stay empty unless the learner enters hours (#1923).

A blank field means "not provided" and is stored as NULL; a deliberate 0 is
stored as 0 and shown back as ``0``. Formatter, parser and migration edge
cases are authoritative as Django tests in
``content/tests/test_homework_time_spent_1923.py``; these journeys cover the
learner-visible review step.
"""

import datetime
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

LECTURES = 'Time spent on lectures (hours) (optional)'
HOMEWORK = 'Time spent on homework (hours) (optional)'
HOMEWORK_URL = 'Homework URL (required)'


def _create_assignment(email):
    from django.db import connection
    from django.utils import timezone

    from content.models import Course, Module, Unit
    from content.models.cohort import Cohort
    from content.models.homework import Homework, Question, QuestionType

    create_user(email)
    course = Course.objects.create(
        title='Time spent form test', slug='time-spent-form-1923',
        status='published', required_level=0,
    )
    module = Module.objects.create(course=course, title='Module 1', slug='module-1')
    today = timezone.now().date()
    cohort = Cohort.objects.create(
        course=course, name='Time spent cohort',
        start_date=today - datetime.timedelta(days=1),
        end_date=today + datetime.timedelta(days=30),
    )
    content_id = uuid.uuid4()
    unit = Unit.objects.create(
        module=module, title='Time spent homework', slug='time-spent-homework',
        kind='homework', content_id=content_id,
        homework='Do the work.\n\n## Question 1. Project\nDescribe your project.',
    )
    homework = Homework.objects.create(
        cohort=cohort, slug='time-spent-homework', title='Time spent homework',
        content_id=content_id,
        due_date=timezone.now() + datetime.timedelta(days=7),
        stepper_enabled=True,
        homework_url_field=True,
        time_spent_lectures_field=True,
        time_spent_homework_field=True,
    )
    Question.objects.create(
        homework=homework, source_question_id='q1-project', text='Describe your project.',
        question_type=QuestionType.FREE_FORM_LONG,
    )
    connection.close()
    return unit, homework


def _accept(email, homework, *, lectures, homework_hours):
    """Seed an accepted submission through the service."""
    from django.db import connection

    from accounts.models import User
    from content.services.homework_submissions import save_submission

    save_submission(
        homework, User.objects.get(email=email),
        homework_link='https://github.com/student/project',
        time_spent_lectures=lectures, time_spent_homework=homework_hours,
        answers_by_question_id={},
    )
    connection.close()


def _submission(email, homework):
    from accounts.models import User
    from content.models.homework import Submission

    return Submission.objects.get(homework=homework, student=User.objects.get(email=email))


def _open(browser, django_server, email, unit, step='review'):
    context = auth_context(browser, email)
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'domain': '127.0.0.1', 'path': '/',
    }])
    page = context.new_page()
    unit_url = f'{django_server}{unit.get_absolute_url()}'
    page.goto(f'{unit_url}/{step}' if step else unit_url, wait_until='domcontentloaded')
    return context, page, unit_url


def _submit(page, button='Submit homework'):
    # Wait for the post-submit receipt redirect: an already-submitted learner
    # sees the submitted status before the click, so it is not a sync point.
    page.get_by_role('button', name=button).click()
    expect(page).to_have_url(re.compile(r'/review\?receipt=.+'))
    expect(page.get_by_test_id('homework-submitted-status')).to_be_visible()


@pytest.mark.core
@browser_journey
def test_first_time_learner_sees_empty_time_spent_fields(django_server, browser):
    email = 'time-spent-first-1923@test.com'
    unit, _homework = _create_assignment(email)
    context, page, unit_url = _open(browser, django_server, email, unit, step=None)

    page.get_by_role('link', name='Start questions').click()
    expect(page).to_have_url(f'{unit_url}/q1-project')
    page.get_by_role('button', name='Save & review').click()
    expect(page).to_have_url(f'{unit_url}/review')

    expect(page.get_by_label(LECTURES)).to_have_value('')
    expect(page.get_by_label(HOMEWORK)).to_have_value('')
    expect(page.get_by_label(LECTURES)).to_have_attribute('autocomplete', 'off')
    page.screenshot(path='.tmp/homework-time-spent-1923-desktop.png', full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path='.tmp/homework-time-spent-1923-mobile.png', full_page=True)
    page.reload(wait_until='domcontentloaded')
    expect(page.get_by_label(LECTURES)).to_have_value('')
    expect(page.get_by_label(HOMEWORK)).to_have_value('')
    context.close()


@pytest.mark.core
@browser_journey
def test_blank_time_spent_is_stored_as_not_provided(django_server, browser):
    email = 'time-spent-blank-1923@test.com'
    unit, homework = _create_assignment(email)
    context, page, _unit_url = _open(browser, django_server, email, unit)

    page.get_by_label(HOMEWORK_URL).fill('https://github.com/student/project')
    _submit(page)

    submission = _submission(email, homework)
    assert submission.time_spent_lectures is None
    assert submission.time_spent_homework is None
    page.reload(wait_until='domcontentloaded')
    expect(page.get_by_label(LECTURES)).to_have_value('')
    expect(page.get_by_label(HOMEWORK)).to_have_value('')
    context.close()


@pytest.mark.core
@browser_journey
def test_explicit_zero_is_stored_and_shown_as_zero(django_server, browser):
    email = 'time-spent-zero-1923@test.com'
    unit, homework = _create_assignment(email)
    context, page, unit_url = _open(browser, django_server, email, unit)

    page.get_by_label(LECTURES).fill('0')
    page.get_by_label(HOMEWORK).fill('3')
    page.get_by_label(HOMEWORK_URL).fill('https://github.com/student/project')
    _submit(page)

    submission = _submission(email, homework)
    assert submission.time_spent_lectures == 0.0
    assert submission.time_spent_homework == 3.0
    page.goto(f'{unit_url}/review', wait_until='domcontentloaded')
    expect(page.get_by_label(LECTURES)).to_have_value('0')
    expect(page.get_by_label(HOMEWORK)).to_have_value('3')
    expect(page.get_by_test_id('homework-pending-draft-status')).to_have_count(0)
    context.close()


@browser_journey
def test_fractional_hours_come_back_as_typed(django_server, browser):
    email = 'time-spent-fraction-1923@test.com'
    unit, homework = _create_assignment(email)
    context, page, unit_url = _open(browser, django_server, email, unit)

    page.get_by_label(LECTURES).fill('1.5')
    page.get_by_label(HOMEWORK).fill('0.25')
    page.get_by_label(HOMEWORK_URL).fill('https://github.com/student/project')
    _submit(page)

    submission = _submission(email, homework)
    assert (submission.time_spent_lectures, submission.time_spent_homework) == (1.5, 0.25)
    page.goto(f'{unit_url}/review', wait_until='domcontentloaded')
    expect(page.get_by_label(LECTURES)).to_have_value('1.5')
    expect(page.get_by_label(HOMEWORK)).to_have_value('0.25')
    context.close()


@browser_journey
def test_learner_clears_a_saved_time_estimate(django_server, browser):
    email = 'time-spent-clear-1923@test.com'
    unit, homework = _create_assignment(email)
    _accept(email, homework, lectures=None, homework_hours=2.0)
    context, page, _unit_url = _open(browser, django_server, email, unit)

    expect(page.get_by_label(HOMEWORK)).to_have_value('2')
    page.get_by_label(HOMEWORK).fill('')
    _submit(page, button='Update submission')

    assert _submission(email, homework).time_spent_homework is None
    page.reload(wait_until='domcontentloaded')
    expect(page.get_by_label(HOMEWORK)).to_have_value('')
    context.close()


@browser_journey
def test_draft_with_blank_time_spent_resumes_empty(django_server, browser):
    from content.models.homework import Submission

    email = 'time-spent-draft-1923@test.com'
    unit, homework = _create_assignment(email)
    context, page, unit_url = _open(browser, django_server, email, unit)

    page.get_by_label(HOMEWORK_URL).fill('https://github.com/student/draft')
    page.get_by_role('button', name='Save draft').click()
    expect(page).to_have_url(re.compile(re.escape(f'{unit_url}/review')))
    page.goto(f'{django_server}{unit.module.course.get_absolute_url()}', wait_until='domcontentloaded')
    page.goto(f'{unit_url}/review', wait_until='domcontentloaded')

    expect(page.get_by_label(HOMEWORK_URL)).to_have_value('https://github.com/student/draft')
    expect(page.get_by_label(LECTURES)).to_have_value('')
    expect(page.get_by_label(HOMEWORK)).to_have_value('')
    assert not Submission.objects.filter(homework=homework).exists()
    context.close()


@browser_journey
def test_negative_hours_are_rejected_then_blank_submits(django_server, browser):
    from content.models.homework import Submission

    email = 'time-spent-negative-1923@test.com'
    unit, homework = _create_assignment(email)
    context, page, unit_url = _open(browser, django_server, email, unit)

    page.get_by_label(LECTURES).fill('-1')
    page.get_by_label(HOMEWORK_URL).fill('https://github.com/student/project')
    page.get_by_role('button', name='Submit homework').click()

    expect(page).to_have_url(f'{unit_url}/review')
    expect(page.get_by_test_id('homework-submitted-status')).to_have_count(0)
    assert not Submission.objects.filter(homework=homework).exists()
    page.get_by_label(LECTURES).fill('')
    _submit(page)
    assert _submission(email, homework).time_spent_lectures is None
    context.close()


@browser_journey
def test_closed_homework_shows_no_answer_saved_not_zero(django_server, browser):
    from content.models.homework import HomeworkState

    email = 'time-spent-closed-1923@test.com'
    unit, homework = _create_assignment(email)
    _accept(email, homework, lectures=None, homework_hours=None)
    homework.state = HomeworkState.CLOSED
    homework.save(update_fields=['state'])
    context, page, _unit_url = _open(browser, django_server, email, unit)

    snapshot = page.get_by_test_id('homework-accepted-snapshot')
    expect(snapshot).to_be_visible()
    expect(snapshot.get_by_text(f'{LECTURES}: No answer saved')).to_be_visible()
    expect(snapshot.get_by_text(f'{HOMEWORK}: No answer saved')).to_be_visible()
    expect(page.get_by_label(LECTURES)).to_have_count(0)
    context.close()
