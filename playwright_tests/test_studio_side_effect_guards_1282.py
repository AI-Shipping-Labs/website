"""Core browser journeys for Studio irreversible-action guards (#1282)."""

import datetime
import os
from unittest.mock import patch

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = [
    pytest.mark.core,
    pytest.mark.local_only,
    pytest.mark.django_db(transaction=True),
]


def _staff_page(browser, email):
    _create_staff_user(email)
    context = _auth_context(browser, email)
    return context, context.new_page()


def _member(email, *, verified=True):
    _ensure_tiers()
    return _create_user(
        email, tier_slug='free', email_verified=verified,
    )


def _future(days=30):
    return timezone.now() + datetime.timedelta(days=days)


def _dialog_controller(page):
    state = {'accept': False, 'messages': []}

    def handle(dialog):
        state['messages'].append(dialog.message)
        dialog.accept() if state['accept'] else dialog.dismiss()

    page.on('dialog', handle)
    return state


def _side_effect_requests(page):
    requests = []

    def record(request):
        if any(token in request.url for token in (
            '/notify', '/announce-slack', '/impersonate/',
            '/carry-over', '/draft-next-sprint',
        )):
            requests.append(request.url)

    page.on('request', record)
    return requests


def _event(title, slug, *, registrations=0, start=None):
    from accounts.models import User
    from events.models import Event, EventRegistration

    start = start or _future()
    event = Event.objects.create(
        title=title,
        slug=slug,
        start_datetime=start,
        end_datetime=start + datetime.timedelta(hours=1),
        timezone='UTC',
        origin='studio',
        status='upcoming',
        platform='custom',
        zoom_join_url='https://example.test/live',
    )
    for index in range(registrations):
        user = User.objects.create_user(
            email=f'{slug}-{index}@test.com', password='pw',
        )
        EventRegistration.objects.create(event=event, user=user)
    pk = event.pk
    connection.close()
    return pk


def _plan_pair(*, member_email, source_name='Source Sprint',
               target_name='Target Sprint', source_items=(), target_items=()):
    from accounts.models import User
    from plans.models import NextStep, Plan, Sprint

    member = User.objects.get(email=member_email)
    target_start = timezone.localdate() + datetime.timedelta(days=30)
    source_sprint = Sprint.objects.create(
        name=source_name, slug=f'{member.pk}-source-1282',
        start_date=target_start - datetime.timedelta(days=30),
    )
    target_sprint = Sprint.objects.create(
        name=target_name, slug=f'{member.pk}-target-1282',
        start_date=target_start,
    )
    source = Plan.objects.create(member=member, sprint=source_sprint)
    target = Plan.objects.create(member=member, sprint=target_sprint)
    for position, description in enumerate(source_items):
        NextStep.objects.create(
            plan=source, description=description, position=position,
        )
    for position, description in enumerate(target_items):
        NextStep.objects.create(
            plan=target, description=description, position=position,
        )
    ids = source.pk, target.pk
    connection.close()
    return ids


def test_operator_corrects_event_copy_without_alarming_attendees(
    django_server, browser,
):
    from events.models import Event

    event_id = _event('Original title', 'event-copy-1282', registrations=3)
    context, page = _staff_page(browser, 'event-copy-staff@test.com')
    dialogs = _dialog_controller(page)
    page.goto(f'{django_server}/studio/events/{event_id}/edit')

    page.fill('[name="title"]', 'Corrected title')
    page.get_by_test_id('sticky-save-action').click()
    page.wait_for_url(f'{django_server}/studio/events/{event_id}/edit')
    assert dialogs['messages'] == []
    assert Event.objects.get(pk=event_id).title == 'Corrected title'
    connection.close()

    original = Event.objects.get(pk=event_id).start_datetime
    connection.close()
    page.fill('[name="event_date"]', (original + datetime.timedelta(days=1)).strftime('%d/%m/%Y'))
    page.get_by_test_id('sticky-save-action').click()
    assert dialogs['messages'][-1] == (
        'Saving will email 3 registered attendees a rescheduling notice. '
        'This cannot be undone. Continue?'
    )
    assert Event.objects.get(pk=event_id).start_datetime == original
    connection.close()

    dialogs['accept'] = True
    with patch('studio.views.events.enqueue_schedule_update') as enqueue:
        page.get_by_test_id('sticky-save-action').click()
        page.wait_for_url(f'{django_server}/studio/events/{event_id}/edit')
        assert enqueue.call_count == 1
    assert Event.objects.get(pk=event_id).start_datetime != original
    connection.close()
    context.close()


