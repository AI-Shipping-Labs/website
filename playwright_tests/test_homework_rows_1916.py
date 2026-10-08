"""Learner journeys for plain homework rows with count and status (#1916).

Homework and capstone rows look like every other unit row in the course
syllabus and the reader sidebar: no chevron, no expandable outline. The
syllabus shows `N questions · <status>`, the sidebar shows the status
alone, and the status follows the learner through the shared
community-base homework states.
"""

import datetime
import os
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

SCREENSHOT_DIR = '.tmp/homework-rows-1916'


def _context(browser, email):
    context = auth_context(browser, email)
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'domain': '127.0.0.1', 'path': '/',
    }])
    return context


def _create_buildcamp(learner_email, *, due_in_days=7, capstone_questions=1, state=None):
    """A Buildcamp-shaped week: an inline overview lesson plus a Homework
    wrapper holding a stepped homework (3 questions) and a capstone."""
    from django.db import connection
    from django.utils import timezone

    from content.models import Cohort, CohortEnrollment, Course, Module, Unit
    from content.models.homework import Homework, Question, QuestionType

    course = Course.objects.create(
        title='AI Engineering Buildcamp', slug='ai-buildcamp',
        status='published', required_level=0,
        reader_navigation_scope='module',
    )
    week = Module.objects.create(
        course=course, title='Document Processing', slug='week-1', sort_order=1,
    )
    # A one-unit `Week 1 Overview` wrapper renders inline, so its lesson is
    # a sibling row of the homework and capstone rows.
    overview = Module.objects.create(
        course=course, parent=week, title='Week 1 Overview', slug='overview',
        sort_order=0,
    )
    lesson = Unit.objects.create(
        module=overview, title='From Idea to Submission', slug='idea-to-submission',
        sort_order=1, body='Read this lesson.',
    )
    folder = Module.objects.create(
        course=course, parent=week, title='Homework', slug='homework', sort_order=90,
    )
    today = timezone.now().date()
    cohort = Cohort.objects.create(
        course=course, name='Cohort 4', external_key='cohort-4',
        start_date=today - datetime.timedelta(days=5),
        end_date=today + datetime.timedelta(days=60),
    )
    unit = Unit.objects.create(
        module=folder, title='Homework: Document Processing with AI',
        slug='document-processing', sort_order=1, kind='homework',
        content_id=uuid.uuid4(),
        homework=(
            'Read this first.\n\n'
            '## Question 1. First\nChoose.\n\n'
            '## Question 2. Second\nChoose.\n\n'
            '## Question 3. Third\nChoose.'
        ),
    )
    homework = Homework.objects.create(
        cohort=cohort, slug='document-processing', title=unit.title,
        content_id=unit.content_id, stepper_enabled=True,
        due_date=timezone.now() + datetime.timedelta(days=due_in_days),
        **({'state': state} if state else {}),
    )
    for index, key in enumerate(('q1-first', 'q2-second', 'q3-third'), start=1):
        Question.objects.create(
            homework=homework, source_question_id=key, text=f'Question {index}',
            question_type=QuestionType.MULTIPLE_CHOICE,
            possible_answers='Alpha\nBeta', correct_answer='1',
        )
    capstone = Unit.objects.create(
        module=folder, title='Capstone: Your AI Project',
        slug='capstone-ai-project', sort_order=2, kind='homework',
        content_id=uuid.uuid4(), homework='Ship your project.',
    )
    capstone_homework = Homework.objects.create(
        cohort=cohort, slug='capstone', title=capstone.title,
        content_id=capstone.content_id,
        due_date=timezone.now() + datetime.timedelta(days=due_in_days + 7),
    )
    for index in range(capstone_questions):
        Question.objects.create(
            homework=capstone_homework, source_question_id=f'c{index}',
            text=f'Capstone question {index}', question_type=QuestionType.FREE_FORM,
        )
    if learner_email:
        learner = create_user(learner_email)
        CohortEnrollment.objects.create(cohort=cohort, user=learner)
    connection.close()
    return {
        'course': course, 'cohort': cohort, 'lesson': lesson, 'unit': unit,
        'homework': homework, 'capstone': capstone,
    }


