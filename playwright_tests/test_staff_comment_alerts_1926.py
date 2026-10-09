"""Playwright E2E for the staff Slack comment alert (issue #1926).

A non-staff member's comment or reply on a course lesson, homework step, or
workshop page posts one message to the staff Slack channel. The Django
server runs in this process (background thread), so the Slack HTTP call is
captured in-process by patching
``notifications.services.staff_slack.requests.post``; no live call
is made. Settings are DB overrides written through the package settings
service, or edited in Studio where the scenario says so.

Usage:
    uv run pytest playwright_tests/test_staff_comment_alerts_1926.py -v
"""

import datetime
import os
import uuid
from contextlib import contextmanager
from unittest import mock

import pytest
import requests

from playwright_tests.conftest import auth_context, create_staff_user, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

POST_TARGET = 'notifications.services.staff_slack.requests.post'
CONFIG_KEYS = (
    'SLACK_ENABLED',
    'SLACK_BOT_TOKEN',
    'SITE_BASE_URL',
    'STAFF_COMMENT_NOTIFY_ENABLED',
    'STAFF_COMMENT_NOTIFY_CHANNEL_ID',
    'STAFF_SIGNUP_NOTIFY_CHANNEL_ID',
)
LESSON_PATH = '/courses/llm-zoomcamp/module-1/intro-to-rag'


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------


def _configure(server, **overrides):
    """Write Slack + alert settings as DB overrides."""
    from community_base.config.models import Setting
    from community_base.config.service import set as package_set

    from integrations.config import clear_config_cache

    Setting.objects.filter(key__in=CONFIG_KEYS).delete()
    values = {
        'SLACK_ENABLED': 'true',
        'SLACK_BOT_TOKEN': 'xoxb-test-1926',
        'SITE_BASE_URL': server,
        'STAFF_COMMENT_NOTIFY_CHANNEL_ID': 'C_COMMENTS',
    }
    values.update(overrides)
    for key, value in values.items():
        if value is None:
            continue
        package_set(key, value, actor_ref='test:1926')
    clear_config_cache()
    connection.close()


def _cleanup_config():
    from community_base.config.models import Setting

    from integrations.config import clear_config_cache

    Setting.objects.filter(key__in=CONFIG_KEYS).delete()
    clear_config_cache()
    connection.close()


@contextmanager
def _capture_slack(fail_with=None):
    """Capture every ``chat.postMessage`` payload the alert sends."""
    posts = []

    def fake_post(url, json=None, **kwargs):
        posts.append(json)
        if fail_with is not None:
            raise fail_with
        response = mock.Mock()
        response.json.return_value = {'ok': True}
        return response

    with mock.patch(POST_TARGET, side_effect=fake_post):
        try:
            yield posts
        finally:
            _cleanup_config()


def _button_url(payload):
    for block in payload['blocks']:
        if block['type'] == 'actions':
            return block['elements'][0]['url']
    raise AssertionError('no Open discussion button in payload')


def _headline(payload):
    return payload['blocks'][0]['text']['text']


def _preview(payload):
    quotes = [
        block['text']['text'] for block in payload['blocks']
        if block['type'] == 'section' and block['text']['text'].startswith('>')
    ]
    assert len(quotes) == 1
    return quotes[0][1:]


def _make_lesson(instructor_email=None):
    from accounts.models import User
    from content.models import Course, Instructor, Module, Unit

    course = Course.objects.create(
        title='LLM Zoomcamp', slug='llm-zoomcamp', status='published',
    )
    module = Module.objects.create(
        course=course, title='Module 1', slug='module-1', sort_order=1,
    )
    unit = Unit.objects.create(
        module=module, title='Intro to RAG', slug='intro-to-rag',
        sort_order=1, is_preview=True, content_id=uuid.uuid4(),
        body='Lesson body',
    )
    if instructor_email:
        instructor = Instructor.objects.create(
            instructor_id='alexey-grigorev', name='Alexey Grigorev',
            status='published',
            user=User.objects.get(email=instructor_email),
        )
        course.instructors.add(instructor)
    connection.close()
    return unit