def test_operator_sees_same_guard_for_end_time_changes(
    django_server, browser,
):
    from events.models import Event

    one_id = _event('One attendee', 'duration-one-1282', registrations=1)
    context, page = _staff_page(browser, 'duration-staff@test.com')
    dialogs = _dialog_controller(page)
    page.goto(f'{django_server}/studio/events/{one_id}/edit')
    page.fill('[name="duration_hours"]', '2')
    page.get_by_test_id('sticky-save-action').click()
    assert '1 registered attendee a rescheduling notice' in dialogs['messages'][0]
    assert Event.objects.get(pk=one_id).end_datetime == (
        Event.objects.get(pk=one_id).start_datetime + datetime.timedelta(hours=1)
    )
    connection.close()

    zero_id = _event('Zero attendees', 'duration-zero-1282', registrations=0)
    dialogs['accept'] = True
    page.goto(f'{django_server}/studio/events/{zero_id}/edit')
    dialogs['accept'] = False
    dialogs['messages'].clear()
    page.fill('[name="duration_hours"]', '2')
    page.get_by_test_id('sticky-save-action').click()
    page.wait_for_url(f'{django_server}/studio/events/{zero_id}/edit')
    assert dialogs['messages'] == []

    past_id = _event(
        'Past event', 'duration-past-1282', registrations=1,
        start=timezone.now() - datetime.timedelta(days=3),
    )
    dialogs['accept'] = True
    page.goto(f'{django_server}/studio/events/{past_id}/edit')
    dialogs['accept'] = False
    dialogs['messages'].clear()
    page.fill('[name="duration_hours"]', '2')
    page.get_by_test_id('sticky-save-action').click()
    page.wait_for_url(f'{django_server}/studio/events/{past_id}/edit')
    assert dialogs['messages'] == []
    context.close()


def test_content_operator_understands_every_channel_before_notifying(
    django_server, browser,
):
    from content.models import Article, Course, Download, Workshop
    from events.models import Event

    _member('audience-member@test.com')
    article = Article.objects.create(
        title='Guard article', slug='guard-article-1282', published=True,
        date=datetime.date(2026, 8, 1),
    )
    course = Course.objects.create(
        title='Guard course', slug='guard-course-1282', status='published',
    )
    download = Download.objects.create(
        title='Guard download', slug='guard-download-1282', published=True,
    )
    recording = Event.objects.create(
        title='Guard recording', slug='guard-recording-1282', published=True,
        recording_url='https://example.test/recording',
        start_datetime=_future(),
    )
    event_id = _event('Guard event', 'guard-event-1282')
    workshop = Workshop.objects.create(
        title='Guard workshop', slug='guard-workshop-1282',
        date=datetime.date(2026, 8, 1), status='published',
        landing_required_level=0, pages_required_level=0,
        recording_required_level=0,
    )
    surfaces = [
        (f'/studio/articles/{article.pk}/edit', '#announcements'),
        (f'/studio/courses/{course.pk}/edit', '#announcements'),
        (f'/studio/downloads/{download.pk}/edit', '#announcements'),
        (f'/studio/recordings/{recording.pk}/edit', '#announcements'),
        (f'/studio/events/{event_id}/edit', 'eligible members in app?'),
        (f'/studio/workshops/{workshop.pk}/edit', 'workshop subscribers'),
    ]
    connection.close()
    context, page = _staff_page(browser, 'content-guard-staff@test.com')
    dialogs = _dialog_controller(page)
    requests = _side_effect_requests(page)
    for path, expected in surfaces:
        page.goto(f'{django_server}{path}')
        label = page.locator('#notify-subscribers-btn').inner_text()
        assert label.startswith('Notify ') and ' eligible members' in label
        before = page.locator('#notify-status').get_attribute('style')
        page.locator('#notify-subscribers-btn').click()
        assert expected in dialogs['messages'][-1]
        assert page.locator('#notify-subscribers-btn').is_enabled()
        assert page.locator('#notify-status').get_attribute('style') == before
        page.locator('#post-to-slack-btn').click()
        assert dialogs['messages'][-1] == (
            'Post this announcement to the configured #announcements '
            'channel? This cannot be undone.'
        )
        assert page.locator('#post-to-slack-btn').is_enabled()
    assert requests == []
    context.close()


