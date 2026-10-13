"""Learner journeys for canonical homework step path URLs (#1827).

Every active stepped homework step lives at
``/courses/<course>/<unit-route>/<step>``: the bare Unit URL opens the
learner's entry step, old ``?homework_step=`` bookmarks redirect, and the
content header no longer repeats a "Step N of M" counter.
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

SCREENSHOT_DIR = '.tmp/homework-step-paths-1827'
STEP_COUNTER = re.compile(r'Step \d+ of \d+')


def _context(browser, email):
    context = auth_context(browser, email)
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'domain': '127.0.0.1', 'path': '/',
    }])
    return context


def _create_homework(learner_email):
    """A dated cohort homework with two choice questions and an enrolled learner."""
    from django.db import connection
    from django.utils import timezone

    from content.models import Cohort, CohortEnrollment, Course, Module, Unit
    from content.models.homework import Homework, Question, QuestionType

    course = Course.objects.create(
        title='Homework paths', slug='homework-paths-1827',
        status='published', required_level=0,
    )
    module = Module.objects.create(course=course, title='Week 1', slug='week-1')
    today = timezone.now().date()
    cohort = Cohort.objects.create(
        course=course, name='Cohort 4', external_key='cohort-4',
        start_date=today - datetime.timedelta(days=5),
        end_date=today + datetime.timedelta(days=60),
    )
    unit = Unit.objects.create(
        module=module, title='Homework', slug='homework', kind='homework',
        content_id=uuid.uuid4(),
        homework=(
            'Read this first.\n\n## Question 1. First\nChoose an answer.\n'
            '## Question 2. Second\nChoose another answer.'
        ),
    )
    homework = Homework.objects.create(
        cohort=cohort, slug='homework', title='Homework', content_id=unit.content_id,
        due_date=timezone.now() + datetime.timedelta(days=7), stepper_enabled=True,
    )
    Question.objects.create(
        homework=homework, source_question_id='q1-first', text='First',
        question_type=QuestionType.MULTIPLE_CHOICE, possible_answers='Alpha\nBeta',
        correct_answer='1',
    )
    Question.objects.create(
        homework=homework, source_question_id='q2-second', text='Second',
        question_type=QuestionType.MULTIPLE_CHOICE, possible_answers='Yes\nNo',
        correct_answer='1',
    )
    learner = create_user(learner_email)
    CohortEnrollment.objects.create(cohort=cohort, user=learner)
    connection.close()
    return {'unit': unit, 'homework': homework, 'learner': learner}


def _save_draft(data, answers):
    from community_base.homework_steps.models import HomeworkDraft
    from django.db import connection

    from content.services.homework_step_reader import assignment_key

    HomeworkDraft.objects.update_or_create(
        user=data['learner'], assignment_key=assignment_key(data['homework']),
        defaults={'answers': answers, 'revision': 1},
    )
    connection.close()


def _drafts(data):
    from community_base.homework_steps.models import HomeworkDraft

    return list(HomeworkDraft.objects.filter(user=data['learner']))


def _stepper_hrefs(page):
    return page.get_by_test_id('homework-stepper').locator('a[href]').evaluate_all(
        'links => links.map(link => link.getAttribute("href"))',
    ) + page.get_by_test_id('homework-step-nav').locator('a[href]').evaluate_all(
        'links => links.map(link => link.getAttribute("href"))',
    )


def _assert_no_step_query(page):
    assert 'homework_step' not in page.url, page.url
    for href in _stepper_hrefs(page):
        assert 'homework_step' not in href, href


@pytest.mark.core
@browser_journey
def test_first_time_learner_starts_homework_at_a_clean_path_url(django_server, browser):
    email = 'paths-1827-first@test.com'
    data = _create_homework(email)
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    page.goto(unit_url, wait_until='domcontentloaded')

    expect(page).to_have_url(f'{unit_url}/intro')
    expect(page.get_by_test_id('homework-introduction')).to_contain_text('Read this first.')
    expect(page.get_by_test_id('homework-due-date')).to_be_visible()
    expect(page.get_by_test_id('homework-stepper')).not_to_contain_text(STEP_COUNTER)
    _assert_no_step_query(page)
    page.screenshot(path=f'{SCREENSHOT_DIR}/intro-1280-light.png', full_page=True)

    page.get_by_role('link', name='Start questions').click()

    expect(page).to_have_url(f'{unit_url}/q1-first')
    expect(page.get_by_test_id('homework-due-date')).to_be_visible()
    expect(page.get_by_test_id('homework-stepper')).not_to_contain_text(STEP_COUNTER)
    _assert_no_step_query(page)
    page.screenshot(path=f'{SCREENSHOT_DIR}/q1-1280-light.png', full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(page.get_by_test_id('homework-step-pills')).to_be_visible()
    expect(page.get_by_test_id('homework-step-pills')).not_to_contain_text(STEP_COUNTER)
    page.screenshot(path=f'{SCREENSHOT_DIR}/q1-390-light.png', full_page=True)
    page.evaluate("localStorage.setItem('theme', 'dark')")
    page.reload(wait_until='domcontentloaded')
    expect(page.locator('html')).to_have_class(re.compile(r'\bdark\b'))
    page.screenshot(path=f'{SCREENSHOT_DIR}/q1-390-dark.png', full_page=True)
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.screenshot(path=f'{SCREENSHOT_DIR}/q1-1280-dark.png', full_page=True)
    context.close()


@browser_journey
def test_returning_learner_resumes_the_existing_draft(django_server, browser):
    from content.services.homework_step_reader import option_key

    email = 'paths-1827-resume@test.com'
    data = _create_homework(email)
    _save_draft(data, {'q1-first': option_key('Beta')})
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    page.goto(unit_url, wait_until='domcontentloaded')

    expect(page).to_have_url(f'{unit_url}/q2-second')
    page.get_by_role('group', name='Homework steps').get_by_role(
        'link', name=re.compile(r'^Question 1'),
    ).click()
    expect(page).to_have_url(f'{unit_url}/q1-first')
    expect(page.get_by_role('radio', name='Beta')).to_be_checked()
    drafts = _drafts(data)
    assert len(drafts) == 1
    assert drafts[0].answers == {'q1-first': option_key('Beta')}
    context.close()


@browser_journey
def test_learner_moves_between_path_steps_without_losing_an_answer(django_server, browser):
    email = 'paths-1827-moves@test.com'
    data = _create_homework(email)
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()
    page.goto(f'{unit_url}/q1-first', wait_until='domcontentloaded')

    page.get_by_role('radio', name='Alpha').check()
    page.get_by_role('button', name='Save & continue').click()
    expect(page).to_have_url(f'{unit_url}/q2-second')
    page.get_by_role('button', name='Previous').click()
    expect(page).to_have_url(f'{unit_url}/q1-first')
    expect(page.get_by_role('radio', name='Alpha')).to_be_checked()

    page.get_by_role('radio', name='Beta').check()
    steps = page.get_by_role('group', name='Homework steps')
    steps.get_by_role('link', name=re.compile(r'^Question 2')).click()
    expect(page).to_have_url(f'{unit_url}/q2-second')
    steps.get_by_role('link', name=re.compile(r'^Question 1')).click()
    expect(page).to_have_url(f'{unit_url}/q1-first')
    expect(page.get_by_role('radio', name='Beta')).to_be_checked()
    _assert_no_step_query(page)
    context.close()


@browser_journey
def test_cohort_learner_keeps_cohort_context_across_every_step(django_server, browser):
    email = 'paths-1827-cohort@test.com'
    data = _create_homework(email)
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    page.goto(f'{unit_url}?cohort=cohort-4', wait_until='domcontentloaded')
    expect(page).to_have_url(f'{unit_url}/intro?cohort=cohort-4')
    page.get_by_role('link', name='Start questions').click()
    expect(page).to_have_url(f'{unit_url}/q1-first?cohort=cohort-4')
    page.get_by_role('radio', name='Alpha').check()
    page.get_by_role('button', name='Save & continue').click()
    expect(page).to_have_url(f'{unit_url}/q2-second?cohort=cohort-4')
    page.get_by_role('radio', name='Yes').check()
    page.get_by_role('button', name='Save & review').click()

    expect(page).to_have_url(f'{unit_url}/review?cohort=cohort-4')
    expect(page.get_by_test_id('homework-due-date')).to_be_visible()
    for href in _stepper_hrefs(page):
        if href.startswith(data['unit'].get_absolute_url() + '/'):
            assert href.endswith('?cohort=cohort-4'), href
    _assert_no_step_query(page)
    drafts = _drafts(data)
    assert len(drafts) == 1
    assert drafts[0].assignment_key == f'aisl:homework:{data["homework"].pk}'
    context.close()


@browser_journey
def test_learner_submits_and_updates_at_the_review_path(django_server, browser):
    from content.models.homework import Submission
    from content.services import completion as completion_service

    email = 'paths-1827-submit@test.com'
    data = _create_homework(email)
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()
    page.goto(f'{unit_url}/q1-first', wait_until='domcontentloaded')
    page.get_by_role('radio', name='Beta').check()
    page.get_by_role('button', name='Save & continue').click()
    page.get_by_role('radio', name='Yes').check()
    page.get_by_role('button', name='Save & review').click()
    expect(page).to_have_url(f'{unit_url}/review')
    page.get_by_label('Homework URL (required)').fill('https://github.com/example/one')
    page.get_by_role('button', name='Submit homework').click()

    expect(page).to_have_url(re.compile(re.escape(f'{unit_url}/review') + r'\?receipt=.+'))
    expect(page.get_by_test_id('homework-submitted-status')).to_be_visible()
    assert completion_service.is_completed(data['learner'], data['unit'])

    page.goto(unit_url, wait_until='domcontentloaded')
    expect(page).to_have_url(f'{unit_url}/review')
    page.get_by_label('Homework URL (required)').fill('https://github.com/example/two')
    page.get_by_role('button', name='Update submission').click()

    expect(page).to_have_url(re.compile(re.escape(f'{unit_url}/review') + r'\?receipt=.+'))
    expect(page.get_by_test_id('homework-submitted-status')).to_be_visible()
    submissions = list(Submission.objects.filter(
        homework=data['homework'], student=data['learner'],
    ))
    assert len(submissions) == 1
    assert submissions[0].homework_link == 'https://github.com/example/two'
    assert submissions[0].answers.count() == 2
    context.close()


@pytest.mark.core
@browser_journey
def test_learner_follows_a_previously_copied_query_url(django_server, browser):
    from content.services.homework_step_reader import option_key

    email = 'paths-1827-bookmark@test.com'
    data = _create_homework(email)
    _save_draft(data, {'q1-first': option_key('Beta')})
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    page.goto(f'{unit_url}?homework_step=q1-first&cohort=cohort-4', wait_until='domcontentloaded')

    expect(page).to_have_url(f'{unit_url}/q1-first?cohort=cohort-4')
    expect(page.get_by_role('radio', name='Beta')).to_be_checked()
    _assert_no_step_query(page)
    context.close()


@browser_journey
def test_learner_recovers_after_an_authored_question_changes(django_server, browser):
    from content.services.homework_step_reader import option_key

    email = 'paths-1827-changed@test.com'
    data = _create_homework(email)
    _save_draft(data, {'q1-first': option_key('Alpha'), 'q0-removed': 'Old answer'})
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    for old_url in (f'{unit_url}?homework_step=q0-removed', f'{unit_url}/q0-removed'):
        page.goto(old_url, wait_until='domcontentloaded')
        expect(page).to_have_url(f'{unit_url}/q2-second?notice=changed')
        expect(page.get_by_text(
            'That question changed. Your saved answers are still here.',
        )).to_be_visible()

    drafts = _drafts(data)
    assert len(drafts) == 1
    assert drafts[0].answers == {'q1-first': option_key('Alpha'), 'q0-removed': 'Old answer'}
    context.close()


@browser_journey
def test_path_dispatch_does_not_steal_another_course_route(django_server, browser):
    from django.db import connection
    from django.utils import timezone

    from content.models import Cohort, CohortEnrollment, Course, Module, Unit
    from content.models.homework import AnswerType, Homework, Question, QuestionType

    email = 'paths-1827-routes@test.com'
    data = _create_homework(email)
    course = Course.objects.create(
        title='AI Buildcamp', slug='ai-buildcamp', status='published', required_level=0,
    )
    week = Module.objects.create(course=course, title='Week 1', slug='week-1', sort_order=1)
    wrapper = Module.objects.create(
        course=course, parent=week, title='Homework', slug='homework', sort_order=1,
    )
    nested_homework = Unit.objects.create(
        module=wrapper, title='Week 1 homework', slug='week-1-homework', sort_order=1,
        kind='homework', content_id=uuid.uuid4(),
        homework='Intro.\n\n## Question 1. Overlap\nAnswer it.',
    )
    Unit.objects.create(
        module=wrapper, title='Overlapping lesson', slug='q1-overlap', sort_order=2,
        kind='lesson', body='The real nested lesson.',
    )
    cohort = Cohort.objects.create(
        course=course, name='Cohort 1',
        start_date=timezone.localdate() - datetime.timedelta(days=2),
        end_date=timezone.localdate() + datetime.timedelta(days=30),
    )
    stepped = Homework.objects.create(
        cohort=cohort, slug='week-1-homework', title='Week 1 homework',
        content_id=nested_homework.content_id, stepper_enabled=True,
    )
    Question.objects.create(
        homework=stepped, source_question_id='q1-overlap', text='Answer it.',
        question_type=QuestionType.FREE_FORM, answer_type=AnswerType.ANY,
    )
    CohortEnrollment.objects.create(cohort=cohort, user=data['learner'])
    lesson = Unit.objects.create(
        module=data['unit'].module, title='Plain lesson', slug='plain-lesson',
        sort_order=5, body='Plain lesson body.',
    )
    connection.close()
    homework_url = f'{django_server}{nested_homework.get_absolute_url()}'

    context = _context(browser, email)
    page = context.new_page()
    page.goto(f'{homework_url}/q1-overlap', wait_until='domcontentloaded')
    expect(page.get_by_text('The real nested lesson.')).to_be_visible()
    expect(page.get_by_test_id('homework-stepper')).to_have_count(0)
    page.goto(f'{homework_url}/intro', wait_until='domcontentloaded')
    expect(page.get_by_test_id('homework-introduction')).to_be_visible()
    lesson_url = f'{django_server}{lesson.get_absolute_url()}'
    page.goto(lesson_url, wait_until='domcontentloaded')
    expect(page).to_have_url(lesson_url)
    expect(page.get_by_text('Plain lesson body.')).to_be_visible()
    context.close()

    anonymous = browser.new_context()
    visitor = anonymous.new_page()
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    response = visitor.goto(f'{unit_url}/q1-first', wait_until='domcontentloaded')
    expect(visitor.get_by_test_id('homework-stepper')).to_have_count(0)
    expect(visitor.get_by_text('Cohort 4')).to_have_count(0)
    unknown = visitor.goto(f'{unit_url}/q0-removed', wait_until='domcontentloaded')
    assert unknown.status == 404
    assert response.status in (200, 403)
    anonymous.close()