def _open_week(page, base_url, path):
    page.goto(f'{base_url}{path}', wait_until='domcontentloaded')
    week = page.get_by_test_id('syllabus-parent-module').first
    if week.get_attribute('open') is None:
        week.locator('summary').first.click()
    expect(week).to_have_attribute('open', '')
    # Let the disclosure animation settle before measuring or capturing.
    page.wait_for_function('document.getAnimations().length === 0')
    return week


def _row(page, title):
    return page.locator('[data-syllabus-unit-row]', has_text=title).first


def _set_theme(page, theme):
    page.evaluate("theme => localStorage.setItem('theme', theme)", theme)
    page.reload(wait_until='domcontentloaded')


def _assert_rows_look_like_siblings(page):
    homework_row = _row(page, 'Homework: Document Processing')
    capstone_row = _row(page, 'Capstone: Your AI Project')
    lesson_row = _row(page, 'From Idea to Submission')
    for row in (homework_row, capstone_row):
        expect(row).to_be_visible()
        expect(row.locator('.lucide-chevron-right, [data-lucide="chevron-right"]')).to_have_count(0)
        expect(row.get_by_test_id('syllabus-homework-icon')).to_have_count(1)
    expect(page.get_by_test_id('syllabus-homework-group')).to_have_count(0)
    expect(page.get_by_test_id('syllabus-homework-step')).to_have_count(0)
    boxes = [row.bounding_box() for row in (homework_row, capstone_row, lesson_row)]
    icon_x = [
        row.locator('svg').first.bounding_box()['x']
        for row in (homework_row, capstone_row, lesson_row)
    ]
    assert len({round(box['x']) for box in boxes}) == 1, boxes
    assert len({round(box['width']) for box in boxes}) == 1, boxes
    assert max(icon_x) - min(icon_x) < 1, icon_x
    return homework_row, capstone_row


def _sidebar_homework_row(page):
    return page.locator(
        '#sidebar-nav a.reader-list-row', has_text='Homework: Document Processing',
    ).first


@pytest.mark.core
@browser_journey
def test_learner_status_follows_homework_progress_across_the_course(
    django_server, browser,
):
    email = 'rows-1916-progress@test.com'
    data = _create_buildcamp(email)
    syllabus = f'/courses/{data["course"].slug}/home/syllabus'
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    _open_week(page, django_server, syllabus)
    homework_row, capstone_row = _assert_rows_look_like_siblings(page)
    expect(homework_row.get_by_test_id('syllabus-homework-meta')).to_have_text(
        '3 questions · Not submitted',
    )
    expect(capstone_row.get_by_test_id('syllabus-homework-meta')).to_have_text(
        '1 question · Not submitted',
    )
    page.screenshot(path=f'{SCREENSHOT_DIR}/syllabus-light.png', full_page=True)
    _set_theme(page, 'dark')
    _open_week(page, django_server, syllabus)
    page.screenshot(path=f'{SCREENSHOT_DIR}/syllabus-dark.png', full_page=True)
    _set_theme(page, 'light')
    _open_week(page, django_server, syllabus)

    _row(page, 'Homework: Document Processing').click()
    expect(page).to_have_url(unit_url)
    expect(page.get_by_test_id('homework-page-state')).to_have_text('Not submitted')
    page.get_by_role('link', name='Start questions').click()
    expect(page).to_have_url(f'{unit_url}/q1-first')
    page.get_by_role('radio', name='Alpha').check()
    page.get_by_role('button', name='Save & continue').click()
    expect(page).to_have_url(f'{unit_url}/q2-second')

    _open_week(page, django_server, syllabus)
    expect(
        _row(page, 'Homework: Document Processing').get_by_test_id('syllabus-homework-meta'),
    ).to_have_text('3 questions · Draft')

    page.goto(f'{unit_url}/q2-second', wait_until='domcontentloaded')
    page.get_by_role('radio', name='Beta').check()
    page.get_by_role('button', name='Save & continue').click()
    expect(page).to_have_url(f'{unit_url}/q3-third')
    page.get_by_role('radio', name='Alpha').check()
    page.get_by_role('button', name='Save & review').click()
    expect(page).to_have_url(f'{unit_url}/review')
    page.get_by_label('Homework URL (required)').fill('https://github.com/example/solution')
    page.get_by_role('button', name='Submit homework').click()
    expect(page.get_by_text('Your homework was submitted.', exact=False)).to_be_visible()

    _open_week(page, django_server, syllabus)
    expect(
        _row(page, 'Homework: Document Processing').get_by_test_id('syllabus-homework-meta'),
    ).to_have_text('3 questions · Submitted')

    page.goto(
        f'{django_server}{data["lesson"].get_absolute_url()}', wait_until='domcontentloaded',
    )
    sidebar_row = _sidebar_homework_row(page)
    expect(sidebar_row.get_by_test_id('homework-row-status')).to_have_text('Submitted')
    expect(page.locator('#sidebar-nav').get_by_test_id('syllabus-homework-question-count')).to_have_count(0)
    expect(page.get_by_test_id('homework-step-nav')).to_have_count(0)
    expect(sidebar_row.locator('svg.lucide-chevron-right')).to_have_count(0)
    page.locator('#sidebar-nav').screenshot(path=f'{SCREENSHOT_DIR}/sidebar-light.png')
    _set_theme(page, 'dark')
    page.locator('#sidebar-nav').screenshot(path=f'{SCREENSHOT_DIR}/sidebar-dark.png')
    context.close()


