"""Homework Q&A scoped to Introduction and question steps (issue #1925).

Introduction owns its own thread (no answer spoilers from the old unit
thread), Learning in Public has no Q&A, and Review & submit shows the unit
thread only as a collapsed, read-only "Earlier homework discussion" archive.
The basic intro / LIP / review mounts live in
``test_homework_step_qa_1897.py``; this module covers the archive, the
migrated comments, staff moderation, notifications, and cohorts.
"""

import datetime
import importlib
import os
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

LEGACY_Q2 = 'For Question 2 I got 42 tokens'
LEGACY_Q2_REPLY = 'Same here, 42'
LEGACY_OTHER = 'Question 2 answer is definitely 42?'
SPOILER = 'The Question 1 answer is 7944'


def _learner_context(browser, email):
    context = auth_context(browser, email)
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'domain': '127.0.0.1', 'path': '/',
    }])
    return context


def _django_user(email):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.get(email=email)


def _create_homework(*, legacy_comments=True):
    """A stepper homework (2 questions + Learning in Public) with an
    optional legacy unit-thread discussion: 2 top-level comments, one with
    a reply."""
    from django.db import connection
    from django.utils import timezone

    from comments.models import Comment
    from content.models import Course, Module, Unit
    from content.models.cohort import Cohort
    from content.models.homework import Homework, Question, QuestionType

    course = Course.objects.create(
        title='Scoped QA course', slug='scoped-qa-1925',
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
    content_id = uuid.uuid4()
    unit = Unit.objects.create(
        module=module, title='Homework', slug='homework', kind='homework',
        content_id=content_id,
        homework=(
            'Read this first.\n\n'
            '## Question 1. Unstructured\nHow many tokens?\n'
            '## Question 2. Structured\nAnd now?\n'
            '## Learning in Public\nShare your progress if you like.\n'
        ),
    )
    homework = Homework.objects.create(
        cohort=cohort, slug='homework', title='Homework',
        content_id=content_id, stepper_enabled=True, learning_in_public_cap=3,
    )
    Question.objects.create(
        homework=homework, source_question_id='q1-first', text='First',
        question_type=QuestionType.FREE_FORM,
    )
    Question.objects.create(
        homework=homework, source_question_id='q2-second', text='Second',
        question_type=QuestionType.FREE_FORM,
    )
    if legacy_comments:
        create_user('legacy-1925@test.com')
        author = _django_user('legacy-1925@test.com')
        first = Comment.objects.create(
            content_id=content_id, user=author, body=LEGACY_Q2,
        )
        Comment.objects.create(
            content_id=content_id, user=author, body=LEGACY_Q2_REPLY,
            parent=first,
        )
        Comment.objects.create(
            content_id=content_id, user=author, body=LEGACY_OTHER,
        )
    connection.close()
    return course, cohort, unit


def _step_url(server, unit, step, query=''):
    return f'{server}{unit.get_absolute_url()}/{step}{query}'


def _post_question(page, body):
    page.locator('#qa-new-question').fill(body)
    page.locator('#qa-post-btn').click()


def _wait_for_count(page, count):
    page.wait_for_function(
        "document.getElementById('qa-count') && "
        "document.getElementById('qa-count').textContent === '%s'" % count,
        timeout=5000,
    )


def _open_archive(page):
    archive = page.get_by_test_id('homework-qa-archive-details')
    archive.locator('summary').click()
    expect(archive).to_have_attribute('open', '')
    return archive


def _sidebar_link(page, name):
    return page.get_by_test_id('homework-step-nav').locator('a', has_text=name).first


@browser_journey
@pytest.mark.core
def test_learner_reviewing_reads_the_earlier_discussion_read_only(
    django_server, browser,
):
    create_user('main@test.com', tier_slug='main')
    _course, _cohort, unit = _create_homework()

    context = _learner_context(browser, 'main@test.com')
    page = context.new_page()
    page.goto(_step_url(django_server, unit, 'intro'), wait_until='networkidle')
    # No spoilers on the first step a learner sees.
    _wait_for_count(page, '0')
    for body in (LEGACY_Q2, LEGACY_Q2_REPLY, LEGACY_OTHER):
        assert body not in page.content()

    _sidebar_link(page, 'Review & submit').click()
    page.wait_for_url('**/review')
    page.wait_for_load_state('networkidle')
    assert page.locator('textarea.qa-new-question').count() == 0
    archive = page.get_by_test_id('homework-qa-archive-details')
    expect(archive.locator('summary')).to_have_text(
        'Earlier homework discussion (2)',
    )
    expect(archive).not_to_have_attribute('open', '')
    # The archive sits below the review content.
    review_box = page.get_by_test_id('homework-review-form').bounding_box()
    archive_box = archive.bounding_box()
    assert archive_box['y'] > review_box['y'] + review_box['height']

    _open_archive(page)
    for body in (LEGACY_Q2, LEGACY_Q2_REPLY, LEGACY_OTHER):
        expect(archive.get_by_text(body, exact=True)).to_be_visible()
    expect(archive.get_by_text(
        'Questions posted before each step had its own Q&A.', exact=False,
    )).to_be_visible()
    assert archive.get_by_role('button', name='Reply').count() == 0
    assert archive.locator('textarea').count() == 0
    assert archive.get_by_test_id('qa-delete').count() == 0

    context.close()


@browser_journey
@pytest.mark.core
def test_review_stays_clean_without_earlier_discussion(django_server, browser):
    create_user('main@test.com', tier_slug='main')
    _course, _cohort, unit = _create_homework(legacy_comments=False)

    context = _learner_context(browser, 'main@test.com')
    page = context.new_page()
    page.goto(_step_url(django_server, unit, 'review'), wait_until='networkidle')

    expect(page.get_by_test_id('homework-submit-button')).to_be_visible()
    assert page.get_by_text('Earlier homework discussion').count() == 0
    assert page.get_by_role('heading', name='Questions & Answers').count() == 0
    assert page.locator('#qa-section').count() == 0
    assert page.locator('.qa-thread').count() == 0

    context.close()


@browser_journey
@pytest.mark.core
def test_comments_from_review_and_lip_threads_survive_in_the_archive(
    django_server, browser,
):
    """Comments posted on the retired review / LIP step threads before
    #1925 are moved by migration 0086 and readable in the archive."""
    from django.apps import apps as django_apps
    from django.db import connection

    from comments.models import Comment
    from content.models.homework import HomeworkStepThread
    from content.services.homework_step_threads import step_thread_content_id

    create_user('main@test.com', tier_slug='main')
    _course, _cohort, unit = _create_homework(legacy_comments=False)
    author = _django_user('main@test.com')
    for slug, body in (
        ('review', 'Does review show my score?'),
        ('learning-in-public', 'Which platforms count?'),
    ):
        thread = HomeworkStepThread.objects.create(
            unit_content_id=unit.content_id, step_slug=slug,
            content_id=step_thread_content_id(unit.content_id, slug),
        )
        Comment.objects.create(
            content_id=thread.content_id, user=author, body=body,
        )
    migration = importlib.import_module(
        'content.migrations.0086_homework_qa_intro_thread_review_archive',
    )
    migration.move_retired_step_comments_to_unit_thread(django_apps, None)
    connection.close()

    context = _learner_context(browser, 'main@test.com')
    page = context.new_page()
    page.goto(_step_url(django_server, unit, 'review'), wait_until='networkidle')
    archive = _open_archive(page)
    expect(archive.get_by_text('Does review show my score?')).to_be_visible()
    expect(archive.get_by_text('Which platforms count?')).to_be_visible()

    page.goto(
        _step_url(django_server, unit, 'learning-in-public'),
        wait_until='networkidle',
    )
    assert 'Does review show my score?' not in page.content()
    assert 'Which platforms count?' not in page.content()

    context.close()


@browser_journey
@pytest.mark.core
def test_staff_hides_a_leftover_spoiler_from_the_archive(django_server, browser):
    from django.db import connection

    from comments.models import Comment

    create_user('main@test.com', tier_slug='main')
    create_staff_user('admin@test.com')
    _course, _cohort, unit = _create_homework()
    Comment.objects.create(
        content_id=unit.content_id, user=_django_user('main@test.com'),
        body=SPOILER,
    )
    connection.close()

    staff = auth_context(browser, 'admin@test.com')
    page = staff.new_page()
    page.goto(_step_url(django_server, unit, 'review'), wait_until='networkidle')
    archive = page.get_by_test_id('homework-qa-archive-details')
    expect(archive.locator('summary')).to_have_text(
        'Earlier homework discussion (3)',
    )
    _open_archive(page)
    card = archive.locator('.qa-card', has_text=SPOILER).first
    page.once('dialog', lambda dialog: dialog.accept())
    card.get_by_test_id('qa-delete').first.click()
    expect(archive.get_by_text(SPOILER)).to_have_count(0)
    expect(archive.locator('summary')).to_have_text(
        'Earlier homework discussion (2)',
    )
    staff.close()

    learner = _learner_context(browser, 'main@test.com')
    page = learner.new_page()
    page.goto(_step_url(django_server, unit, 'review'), wait_until='networkidle')
    archive = _open_archive(page)
    expect(archive.locator('summary')).to_have_text(
        'Earlier homework discussion (2)',
    )
    expect(archive.get_by_text(LEGACY_OTHER)).to_be_visible()
    assert SPOILER not in page.content()
    learner.close()


@browser_journey
@pytest.mark.core
def test_instructor_notification_for_an_intro_question_opens_intro(
    django_server, browser,
):
    from django.db import connection

    from content.models import Instructor

    create_user('instructor-1925@test.com')
    create_user('main@test.com', tier_slug='main')
    course, _cohort, unit = _create_homework()
    instructor = Instructor.objects.create(
        instructor_id='qa-1925', name='Ada', status='published',
        user=_django_user('instructor-1925@test.com'),
    )
    course.instructors.add(instructor)
    connection.close()

    asker = _learner_context(browser, 'main@test.com')
    page = asker.new_page()
    page.goto(_step_url(django_server, unit, 'intro'), wait_until='networkidle')
    _post_question(page, 'Where do I find the dataset?')
    _wait_for_count(page, '1')
    asker.close()

    context = _learner_context(browser, 'instructor-1925@test.com')
    page = context.new_page()
    page.goto(f'{django_server}/notifications', wait_until='networkidle')
    expected_suffix = f'{unit.get_absolute_url()}/intro#qa-section'
    target = page.locator(f'[data-notification-target][href$="{expected_suffix}"]')
    expect(target.first).to_be_visible()
    target.first.click()
    page.wait_for_url(f'**{expected_suffix}')
    _wait_for_count(page, '1')
    expect(
        page.locator('#qa-section').get_by_text('Where do I find the dataset?'),
    ).to_be_visible()
    assert LEGACY_Q2 not in page.content()
    context.close()


@browser_journey
@pytest.mark.core
def test_cohort_learner_sees_the_same_intro_thread(django_server, browser):
    from django.db import connection

    from content.models.cohort import CohortEnrollment

    create_user('main@test.com', tier_slug='main')
    create_user('cohort-1925@test.com', tier_slug='main')
    _course, cohort, unit = _create_homework()
    CohortEnrollment.objects.create(
        user=_django_user('cohort-1925@test.com'), cohort=cohort,
    )
    connection.close()

    first = _learner_context(browser, 'main@test.com')
    page = first.new_page()
    page.goto(_step_url(django_server, unit, 'intro'), wait_until='networkidle')
    _post_question(page, 'Is the deadline in UTC?')
    _wait_for_count(page, '1')
    first.close()

    second = _learner_context(browser, 'cohort-1925@test.com')
    page = second.new_page()
    page.goto(
        _step_url(django_server, unit, 'intro', f'?cohort={cohort.external_key}'),
        wait_until='networkidle',
    )
    _wait_for_count(page, '1')
    expect(
        page.locator('#qa-section').get_by_text('Is the deadline in UTC?'),
    ).to_be_visible()
    assert LEGACY_Q2 not in page.content()
    second.close()