def test_operator_accepts_one_notification_batch_without_duplicate_requests(
    django_server, browser,
):
    from notifications.models import Notification

    _member('notify-member@test.com')
    event_id = _event('Notify once', 'notify-once-1282')
    context, page = _staff_page(browser, 'notify-staff@test.com')
    dialogs = _dialog_controller(page)
    dialogs['accept'] = True
    requests = _side_effect_requests(page)
    page.goto(f'{django_server}/studio/events/{event_id}/edit')
    button = page.locator('#notify-subscribers-btn')
    button.click()
    page.locator('#notify-status').wait_for(state='visible')
    first_count = Notification.objects.filter(url__contains='notify-once-1282').count()
    assert first_count > 0
    connection.close()
    button.click()
    page.wait_for_function(
        "document.querySelector('#notify-status').textContent.includes('Already notified')"
    )
    assert len([url for url in requests if url.endswith('/notify')]) == 2
    assert Notification.objects.filter(url__contains='notify-once-1282').count() == first_count
    connection.close()
    context.close()


def test_series_operator_previews_lowest_tier_audience_safely(
    django_server, browser,
):
    from events.models import Event, EventSeries
    from notifications.models import Notification

    _member('series-member@test.com')
    series = EventSeries.objects.create(
        name='Mixed access series', slug='mixed-access-1282',
        start_time=datetime.time(18, 0), required_level=30,
    )
    Event.objects.create(
        title='Open occurrence', slug='open-occurrence-1282',
        event_series=series, start_datetime=_future(), status='upcoming',
        required_level=0,
    )
    Event.objects.create(
        title='Premium occurrence', slug='premium-occurrence-1282',
        event_series=series, start_datetime=_future(31), status='upcoming',
        required_level=30,
    )
    series_id = series.pk
    connection.close()
    context, page = _staff_page(browser, 'series-staff@test.com')
    dialogs = _dialog_controller(page)
    requests = _side_effect_requests(page)
    page.goto(f'{django_server}/studio/event-series/{series_id}/')
    label = page.get_by_test_id('event-series-notify').inner_text()
    assert label.startswith('Notify ') and label.endswith(' eligible members')
    page.get_by_test_id('event-series-notify').click()
    assert dialogs['messages'][-1].startswith(label + ' in app?')
    assert not requests
    assert Notification.objects.filter(title='New event series: Mixed access series').count() == 0
    connection.close()
    dialogs['accept'] = True
    page.get_by_test_id('event-series-notify').click()
    page.locator('#series-notify-status').wait_for(state='visible')
    assert len(requests) == 1
    assert Notification.objects.filter(title='New event series: Mixed access series').count() > 0
    connection.close()
    context.close()