def _make_stepper_homework():
    from django.utils import timezone

    from content.models import Course, Module, Unit
    from content.models.cohort import Cohort
    from content.models.homework import Homework, Question, QuestionType

    course = Course.objects.create(
        title='LLM Zoomcamp', slug='llm-zoomcamp', status='published',
        required_level=0,
    )
    module = Module.objects.create(
        course=course, title='Module 1', slug='module-1', sort_order=1,
    )
    today = timezone.now().date()
    cohort = Cohort.objects.create(
        course=course, name='Cohort 1',
        start_date=today - datetime.timedelta(days=2),
        end_date=today + datetime.timedelta(days=30),
    )
    content_id = uuid.uuid4()
    unit = Unit.objects.create(
        module=module, title='Homework 1', slug='homework', kind='homework',
        content_id=content_id,
        homework=(
            'Read this first.\n\n'
            '## Question 1. Tokens\nHow many tokens?\n'
            '## Question 2. Size\nHow big is the index?\n'
        ),
    )
    homework = Homework.objects.create(
        cohort=cohort, slug='homework', title='Homework 1',
        content_id=content_id, stepper_enabled=True,
    )
    for source_question_id in ('q1-tokens', 'q2-size'):
        Question.objects.create(
            homework=homework, source_question_id=source_question_id,
            text='Q', question_type=QuestionType.FREE_FORM,
        )
    connection.close()
    return unit


def _make_workshop_page():
    from content.models import Workshop, WorkshopPage

    workshop = Workshop.objects.create(
        title='Agents 101', slug='agents-101',
        date=datetime.date(2026, 4, 21), status='published',
        landing_required_level=0, pages_required_level=0,
    )
    page = WorkshopPage.objects.create(
        workshop=workshop, slug='setup', title='Setup',
        sort_order=1, body='# Body', content_id=uuid.uuid4(),
    )
    connection.close()
    return page


def _seed_comment(content_id, email, body):
    from accounts.models import User
    from comments.models import Comment

    comment = Comment.objects.create(
        content_id=content_id, user=User.objects.get(email=email), body=body,
    )
    connection.close()
    return comment


def _post_question(page, text, *, container=None):
    """Post a top-level question and assert the API accepted it."""
    scope = container or page
    scope.locator('#qa-new-question').fill(text)
    with page.expect_response(
        lambda r: '/api/comments/' in r.url and r.request.method == 'POST',
    ) as response_info:
        scope.locator('#qa-post-btn').click()
    assert response_info.value.status == 201
    page.wait_for_function(
        'text => document.body.textContent.includes(text)',
        arg=text.split('\n')[0][:40],
        timeout=8000,
    )


def _post_reply(page, text):
    page.wait_for_selector('.qa-reply-toggle', timeout=8000)
    page.locator('.qa-reply-toggle').first.click()
    page.locator('.qa-reply-form:not(.hidden) textarea').fill(text)
    with page.expect_response(
        lambda r: '/reply' in r.url and r.request.method == 'POST',
    ) as response_info:
        page.locator('.qa-reply-form:not(.hidden) .qa-reply-btn').click()
    assert response_info.value.status == 201
    page.wait_for_function(
        'text => document.body.textContent.includes(text)',
        arg=text, timeout=8000,
    )


def _wait_for_qa_text(page, text):
    page.wait_for_function(
        "text => { var s = document.getElementById('qa-section');"
        " return s && s.textContent.includes(text); }",
        arg=text, timeout=8000,
    )


def _open_slack_settings(page, server):
    """Open Studio settings with the Slack integration card visible."""
    page.goto(
        f'{server}/studio/settings/#integration-slack', wait_until='networkidle',
    )
    card = page.locator('#integration-slack')
    card.wait_for(state='visible', timeout=8000)
    return card


