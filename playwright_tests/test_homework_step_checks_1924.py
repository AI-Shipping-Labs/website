"""Learner journeys for answered-question checks in the homework step nav (#1924).

Each question step with a saved answer shows the green completion check in
the reader sidebar step list and a check glyph on the mobile step pill,
with ", answered" in its accessible name. Introduction and Review & submit
never get a check; submission state stays on the homework row status.
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

SCREENSHOT_DIR = '.tmp/homework-step-checks-1924'


def _context(browser, email):
    context = auth_context(browser, email)
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'domain': '127.0.0.1', 'path': '/',
    }])
    return context


def _create_homework(learner_email, *, public_link_cap=0):
    """A Buildcamp-shaped stepped homework: choice, text, choice questions,
    optionally a Learning in Public step, and an enrolled learner."""
    from django.db import connection
    from django.utils import timezone

    from content.models import Cohort, CohortEnrollment, Course, Module, Unit
    from content.models.homework import Homework, Question, QuestionType

    course = Course.objects.create(
        title='AI Engineering Buildcamp', slug='ai-buildcamp-1924',
        status='published', required_level=0, reader_navigation_scope='module',
    )
    week = Module.objects.create(
        course=course, title='Document Processing', slug='week-1', sort_order=1,
    )
    lesson = Unit.objects.create(
        module=week, title='From Idea to Submission', slug='idea-to-submission',
        sort_order=0, body='Read this lesson.',
    )
    today = timezone.now().date()
    cohort = Cohort.objects.create(
        course=course, name='Cohort 4', external_key='cohort-4',
        start_date=today - datetime.timedelta(days=5),
        end_date=today + datetime.timedelta(days=60),
    )
    body = (
        'Read this first.\n\n'
        '## Question 1. First\nChoose.\n\n'
        '## Question 2. Second\nDescribe your chunking.\n\n'
        '## Question 3. Third\nChoose.'
    )
    if public_link_cap:
        body += '\n\n## Learning in Public\nShare your work.'
    unit = Unit.objects.create(
        module=week, title='Homework: Document Processing with AI',
        slug='document-processing', sort_order=1, kind='homework',
        content_id=uuid.uuid4(), homework=body,
    )
    homework = Homework.objects.create(
        cohort=cohort, slug='document-processing', title=unit.title,
        content_id=unit.content_id, stepper_enabled=True,
        learning_in_public_cap=public_link_cap,
        due_date=timezone.now() + datetime.timedelta(days=7),
    )
    questions = [
        Question.objects.create(
            homework=homework, source_question_id='q1-first', text='Question 1',
            question_type=QuestionType.MULTIPLE_CHOICE,
            possible_answers='Alpha\nBeta', correct_answer='1',
        ),
        Question.objects.create(
            homework=homework, source_question_id='q2-second', text='Question 2',
            question_type=QuestionType.FREE_FORM,
        ),
        Question.objects.create(
            homework=homework, source_question_id='q3-third', text='Question 3',
            question_type=QuestionType.MULTIPLE_CHOICE,
            possible_answers='Alpha\nBeta', correct_answer='1',
        ),
    ]
    learner = create_user(learner_email)
    CohortEnrollment.objects.create(cohort=cohort, user=learner)
    connection.close()
    return {
        'course': course, 'lesson': lesson, 'unit': unit, 'homework': homework,
        'questions': questions, 'learner': learner,
    }


def _save_draft(data, answers):
    from community_base.homework_steps.models import HomeworkDraft
    from django.db import connection

    from content.services.homework_step_reader import assignment_key

    HomeworkDraft.objects.update_or_create(
        user=data['learner'], assignment_key=assignment_key(data['homework']),
        defaults={'answers': answers, 'revision': 1},
    )
    connection.close()


def _steps(page):
    return page.get_by_role('group', name='Homework steps')


def _step(page, name):
    return _steps(page).get_by_role('link', name=name, exact=True)


def _assert_answered(page, title):
    link = _step(page, f'{title}, answered')
    expect(link).to_have_attribute('data-homework-step-answered', 'true')
    expect(link.get_by_test_id('homework-step-answered')).to_have_count(1)
    expect(link.locator('svg.lucide-help-circle')).to_have_count(0)


def _assert_unanswered(page, title):
    link = _step(page, title)
    expect(link).to_have_count(1)
    expect(link).not_to_have_attribute('data-homework-step-answered', 'true')
    expect(link.get_by_test_id('homework-step-answered')).to_have_count(0)
    expect(link.locator('svg.lucide-help-circle')).to_have_count(1)


def _homework_row_status(page):
    return page.locator(
        '#sidebar-nav a.reader-list-row', has_text='Homework: Document Processing',
    ).first.get_by_test_id('homework-row-status')


def _set_theme(page, theme):
    page.evaluate("theme => localStorage.setItem('theme', theme)", theme)
    page.reload(wait_until='domcontentloaded')


@pytest.mark.core
@browser_journey
def test_learner_working_through_homework_sees_answered_questions_ticked_off(
    django_server, browser,
):
    email = 'checks-1924-progress@test.com'
    data = _create_homework(email)
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    page.goto(unit_url, wait_until='domcontentloaded')
    page.get_by_role('link', name='Start questions').click()
    expect(page).to_have_url(f'{unit_url}/q1-first')
    for title in ('Question 1', 'Question 2', 'Question 3'):
        _assert_unanswered(page, title)
    expect(_steps(page).locator('[data-homework-step-answered]')).to_have_count(0)

    page.get_by_role('radio', name='Alpha').check()
    page.get_by_role('button', name='Save & continue').click()
    expect(page).to_have_url(f'{unit_url}/q2-second')
    _assert_answered(page, 'Question 1')
    _assert_unanswered(page, 'Question 2')
    _assert_unanswered(page, 'Question 3')
    expect(_homework_row_status(page)).to_have_text('Draft')
    page.locator('#sidebar-nav').screenshot(path=f'{SCREENSHOT_DIR}/sidebar-desktop-light.png')
    _set_theme(page, 'dark')
    page.locator('#sidebar-nav').screenshot(path=f'{SCREENSHOT_DIR}/sidebar-desktop-dark.png')
    _set_theme(page, 'light')
    context.close()


@browser_journey
def test_sidebar_jump_autosaves_and_marks_the_answer(django_server, browser):
    email = 'checks-1924-autosave@test.com'
    data = _create_homework(email)
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    page.goto(f'{unit_url}/q2-second', wait_until='domcontentloaded')
    _assert_unanswered(page, 'Question 2')
    page.get_by_label('Your answer').fill('Chunking by headings')
    _step(page, 'Question 3').click()
    expect(page).to_have_url(f'{unit_url}/q3-third')
    _assert_answered(page, 'Question 2')

    _step(page, 'Question 2, answered').click()
    expect(page).to_have_url(f'{unit_url}/q2-second')
    expect(page.get_by_label('Your answer')).to_have_value('Chunking by headings')
    current = page.get_by_test_id('homework-step-current')
    expect(current).to_have_attribute('aria-current', 'step')
    expect(current).to_have_attribute('data-homework-step-answered', 'true')
    expect(current.get_by_test_id('homework-step-answered')).to_have_count(1)
    context.close()


@browser_journey
def test_skipped_question_stays_open_until_answered(django_server, browser):
    email = 'checks-1924-skip@test.com'
    data = _create_homework(email)
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    page.goto(f'{unit_url}/q1-first', wait_until='domcontentloaded')
    page.get_by_role('radio', name='Alpha').check()
    page.get_by_role('button', name='Save & continue').click()
    expect(page).to_have_url(f'{unit_url}/q2-second')
    page.get_by_role('button', name='Save & continue').click()
    expect(page).to_have_url(f'{unit_url}/q3-third')
    page.get_by_role('radio', name='Beta').check()
    page.get_by_role('button', name='Save & review').click()
    expect(page).to_have_url(f'{unit_url}/review')

    _assert_answered(page, 'Question 1')
    _assert_unanswered(page, 'Question 2')
    _assert_answered(page, 'Question 3')
    review = page.get_by_test_id('homework-review-summary')
    expect(review).to_contain_text('Alpha')
    expect(review).to_contain_text('Beta')

    _step(page, 'Question 2').click()
    expect(page).to_have_url(f'{unit_url}/q2-second')
    page.get_by_label('Your answer').fill('Fixed-size windows')
    page.get_by_role('button', name='Save & continue').click()
    expect(page).to_have_url(f'{unit_url}/q3-third')
    page.get_by_role('button', name='Save & review').click()
    expect(page).to_have_url(f'{unit_url}/review')
    for title in ('Question 1', 'Question 2', 'Question 3'):
        _assert_answered(page, title)
    expect(page.get_by_test_id('homework-review-summary')).to_contain_text('Fixed-size windows')
    context.close()


@browser_journey
def test_clearing_an_answer_removes_the_check(django_server, browser):
    from content.services.homework_step_reader import option_key

    email = 'checks-1924-clear@test.com'
    data = _create_homework(email)
    _save_draft(data, {'q1-first': option_key('Alpha'), 'q2-second': 'Semantic chunks'})
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    page.goto(f'{unit_url}/q2-second', wait_until='domcontentloaded')
    _assert_answered(page, 'Question 2')
    page.get_by_label('Your answer').fill('')
    page.get_by_role('button', name='Save & continue').click()
    expect(page).to_have_url(f'{unit_url}/q3-third')
    _assert_unanswered(page, 'Question 2')
    expect(_step(page, 'Question 2, answered')).to_have_count(0)
    _assert_answered(page, 'Question 1')
    context.close()


@browser_journey
def test_submitted_learner_still_sees_answered_questions_checked(django_server, browser):
    email = 'checks-1924-submitted@test.com'
    data = _create_homework(email)
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    page.goto(f'{unit_url}/q1-first', wait_until='domcontentloaded')
    page.get_by_role('radio', name='Alpha').check()
    page.get_by_role('button', name='Save & continue').click()
    page.get_by_label('Your answer').fill('By headings')
    page.get_by_role('button', name='Save & continue').click()
    page.get_by_role('radio', name='Beta').check()
    page.get_by_role('button', name='Save & review').click()
    expect(page).to_have_url(f'{unit_url}/review')
    page.get_by_label('Homework URL (required)').fill('https://github.com/example/solution')
    page.get_by_role('button', name='Submit homework').click()
    expect(page.get_by_text('Your homework was submitted.', exact=False)).to_be_visible()

    page.goto(f'{django_server}{data["lesson"].get_absolute_url()}', wait_until='domcontentloaded')
    page.locator(
        '#sidebar-nav a.reader-list-row', has_text='Homework: Document Processing',
    ).first.click()
    expect(page).to_have_url(unit_url)
    expect(_homework_row_status(page)).to_have_text('Submitted')
    for title in ('Question 1', 'Question 2', 'Question 3'):
        _assert_answered(page, title)
    expect(_step(page, 'Introduction').locator('svg.lucide-book-open')).to_have_count(1)
    expect(
        _step(page, 'Review & submit').locator('svg.lucide-clipboard-check'),
    ).to_have_count(1)
    expect(_steps(page).locator('[data-homework-step-answered]')).to_have_count(3)
    context.close()


@browser_journey
def test_phone_learner_finds_the_unanswered_question_from_the_pills(django_server, browser):
    from content.services.homework_step_reader import option_key

    email = 'checks-1924-phone@test.com'
    data = _create_homework(email)
    _save_draft(data, {'q1-first': option_key('Alpha'), 'q3-third': option_key('Beta')})
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()
    page.set_viewport_size({'width': 390, 'height': 844})

    page.goto(f'{unit_url}/q3-third', wait_until='domcontentloaded')
    pills = page.get_by_test_id('homework-step-pills')
    expect(pills).to_be_visible()
    q1 = pills.get_by_role('link', name='Step 2: Question 1, answered', exact=True)
    q2 = pills.get_by_role('link', name='Step 3: Question 2', exact=True)
    q3 = pills.get_by_role('link', name='Step 4: Question 3, answered', exact=True)
    expect(q1).to_have_attribute('data-homework-step-answered', 'true')
    expect(q3).to_have_attribute('aria-current', 'step')
    for pill in (q1, q3):
        expect(pill.get_by_test_id('homework-step-pill-answered')).to_be_visible()
        expect(pill.get_by_test_id('homework-step-pill-answered')).to_have_attribute(
            'aria-hidden', 'true',
        )
    expect(q2.get_by_test_id('homework-step-pill-answered')).to_have_count(0)
    expect(pills.locator('[data-homework-step-answered]')).to_have_count(2)
    expect(q1).to_have_text('2')
    pill_tops = {round(pill.bounding_box()['y']) for pill in pills.get_by_role('link').all()}
    assert len(pill_tops) == 1, pill_tops
    overflow = page.evaluate(
        'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )
    assert overflow <= 0, overflow
    pills.screenshot(path=f'{SCREENSHOT_DIR}/pills-390-light.png')
    page.get_by_test_id('reader-mobile-drawer-toggle').click()
    expect(_steps(page)).to_be_visible()
    page.locator('#sidebar-nav').screenshot(path=f'{SCREENSHOT_DIR}/sidebar-390-light.png')
    _set_theme(page, 'dark')
    page.get_by_test_id('homework-step-pills').screenshot(
        path=f'{SCREENSHOT_DIR}/pills-390-dark.png',
    )
    page.get_by_test_id('reader-mobile-drawer-toggle').click()
    expect(_steps(page)).to_be_visible()
    page.locator('#sidebar-nav').screenshot(path=f'{SCREENSHOT_DIR}/sidebar-390-dark.png')
    _set_theme(page, 'light')

    page.get_by_test_id('homework-step-pills').get_by_role(
        'link', name='Step 3: Question 2', exact=True,
    ).click()
    expect(page).to_have_url(f'{unit_url}/q2-second')
    expect(page.get_by_label('Your answer')).to_have_value('')
    context.close()


@browser_journey
def test_learning_in_public_is_ticked_once_a_link_is_saved(django_server, browser):
    from content.services.homework_step_reader import option_key

    email = 'checks-1924-public@test.com'
    data = _create_homework(email, public_link_cap=2)
    _save_draft(data, {'q1-first': option_key('Alpha')})
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    page.goto(f'{unit_url}/learning-in-public', wait_until='domcontentloaded')
    _assert_unanswered(page, 'Learning in Public')
    slots = page.get_by_test_id('learning-public-link-slots')
    slots.get_by_role('textbox').first.fill('https://www.linkedin.com/posts/example')
    page.get_by_role('button', name='Save & review').click()
    expect(page).to_have_url(f'{unit_url}/review')
    _assert_answered(page, 'Learning in Public')
    context.close()


@browser_journey
def test_closed_homework_checks_match_the_accepted_submission(django_server, browser):
    from django.db import connection

    from content.models.homework import HomeworkState
    from content.services.homework_step_reader import option_key
    from content.services.homework_submissions import save_submission

    email = 'checks-1924-closed@test.com'
    data = _create_homework(email)
    q1, q2, _q3 = data['questions']
    save_submission(
        data['homework'], data['learner'], homework_link='',
        answers_by_question_id={q1.pk: '1', q2.pk: 'Accepted chunking'},
    )
    data['homework'].state = HomeworkState.CLOSED
    data['homework'].save(update_fields=['state'])
    connection.close()
    # A later unsent draft answers Question 3; the accepted snapshot wins.
    _save_draft(data, {'q3-third': option_key('Beta')})
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    page.goto(f'{django_server}{data["lesson"].get_absolute_url()}', wait_until='domcontentloaded')
    page.locator(
        '#sidebar-nav a.reader-list-row', has_text='Homework: Document Processing',
    ).first.click()
    expect(page).to_have_url(unit_url)
    expect(_homework_row_status(page)).to_have_text('Submitted')
    _assert_answered(page, 'Question 1')
    _assert_answered(page, 'Question 2')
    _assert_unanswered(page, 'Question 3')

    _step(page, 'Review & submit').click()
    accepted = page.get_by_test_id('homework-accepted-snapshot')
    expect(accepted).to_contain_text('Alpha')
    expect(accepted).to_contain_text('Accepted chunking')
    context.close()


@browser_journey
def test_screen_reader_hears_which_steps_are_done(django_server, browser):
    from content.services.homework_step_reader import option_key

    email = 'checks-1924-a11y@test.com'
    data = _create_homework(email)
    _save_draft(data, {'q1-first': option_key('Alpha')})
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    page.goto(unit_url, wait_until='domcontentloaded')
    for name in (
        'Introduction', 'Question 1, answered', 'Question 2', 'Question 3', 'Review & submit',
    ):
        expect(_step(page, name)).to_have_count(1)
    expect(_steps(page).get_by_role('link')).to_have_count(5)
    marker = _step(page, 'Question 1, answered').get_by_test_id('homework-step-answered')
    expect(marker).to_have_attribute('aria-hidden', 'true')
    context.close()