def test_operator_dismisses_every_explicit_slack_announcement(
    django_server, browser,
):
    from content.models import Article
    from events.models import Event, EventSeries

    article = Article.objects.create(
        title='Slack article', slug='slack-article-1282', published=True,
        date=datetime.date(2026, 8, 1),
    )
    series = EventSeries.objects.create(
        name='Slack series', slug='slack-series-1282',
        start_time=datetime.time(18, 0),
    )
    Event.objects.create(
        title='Slack session', slug='slack-session-1282',
        event_series=series, start_datetime=_future(), status='upcoming',
    )
    ids = article.pk, series.pk
    connection.close()
    context, page = _staff_page(browser, 'slack-staff@test.com')
    dialogs = _dialog_controller(page)
    requests = _side_effect_requests(page)
    page.goto(f'{django_server}/studio/articles/{ids[0]}/edit')
    page.locator('#post-to-slack-btn').click()
    assert dialogs['messages'][-1] == (
        'Post this announcement to the configured #announcements channel? '
        'This cannot be undone.'
    )
    assert requests == []
    page.route('**/announce-slack', lambda route: route.fulfill(
        status=200, content_type='application/json', body='{"posted": true}',
    ))
    dialogs['accept'] = True
    page.locator('#post-to-slack-btn').click()
    page.locator('#slack-status').wait_for(state='visible')
    assert page.locator('#slack-status').inner_text() == (
        'Slack announcement posted successfully'
    )
    assert len(requests) == 1

    dialogs['accept'] = False
    page.goto(f'{django_server}/studio/event-series/{ids[1]}/')
    page.get_by_test_id('event-series-announce-slack').click()
    assert len(requests) == 1
    dialogs['accept'] = True
    page.get_by_test_id('event-series-announce-slack').click()
    page.locator('#series-slack-status').wait_for(state='visible')
    assert page.locator('#series-slack-status').inner_text() == (
        'Slack announcement posted successfully'
    )
    assert len(requests) == 2
    context.close()


def test_support_operator_deliberately_enters_member_session(
    django_server, browser,
):
    from accounts.models import User
    from crm.models import CRMRecord

    _member('impersonated-member@test.com')
    member = User.objects.get(email='impersonated-member@test.com')
    crm = CRMRecord.objects.create(user=member)
    ids = member.pk, crm.pk
    connection.close()
    context, page = _staff_page(browser, 'impersonator-staff@test.com')
    dialogs = _dialog_controller(page)
    requests = _side_effect_requests(page)
    for path, testid in (
        (f'/studio/users/{ids[0]}/', 'user-detail-impersonate'),
        (f'/studio/crm/{ids[1]}/', 'crm-detail-impersonate'),
    ):
        page.goto(f'{django_server}{path}')
        page.get_by_test_id(testid).click()
        assert dialogs['messages'][-1].startswith(
            'Log in as impersonated-member@test.com?'
        )
        assert page.url.endswith(path)
    assert requests == []

    dialogs['accept'] = True
    with patch('studio.views.impersonate.logger.info') as audit:
        page.get_by_test_id('crm-detail-impersonate').click()
        page.wait_for_url(f'{django_server}/')
        assert audit.call_count == 1
    assert page.get_by_text('You are logged in as impersonated-member@test.com').is_visible()
    context.close()


def test_staff_cannot_cross_superuser_boundary(django_server, browser):
    from accounts.models import User

    superuser = User.objects.create_superuser(
        email='target-superuser@test.com', password='pw',
    )
    target_id = superuser.pk
    connection.close()
    context, page = _staff_page(browser, 'boundary-staff@test.com')
    dialogs = _dialog_controller(page)
    dialogs['accept'] = True
    page.goto(f'{django_server}/studio/users/{target_id}/')
    with patch('studio.views.impersonate.logger.warning') as audit:
        page.get_by_test_id('user-detail-impersonate').click()
        page.wait_for_url(f'{django_server}/studio/users/{target_id}/')
        assert audit.call_count == 1
    assert page.get_by_text('Cannot log in as a superuser.').is_visible()
    assert page.get_by_test_id('user-detail-impersonate').is_visible()

    # A direct form POST still reaches the same server-side refusal.
    with patch('studio.views.impersonate.logger.warning') as audit:
        with page.expect_navigation(wait_until='domcontentloaded'):
            page.locator(
                'form:has([data-testid="user-detail-impersonate"])'
            ).evaluate('(form) => HTMLFormElement.prototype.submit.call(form)')
        assert audit.call_count == 1
    assert page.get_by_text('Cannot log in as a superuser.').is_visible()
    context.close()