def _save_slack_settings(page, server):
    """Save the Slack card, then reopen it (the redirect drops the hash)."""
    with page.expect_navigation():
        page.locator('#integration-slack').get_by_role(
            'button', name='Save slack',
        ).click()
    return _open_slack_settings(page, server)


# ----------------------------------------------------------------------
# Scenario 1: Admin hears about a learner's question and answers it.
# ----------------------------------------------------------------------


@browser_journey
@pytest.mark.core
def test_admin_hears_about_lesson_question_and_answers_it(
    django_server, browser,
):
    create_user('main@test.com', tier_slug='main', first_name='Mia')
    create_staff_user('admin@test.com')
    _make_lesson()
    _configure(django_server)

    with _capture_slack() as posts:
        member_ctx = auth_context(browser, 'main@test.com')
        member_page = member_ctx.new_page()
        dialogs = []
        member_page.on('dialog', lambda d: (dialogs.append(d.message), d.dismiss()))
        member_page.goto(f'{django_server}{LESSON_PATH}', wait_until='networkidle')
        _post_question(member_page, 'How do I chunk PDFs?')
        assert dialogs == []
        member_ctx.close()

        assert len(posts) == 1
        payload = posts[0]
        assert payload['channel'] == 'C_COMMENTS'
        headline = _headline(payload)
        assert '|Mia>' in headline
        assert '(main@test.com) commented on' in headline
        assert 'LLM Zoomcamp: Intro to RAG' in headline
        assert _preview(payload) == 'How do I chunk PDFs?'
        link = _button_url(payload)
        assert link == f'{django_server}{LESSON_PATH}#qa-section'

        admin_ctx = auth_context(browser, 'admin@test.com')
        admin_page = admin_ctx.new_page()
        admin_page.goto(link, wait_until='networkidle')
        assert admin_page.url.endswith(f'{LESSON_PATH}#qa-section')
        _wait_for_qa_text(admin_page, 'How do I chunk PDFs?')

        _post_reply(admin_page, 'Use a page-aware splitter')
        admin_ctx.close()

        assert len(posts) == 1


# ----------------------------------------------------------------------
# Scenario 2: Admin is taken straight to the homework step.
# ----------------------------------------------------------------------


@browser_journey
def test_homework_step_alert_links_to_that_step(django_server, browser):
    create_user('main@test.com', tier_slug='main', first_name='Mia')
    create_staff_user('admin@test.com')
    unit = _make_stepper_homework()
    unit_url = unit.get_absolute_url()
    _configure(django_server)
    question = 'Is the expected answer in MB or GB?'

    with _capture_slack() as posts:
        member_ctx = auth_context(browser, 'main@test.com')
        member_page = member_ctx.new_page()
        member_page.goto(
            f'{django_server}{unit_url}/q2-size', wait_until='networkidle',
        )
        _post_question(member_page, question)
        member_ctx.close()

        assert len(posts) == 1
        link = _button_url(posts[0])
        assert link == f'{django_server}{unit_url}/q2-size#qa-section'
        assert 'Homework 1 — Question 2' in posts[0]['text']

        admin_ctx = auth_context(browser, 'admin@test.com')
        admin_page = admin_ctx.new_page()
        admin_page.goto(link, wait_until='networkidle')
        assert admin_page.url.endswith('/q2-size#qa-section')
        _wait_for_qa_text(admin_page, question)

        admin_page.goto(
            f'{django_server}{unit_url}/q1-tokens', wait_until='networkidle',
        )
        admin_page.wait_for_function(
            "document.getElementById('qa-count')"
            " && document.getElementById('qa-count').textContent === '0'",
            timeout=8000,
        )
        assert question not in admin_page.locator('#qa-section').inner_text()
        admin_ctx.close()


# ----------------------------------------------------------------------
# Scenario 3: Admin follows a reply between two learners on a workshop.
# ----------------------------------------------------------------------