@pytest.mark.core
@browser_journey
def test_learner_moves_between_homework_steps_from_the_sidebar(django_server, browser):
    email = 'rows-1916-steps@test.com'
    data = _create_buildcamp(email)
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()
    page.goto(f'{unit_url}/q2-second?cohort=cohort-4', wait_until='domcontentloaded')

    homework_row = _sidebar_homework_row(page)
    expect(homework_row).to_have_attribute('aria-current', 'page')
    expect(homework_row.locator('svg.lucide-chevron-right')).to_have_count(0)
    expect(page.locator('#sidebar-nav details[data-testid="reader-homework-group"]')).to_have_count(0)
    step_nav = page.get_by_role('group', name='Homework steps')
    expect(step_nav).to_be_visible()
    expect(step_nav.get_by_role('link')).to_have_text([
        'Introduction', 'Question 1', 'Question 2', 'Question 3', 'Review & submit',
    ])
    expect(page.get_by_test_id('homework-step-current')).to_have_text('Question 2')
    expect(page.get_by_test_id('homework-step-current')).to_have_attribute('aria-current', 'step')
    page.locator('#sidebar-nav').screenshot(path=f'{SCREENSHOT_DIR}/sidebar-step-light.png')
    _set_theme(page, 'dark')
    page.locator('#sidebar-nav').screenshot(path=f'{SCREENSHOT_DIR}/sidebar-step-dark.png')
    _set_theme(page, 'light')

    page.get_by_role('group', name='Homework steps').get_by_role('link', name='Question 3').click()
    expect(page).to_have_url(f'{unit_url}/q3-third?cohort=cohort-4')
    expect(page.get_by_test_id('homework-step-current')).to_have_text('Question 3')

    page.locator('#sidebar-nav a.reader-list-row', has_text='Capstone: Your AI Project').click()
    expect(page).to_have_url(f'{django_server}{data["capstone"].get_absolute_url()}?cohort=cohort-4')
    expect(page.get_by_test_id('homework-step-nav')).to_have_count(0)
    expect(_sidebar_homework_row(page)).not_to_have_attribute('aria-current', 'page')
    context.close()


@browser_journey
def test_past_due_homework_stays_open_until_the_operator_closes_it(
    django_server, browser,
):
    """Issue #1917: a past-due OPEN homework still accepts work, so the row
    reads ``Not submitted``; ``Closed — not submitted`` comes only from the
    operator setting ``state=CLOSED``."""
    from django.db import connection

    from content.models.homework import HomeworkState

    email = 'rows-1916-closed@test.com'
    data = _create_buildcamp(email, due_in_days=-1)
    syllabus = f'/courses/{data["course"].slug}/home/syllabus'
    context = _context(browser, email)
    page = context.new_page()

    _open_week(page, django_server, syllabus)
    homework_row = _row(page, 'Homework: Document Processing')
    expect(homework_row.get_by_test_id('syllabus-homework-deadline')).to_contain_text('Due ')
    expect(homework_row.get_by_test_id('syllabus-homework-meta')).to_have_text(
        '3 questions · Not submitted',
    )

    data['homework'].state = HomeworkState.CLOSED
    data['homework'].save(update_fields=['state'])
    connection.close()
    _open_week(page, django_server, syllabus)
    homework_row = _row(page, 'Homework: Document Processing')
    expect(homework_row.get_by_test_id('syllabus-homework-meta')).to_have_text(
        '3 questions · Closed — not submitted',
    )
    homework_row.click()
    expect(page.get_by_test_id('homework-page-state')).to_have_text('Closed — not submitted')

    data['homework'].state = HomeworkState.SCORED
    data['homework'].save(update_fields=['state'])
    connection.close()
    _open_week(page, django_server, syllabus)
    # Scored without a submission is still "Closed — not submitted"; the
    # shared model reserves "Scored" for a learner who submitted.
    expect(
        _row(page, 'Homework: Document Processing').get_by_test_id('syllabus-homework-meta'),
    ).to_have_text('3 questions · Closed — not submitted')
    context.close()