def test_operator_previews_idempotent_carry_over_before_copying(
    django_server, browser,
):
    from plans.models import NextStep

    _member('carry-member@test.com')
    source_id, target_id = _plan_pair(
        member_email='carry-member@test.com',
        source_items=('A', 'B', 'C', 'D', 'E'),
        target_items=('A', 'B'),
    )
    context, page = _staff_page(browser, 'carry-staff@test.com')
    dialogs = _dialog_controller(page)
    requests = _side_effect_requests(page)
    page.goto(f'{django_server}/studio/plans/{target_id}/')
    button = page.get_by_test_id('studio-plan-carry-over')
    assert '3 unfinished tasks from Source Sprint into Target Sprint' in button.get_attribute('title')
    button.click()
    assert '3 unfinished tasks from Source Sprint into Target Sprint' in dialogs['messages'][-1]
    assert requests == []
    assert NextStep.objects.filter(plan_id=target_id).count() == 2
    connection.close()
    dialogs['accept'] = True
    button.click()
    page.wait_for_url(f'{django_server}/studio/plans/{target_id}/')
    assert NextStep.objects.filter(plan_id=target_id).count() == 5
    assert NextStep.objects.filter(plan_id=source_id).count() == 5
    connection.close()
    context.close()


def test_operator_starts_advisory_draft_with_full_context(
    django_server, browser,
):
    from plans.models import Plan

    _member('draft-member@test.com')
    _, target_id = _plan_pair(
        member_email='draft-member@test.com', source_items=('A', 'B'),
    )
    context, page = _staff_page(browser, 'draft-staff@test.com')
    dialogs = _dialog_controller(page)
    requests = _side_effect_requests(page)
    page.goto(f'{django_server}/studio/plans/{target_id}/')
    button = page.get_by_test_id('studio-plan-draft-next-sprint')
    title = button.get_attribute('title')
    assert all(fragment in title for fragment in (
        'Source Sprint', 'Target Sprint', '2 unfinished tasks',
        'LLM', 'held for review, not published',
    ))
    original_goal = Plan.objects.get(pk=target_id).goal
    connection.close()
    button.click()
    assert requests == []

    outcome = {
        'carried_over': 2, 'source_plan': object(), 'llm_enabled': False,
        'draft_error': '', 'update_count': 0,
    }
    dialogs['accept'] = True
    with patch(
        'studio.views.plans.draft_next_sprint_plan', return_value=outcome,
    ) as draft:
        button.click()
        page.wait_for_url(f'{django_server}/studio/plans/{target_id}/edit/')
        assert draft.call_count == 1
    assert Plan.objects.get(pk=target_id).goal == original_goal
    connection.close()
    context.close()


def test_plan_confirmations_stay_truthful_at_empty_boundaries(
    django_server, browser,
):
    from accounts.models import User
    from plans.models import NextStep, Plan, Sprint

    _member('empty-plan-member@test.com')
    member = User.objects.get(email='empty-plan-member@test.com')
    no_source_sprint = Sprint.objects.create(
        name='No Source Target', slug='no-source-target-1282',
        start_date=timezone.localdate() + datetime.timedelta(days=30),
    )
    no_source = Plan.objects.create(member=member, sprint=no_source_sprint)
    no_source_id = no_source.pk
    connection.close()
    context, page = _staff_page(browser, 'empty-plan-staff@test.com')
    _dialog_controller(page)
    requests = _side_effect_requests(page)
    page.goto(f'{django_server}/studio/plans/{no_source_id}/')
    carry = page.get_by_test_id('studio-plan-carry-over')
    assert carry.get_attribute('title') == (
        'No prior sprint plan is available to carry into No Source Target. Continue?'
    )
    carry.click()
    assert requests == []

    _member('zero-plan-member@test.com')
    _, zero_target = _plan_pair(
        member_email='zero-plan-member@test.com',
        source_name='Zero Source', target_name='Zero Target',
        source_items=('Already there',), target_items=('Already there',),
    )
    page.goto(f'{django_server}/studio/plans/{zero_target}/')
    carry = page.get_by_test_id('studio-plan-carry-over')
    assert '0 unfinished tasks from Zero Source into Zero Target' in carry.get_attribute('title')
    carry.click()
    assert requests == []
    assert NextStep.objects.filter(plan_id=zero_target).count() == 1
    connection.close()
    context.close()