@browser_journey
def test_workshop_reply_alert_names_parent_author_and_workshop(
    django_server, browser,
):
    create_user('main@test.com', tier_slug='main', first_name='Mia')
    create_user('free@test.com', tier_slug='free', first_name='Fred')
    create_staff_user('admin@test.com')
    ws_page = _make_workshop_page()
    page_url = ws_page.get_absolute_url()
    _seed_comment(ws_page.content_id, 'free@test.com', 'uv sync fails for me')
    _configure(django_server)

    with _capture_slack() as posts:
        member_ctx = auth_context(browser, 'main@test.com')
        member_page = member_ctx.new_page()
        member_page.goto(f'{django_server}{page_url}', wait_until='networkidle')
        _post_reply(member_page, 'Try upgrading uv')
        member_ctx.close()

        assert len(posts) == 1
        payload = posts[0]
        assert payload['text'].startswith('New reply on')
        assert 'replied to Fred on' in _headline(payload)
        assert 'Agents 101: Setup' in _headline(payload)

        admin_ctx = auth_context(browser, 'admin@test.com')
        admin_page = admin_ctx.new_page()
        admin_page.goto(_button_url(payload), wait_until='networkidle')
        _wait_for_qa_text(admin_page, 'uv sync fails for me')
        _wait_for_qa_text(admin_page, 'Try upgrading uv')
        admin_ctx.close()


# ----------------------------------------------------------------------
# Scenario 4: Learner's comment still posts when Slack is down.
# ----------------------------------------------------------------------


@browser_journey
@pytest.mark.core
def test_comment_survives_slack_outage(django_server, browser):
    create_user('main@test.com', tier_slug='main', first_name='Mia')
    _make_lesson()
    _configure(django_server)

    with _capture_slack(fail_with=requests.exceptions.Timeout()) as posts:
        ctx = auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        dialogs = []
        page.on('dialog', lambda d: (dialogs.append(d.message), d.dismiss()))
        page.goto(f'{django_server}{LESSON_PATH}', wait_until='networkidle')
        _post_question(page, 'Video does not load')
        assert dialogs == []
        assert len(posts) == 1

        page.reload(wait_until='networkidle')
        _wait_for_qa_text(page, 'Video does not load')
        ctx.close()


# ----------------------------------------------------------------------
# Scenario 5: Admin pauses comment alerts from Studio settings.
# ----------------------------------------------------------------------


@browser_journey
def test_admin_pauses_and_resumes_alerts_in_studio(django_server, browser):
    create_user('main@test.com', tier_slug='main', first_name='Mia')
    create_staff_user('admin@test.com')
    _make_lesson()
    _configure(django_server)

    with _capture_slack() as posts:
        admin_ctx = auth_context(browser, 'admin@test.com')
        admin_page = admin_ctx.new_page()
        _open_slack_settings(admin_page, django_server)
        field = admin_page.locator('#field-STAFF_COMMENT_NOTIFY_ENABLED')
        assert admin_page.locator(
            'label[for="field-STAFF_COMMENT_NOTIFY_ENABLED"]',
        ).inner_text() == 'Staff Comment Notify Enabled'
        assert field.is_checked()
        field.uncheck()
        card = _save_slack_settings(admin_page, django_server)
        assert card.locator(
            '[data-settings-source="STAFF_COMMENT_NOTIFY_ENABLED"]',
        ).get_attribute('data-source-badge') == 'db'
        assert not admin_page.locator(
            '#field-STAFF_COMMENT_NOTIFY_ENABLED',
        ).is_checked()

        member_ctx = auth_context(browser, 'main@test.com')
        member_page = member_ctx.new_page()
        member_page.goto(f'{django_server}{LESSON_PATH}', wait_until='networkidle')
        _post_question(member_page, 'First question while paused')
        assert posts == []

        admin_page.locator('#field-STAFF_COMMENT_NOTIFY_ENABLED').check()
        _save_slack_settings(admin_page, django_server)
        assert admin_page.locator(
            '#field-STAFF_COMMENT_NOTIFY_ENABLED',
        ).is_checked()

        member_page.goto(f'{django_server}{LESSON_PATH}', wait_until='networkidle')
        _post_question(member_page, 'Second question after resume')
        assert len(posts) == 1
        assert _preview(posts[0]) == 'Second question after resume'
        member_ctx.close()
        admin_ctx.close()