@browser_journey
def test_scored_and_unsubmitted_changes_follow_the_learner(django_server, browser):
    from community_base.homework_steps.models import HomeworkDraft
    from django.db import connection

    from accounts.models import User
    from content.models.homework import HomeworkState
    from content.services.homework_step_reader import assignment_key, option_key
    from content.services.homework_submissions import save_submission

    email = 'rows-1916-scored@test.com'
    data = _create_buildcamp(email)
    learner = User.objects.get(email=email)
    homework = data['homework']
    questions = list(homework.questions.all())
    save_submission(
        homework, learner, homework_link='',
        answers_by_question_id={question.pk: '1' for question in questions},
    )
    HomeworkDraft.objects.create(
        user=learner, assignment_key=assignment_key(homework),
        answers={
            'q1-first': option_key('Beta'), 'q2-second': option_key('Alpha'),
            'q3-third': option_key('Alpha'),
        },
    )
    connection.close()
    syllabus = f'/courses/{data["course"].slug}/home/syllabus'
    unit_url = f'{django_server}{data["unit"].get_absolute_url()}'
    context = _context(browser, email)
    page = context.new_page()

    _open_week(page, django_server, syllabus)
    expect(
        _row(page, 'Homework: Document Processing').get_by_test_id('syllabus-homework-meta'),
    ).to_have_text('3 questions · Unsubmitted changes')

    page.goto(f'{unit_url}/review', wait_until='domcontentloaded')
    page.get_by_label('Homework URL (required)').fill('https://github.com/example/solution')
    page.get_by_role('button', name='Update submission').click()
    expect(page.get_by_text('Your homework was submitted.', exact=False)).to_be_visible()
    _open_week(page, django_server, syllabus)
    expect(
        _row(page, 'Homework: Document Processing').get_by_test_id('syllabus-homework-meta'),
    ).to_have_text('3 questions · Submitted')

    homework.state = HomeworkState.SCORED
    homework.save(update_fields=['state'])
    connection.close()
    _open_week(page, django_server, syllabus)
    expect(
        _row(page, 'Homework: Document Processing').get_by_test_id('syllabus-homework-meta'),
    ).to_have_text('3 questions · Scored')
    page.goto(f'{django_server}{data["lesson"].get_absolute_url()}', wait_until='domcontentloaded')
    expect(_sidebar_homework_row(page).get_by_test_id('homework-row-status')).to_have_text('Scored')
    context.close()


@browser_journey
def test_anonymous_and_staff_preview_see_count_without_status(django_server, browser):
    data = _create_buildcamp(None)
    create_staff_user('rows-1916-staff@test.com')

    anonymous = browser.new_context()
    page = anonymous.new_page()
    _open_week(page, django_server, data['course'].get_absolute_url())
    homework_row, capstone_row = _assert_rows_look_like_siblings(page)
    expect(homework_row.get_by_test_id('syllabus-homework-meta')).to_have_text('3 questions')
    expect(page.get_by_test_id('homework-row-status')).to_have_count(0)
    homework_row.click()
    expect(page).to_have_url(f'{django_server}{data["unit"].get_absolute_url()}')
    expect(page.get_by_test_id('homework-page-state')).to_have_count(0)
    anonymous.close()

    staff = _context(browser, 'rows-1916-staff@test.com')
    page = staff.new_page()
    _open_week(page, django_server, f'/courses/{data["course"].slug}/home/syllabus?cohort=cohort-4')
    expect(
        _row(page, 'Homework: Document Processing').get_by_test_id('syllabus-homework-meta'),
    ).to_have_text('3 questions')
    expect(page.get_by_test_id('homework-row-status')).to_have_count(0)
    staff.close()