# ----------------------------------------------------------------------
# Scenario 6: Admin routes comment alerts to a dedicated channel.
# ----------------------------------------------------------------------


@browser_journey
def test_alerts_fall_back_then_route_to_dedicated_channel(
    django_server, browser,
):
    create_user('main@test.com', tier_slug='main', first_name='Mia')
    create_staff_user('admin@test.com')
    _make_lesson()
    _configure(
        django_server,
        STAFF_COMMENT_NOTIFY_CHANNEL_ID=None,
        STAFF_SIGNUP_NOTIFY_CHANNEL_ID='C_STAFF',
    )

    with _capture_slack() as posts:
        member_ctx = auth_context(browser, 'main@test.com')
        member_page = member_ctx.new_page()
        member_page.goto(f'{django_server}{LESSON_PATH}', wait_until='networkidle')
        _post_question(member_page, 'Where is the notebook?')
        assert [p['channel'] for p in posts] == ['C_STAFF']

        admin_ctx = auth_context(browser, 'admin@test.com')
        admin_page = admin_ctx.new_page()
        _open_slack_settings(admin_page, django_server)
        assert admin_page.locator(
            'label[for="field-STAFF_COMMENT_NOTIFY_CHANNEL_ID"]',
        ).inner_text() == 'Staff Comment Notify Channel ID'
        admin_page.locator('#field-STAFF_COMMENT_NOTIFY_CHANNEL_ID').fill(
            'C_COMMENTS',
        )
        _save_slack_settings(admin_page, django_server)
        admin_ctx.close()

        member_page.goto(f'{django_server}{LESSON_PATH}', wait_until='networkidle')
        _post_question(member_page, 'And where is the dataset?')
        assert [p['channel'] for p in posts] == ['C_STAFF', 'C_COMMENTS']
        member_ctx.close()


# ----------------------------------------------------------------------
# Scenario 7: Member threads (Book Club note, sprint plan) stay private.
# ----------------------------------------------------------------------


@browser_journey
def test_member_owned_threads_never_reach_staff_channel(
    django_server, browser,
):
    from django.utils import timezone

    from accounts.models import User
    from bookclub.models import Book, Chapter, Note
    from plans.models import Plan, Sprint, SprintEnrollment

    create_user('main@test.com', tier_slug='main', first_name='Mia')
    create_user('reader@test.com', tier_slug='main', first_name='Rita')
    owner = User.objects.get(email='main@test.com')
    book = Book.objects.create(
        title='Inference Engineering', slug='inference-engineering',
        author='Philip Kiely', required_level=20, status='current',
        start_date=datetime.date(2026, 8, 10),
    )
    chapter = Chapter.objects.create(
        book=book, number=0, title='Chapter 0', week_label='Week 1',
    )
    Note.objects.create(
        chapter=chapter, user=owner, body='Speculative decoding is underrated.',
    )
    sprint = Sprint.objects.create(
        name='Current Sprint', slug='current-sprint-1926',
        start_date=timezone.localdate() - datetime.timedelta(days=1),
    )
    SprintEnrollment.objects.get_or_create(sprint=sprint, user=owner)
    plan = Plan.objects.create(member=owner, sprint=sprint, visibility='cohort')
    plan_id = plan.pk
    connection.close()
    _configure(django_server)

    with _capture_slack() as posts:
        reader_ctx = auth_context(browser, 'reader@test.com')
        reader_page = reader_ctx.new_page()
        reader_page.goto(
            f'{django_server}/books/inference-engineering/chapters/0',
            wait_until='domcontentloaded',
        )
        note_card = reader_page.locator('article').filter(
            has_text='Speculative decoding is underrated.',
        )
        note_card.locator('.qa-new-question').fill('Totally agree.')
        with reader_page.expect_response(
            lambda r: '/api/comments/' in r.url and r.request.method == 'POST',
        ) as response_info:
            note_card.locator('.qa-post-btn').click()
        assert response_info.value.status == 201
        reader_ctx.close()

        owner_ctx = auth_context(browser, 'main@test.com')
        owner_page = owner_ctx.new_page()
        owner_page.goto(
            f'{django_server}/sprints/current-sprint-1926/plan/{plan_id}',
            wait_until='domcontentloaded',
        )
        section = owner_page.locator('[data-testid="plan-comments-section"]')
        _post_question(owner_page, 'A plan discussion note', container=section)

        assert posts == []

        owner_page.goto(f'{django_server}/', wait_until='domcontentloaded')
        owner_page.locator('#notification-bell-btn').click()
        dropdown = owner_page.locator('#notification-dropdown')
        dropdown.wait_for(state='visible', timeout=5000)
        owner_page.wait_for_function(
            "() => { var l = document.getElementById('notification-list');"
            " return l && !l.textContent.includes('Loading'); }",
            timeout=10000,
        )
        assert 'New comment on your note' in dropdown.inner_text()
        owner_ctx.close()