@browser_journey
def test_zero_question_homework_and_no_cohort_render_clean_rows(django_server, browser):
    from django.db import connection

    from content.models import Course, Module, Unit

    email = 'rows-1916-zero@test.com'
    data = _create_buildcamp(email, capstone_questions=0)
    other = Course.objects.create(
        title='No cohort course', slug='no-cohort-1916',
        status='published', required_level=0,
    )
    week = Module.objects.create(course=other, title='Week 1', slug='week-1', sort_order=1)
    Unit.objects.create(module=week, title='Intro lesson', slug='intro', sort_order=0, body='Hi.')
    Unit.objects.create(
        module=week, title='Homework: Uncohorted', slug='uncohorted', sort_order=1,
        kind='homework', content_id=uuid.uuid4(), homework='Do it.',
    )
    connection.close()

    context = _context(browser, email)
    page = context.new_page()
    _open_week(page, django_server, f'/courses/{data["course"].slug}/home/syllabus')
    capstone_meta = _row(page, 'Capstone: Your AI Project').get_by_test_id('syllabus-homework-meta')
    expect(capstone_meta).to_have_text('Not submitted')
    expect(
        _row(page, 'Capstone: Your AI Project').get_by_test_id('syllabus-homework-question-count'),
    ).to_have_count(0)
    context.close()

    anonymous = browser.new_context()
    page = anonymous.new_page()
    page.goto(f'{django_server}/courses/{other.slug}', wait_until='domcontentloaded')
    page.get_by_test_id('syllabus-module-summary').first.click()
    homework_row = _row(page, 'Homework: Uncohorted')
    expect(homework_row).to_be_visible()
    expect(homework_row.get_by_test_id('syllabus-homework-meta')).to_have_count(0)
    lesson_row = _row(page, 'Intro lesson')
    assert round(homework_row.bounding_box()['x']) == round(lesson_row.bounding_box()['x'])
    anonymous.close()


@browser_journey
def test_phone_learner_reads_long_status_without_overflow(django_server, browser):
    from content.models.homework import HomeworkState

    email = 'rows-1916-phone@test.com'
    data = _create_buildcamp(email, due_in_days=-1, state=HomeworkState.CLOSED)
    context = _context(browser, email)
    page = context.new_page()
    page.set_viewport_size({'width': 390, 'height': 844})

    _open_week(page, django_server, f'/courses/{data["course"].slug}/home/syllabus')
    homework_row = _row(page, 'Homework: Document Processing')
    expect(homework_row.get_by_test_id('syllabus-homework-meta')).to_have_text(
        '3 questions · Closed — not submitted',
    )
    title_box = homework_row.locator('.syllabus-title').bounding_box()
    assert title_box['width'] > 120, title_box
    overflow = page.evaluate(
        'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )
    assert overflow <= 0, overflow
    page.screenshot(path=f'{SCREENSHOT_DIR}/syllabus-390-light.png', full_page=True)
    _set_theme(page, 'dark')
    _open_week(page, django_server, f'/courses/{data["course"].slug}/home/syllabus')
    page.screenshot(path=f'{SCREENSHOT_DIR}/syllabus-390-dark.png', full_page=True)
    _set_theme(page, 'light')

    page.goto(f'{django_server}{data["lesson"].get_absolute_url()}', wait_until='domcontentloaded')
    page.get_by_test_id('reader-mobile-drawer-toggle').click()
    drawer_row = _sidebar_homework_row(page)
    expect(drawer_row).to_be_visible()
    expect(drawer_row.get_by_test_id('homework-row-status')).to_have_text('Closed — not submitted')
    nav_box = page.locator('#sidebar-nav').bounding_box()
    status_box = drawer_row.get_by_test_id('homework-row-status').bounding_box()
    assert status_box['x'] + status_box['width'] <= nav_box['x'] + nav_box['width'] + 0.5
    overflow = page.evaluate(
        'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )
    assert overflow <= 0, overflow
    page.screenshot(path=f'{SCREENSHOT_DIR}/drawer-390-light.png')
    drawer_row.click()
    expect(page).to_have_url(f'{django_server}{data["unit"].get_absolute_url()}')
    context.close()