# ----------------------------------------------------------------------
# Scenario 8: Safe, readable preview of a long or tricky comment.
# ----------------------------------------------------------------------


@browser_journey
def test_long_tricky_comment_has_safe_truncated_preview(
    django_server, browser,
):
    create_user('main@test.com', tier_slug='main', first_name='Mia')
    _make_lesson()
    _configure(django_server)
    body = '<!channel> please help\n\n' + '\n'.join(
        f'Line {i}: the retriever returns nothing useful here.'
        for i in range(12)
    )
    assert len(body) > 600

    with _capture_slack() as posts:
        ctx = auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(f'{django_server}{LESSON_PATH}', wait_until='networkidle')
        _post_question(page, body)
        ctx.close()

        assert len(posts) == 1
        preview = _preview(posts[0])
        assert '\n' not in preview
        assert preview.endswith('…')
        unescaped = (
            preview.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        )
        assert len(unescaped) <= 301
        assert preview.startswith('&lt;!channel&gt; please help Line 0')
        assert '<!channel>' not in str(posts[0])


# ----------------------------------------------------------------------
# Scenario 9: Linked instructor keeps their personal notice.
# ----------------------------------------------------------------------


@browser_journey
def test_linked_instructor_keeps_bell_alongside_team_alert(
    django_server, browser,
):
    create_user('main@test.com', tier_slug='main', first_name='Mia')
    create_user('instructor@test.com', tier_slug='free', first_name='Alexey')
    _make_lesson(instructor_email='instructor@test.com')
    _configure(django_server)

    with _capture_slack() as posts:
        member_ctx = auth_context(browser, 'main@test.com')
        member_page = member_ctx.new_page()
        member_page.goto(f'{django_server}{LESSON_PATH}', wait_until='networkidle')
        _post_question(member_page, 'Which embedding model should I use?')
        member_ctx.close()
        assert len(posts) == 1
        assert posts[0]['channel'] == 'C_COMMENTS'

        inst_ctx = auth_context(browser, 'instructor@test.com')
        inst_page = inst_ctx.new_page()
        inst_page.goto(f'{django_server}/', wait_until='domcontentloaded')
        inst_page.locator('#notification-bell-btn').click()
        dropdown = inst_page.locator('#notification-dropdown')
        dropdown.wait_for(state='visible', timeout=5000)
        inst_page.wait_for_function(
            "() => { var l = document.getElementById('notification-list');"
            " return l && !l.textContent.includes('Loading'); }",
            timeout=10000,
        )
        assert 'New comment on Intro to RAG' in dropdown.inner_text()
        inst_page.locator(
            f'#notification-list a[href="{LESSON_PATH}#qa-section"]',
        ).first.click()
        inst_page.wait_for_url(f'**{LESSON_PATH}**')
        _wait_for_qa_text(inst_page, 'Which embedding model should I use?')
        inst_ctx.close()
