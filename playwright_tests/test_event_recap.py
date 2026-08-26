"""End-to-end coverage for event recaps (issue #1458).

The recap used to render inline on the event detail page (#393). It now lives
at its own ``/events/<id>/<slug>/recap`` URL, is authored in Studio or through
the staff API, and never has to become an article. Two tests in this module
were rewritten from the superseded #393 expectations
(``test_visitor_finds_rendered_recap_content_inline`` and
``TestEventWithoutRenderedRecap::test_no_recap_link_and_404``) — see the issue
for the owner-approved paper trail.

Usage:
    uv run pytest playwright_tests/test_event_recap.py -v
"""

import datetime
import json
import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Issue #656: this module seeds Events via Event.objects.create and
# cannot run against the deployed dev environment.
pytestmark = [pytest.mark.local_only, pytest.mark.core]

from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

SYNCED_RECAP_HTML = (
    '<h2>Watch the recording</h2>'
    '<section id="watch-stream">'
    '<iframe src="https://www.youtube.com/embed/WQAs1LNxdvM"></iframe>'
    '</section>'
    '<h2>What you need to know</h2>'
    '<article><h3>Execution</h3><p>Ship real projects.</p></article>'
)


def _clear_events():
    from events.models import Event

    Event.objects.all().delete()
    connection.close()


def _create_event(slug='launch', status='completed', **kwargs):
    from events.models import Event

    # Issue #713: ``is_upcoming`` / ``is_past`` are derived from the
    # timestamps, so pin them to match the scenario each test wants.
    if status == 'upcoming':
        start_dt = timezone.now() + datetime.timedelta(days=7)
        end_dt = start_dt + datetime.timedelta(hours=1)
    else:
        start_dt = timezone.now() - datetime.timedelta(days=2)
        end_dt = start_dt + datetime.timedelta(hours=1)

    defaults = {
        'title': 'AI Shipping Labs Community Launch',
        'description': 'A live community session.',
        'start_datetime': start_dt,
        'end_datetime': end_dt,
        'status': status,
        'published': True,
    }
    defaults.update(kwargs)
    event = Event.objects.create(slug=slug, **defaults)
    connection.close()
    return event


def _accept_studio_confirms(page):
    """Accept the Studio event form's "no meeting link" save confirm.

    ``templates/studio/events/form.html`` warns before saving an event with
    no Zoom/custom/external join URL. Playwright dismisses dialogs by
    default, which would silently cancel every save in these tests.
    """
    page.on('dialog', lambda dialog: dialog.accept())


def _dismiss_analytics_prompt(page):
    """Clear the analytics consent banner so it cannot cover the save bar."""
    button = page.get_by_role('button', name='Keep analytics off')
    if button.is_visible():
        with page.expect_navigation(wait_until='domcontentloaded'):
            button.click()


def _create_staff_token(email='admin@test.com'):
    from accounts.models import Token, User

    user = User.objects.get(email=email)
    token = Token.objects.create(user=user, name='recap-e2e')
    key = token.key
    connection.close()
    return key


@pytest.mark.django_db(transaction=True)
class TestStudioAuthoredRecap:
    def test_organiser_writes_notes_in_studio_and_members_read_them(
        self, django_server, browser, page,
    ):
        _clear_events()
        create_staff_user('admin@test.com')
        event = _create_event(
            slug='inference-book-club-kickoff',
            title='Inference Engineering Book Club Kickoff',
        )

        context = auth_context(browser, 'admin@test.com')
        staff_page = context.new_page()
        staff_page.goto(
            f'{django_server}/studio/events/{event.pk}/edit',
            wait_until='domcontentloaded',
        )
        _dismiss_analytics_prompt(staff_page)
        _accept_studio_confirms(staff_page)
        notes = staff_page.get_by_test_id('event-recap-notes')
        expect(notes).to_be_visible()
        assert notes.input_value() == ''

        notes.fill(
            '## What we covered\n\nBatching and KV cache.\n\n'
            '## Next week\n\nSpeculative decoding.'
        )
        with staff_page.expect_navigation(wait_until='domcontentloaded'):
            staff_page.get_by_test_id('sticky-save-action').click()

        notes = staff_page.get_by_test_id('event-recap-notes')
        assert 'What we covered' in notes.input_value()

        staff_page.get_by_test_id('event-recap-view-link').click()
        staff_page.wait_for_load_state('domcontentloaded')
        assert staff_page.url.endswith('/recap')
        expect(
            staff_page.get_by_role('heading', name='What we covered')
        ).to_be_visible()
        expect(
            staff_page.get_by_role('heading', name='Next week')
        ).to_be_visible()
        expect(
            staff_page.get_by_role(
                'heading', name='Inference Engineering Book Club Kickoff',
            )
        ).to_be_visible()
        body = staff_page.content()
        assert '## What we covered' not in body
        recap_url = staff_page.url
        context.close()

        # Anonymous visitor: no login wall, no tier gate.
        page.goto(recap_url, wait_until='domcontentloaded')
        expect(page.get_by_text('Batching and KV cache.')).to_be_visible()
        assert '/login' not in page.url
        assert 'data-testid="gated-access-card"' not in page.content()

    def test_notes_take_precedence_over_synced_recap_without_destroying_it(
        self, django_server, browser,
    ):
        _clear_events()
        create_staff_user('admin@test.com')
        event = _create_event(
            slug='synced-and-noted',
            recap_file='launch/recap.md',
            recap_html=SYNCED_RECAP_HTML,
        )

        context = auth_context(browser, 'admin@test.com')
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/events/{event.pk}/edit',
            wait_until='domcontentloaded',
        )
        _dismiss_analytics_prompt(page)
        _accept_studio_confirms(page)
        conflict = page.get_by_test_id('event-recap-source-conflict')
        expect(conflict).to_contain_text('launch/recap.md')
        expect(conflict).to_contain_text('take precedence')

        page.get_by_test_id('event-recap-notes').fill(
            'Organiser notes for this session'
        )
        with page.expect_navigation(wait_until='domcontentloaded'):
            page.get_by_test_id('sticky-save-action').click()

        recap_url = f'{django_server}/events/{event.pk}/synced-and-noted/recap'
        page.goto(recap_url, wait_until='domcontentloaded')
        body = page.content()
        assert 'Organiser notes for this session' in body
        assert 'Ship real projects.' not in body

        page.goto(
            f'{django_server}/studio/events/{event.pk}/edit',
            wait_until='domcontentloaded',
        )
        _dismiss_analytics_prompt(page)
        _accept_studio_confirms(page)
        page.get_by_test_id('event-recap-notes').fill('')
        with page.expect_navigation(wait_until='domcontentloaded'):
            page.get_by_test_id('sticky-save-action').click()

        page.goto(recap_url, wait_until='domcontentloaded')
        body = page.content()
        assert 'Ship real projects.' in body
        assert 'Organiser notes for this session' not in body
        context.close()


@pytest.mark.django_db(transaction=True)
class TestMemberDiscoversRecap:
    def test_member_moves_from_event_page_to_the_recap_and_back(
        self, django_server, browser,
    ):
        _clear_events()
        create_user('main@test.com', tier_slug='main')
        event = _create_event(
            slug='member-recap-journey',
            recap_notes='## Session notes\n\nWe shipped an eval harness.',
        )

        context = auth_context(browser, 'main@test.com')
        page = context.new_page()
        page.goto(
            f'{django_server}{event.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        expect(page.get_by_text('A live community session.')).to_be_visible()
        cta = page.get_by_test_id('event-recap-cta')
        expect(cta).to_be_visible()

        page.get_by_test_id('event-recap-cta-link').click()
        page.wait_for_load_state('domcontentloaded')
        expect(page.get_by_text('We shipped an eval harness.')).to_be_visible()

        page.get_by_test_id('event-recap-back-cta').click()
        page.wait_for_load_state('domcontentloaded')
        assert page.url.rstrip('/') == (
            f'{django_server}{event.get_absolute_url()}'
        )
        context.close()

    def test_book_club_member_reaches_a_past_meeting_recap(
        self, django_server, browser,
    ):
        from bookclub.models import Book
        from events.models import EventSeries

        _clear_events()
        create_user('main@test.com', tier_slug='main')

        series = EventSeries.objects.create(
            name='Inference Engineering Book Club',
            slug='inference-book-club-series',
            cadence='none',
            day_of_week=None,
            start_time=None,
            timezone='Europe/Berlin',
            required_level=0,
        )
        Book.objects.create(
            title='Inference Engineering',
            slug='inference-engineering',
            author='Philip Kiely',
            required_level=0,
            status='current',
            event_series=series,
        )
        meeting = _create_event(
            slug='book-club-week-1',
            title='Book Club Week 1',
            event_series=series,
            recap_notes='## Week 1 notes\n\nWe read chapters 1-3.',
        )
        connection.close()

        context = auth_context(browser, 'main@test.com')
        page = context.new_page()
        page.goto(
            f'{django_server}{meeting.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        expect(page.get_by_test_id('event-recap-cta')).to_be_visible()

        page.get_by_test_id('event-recap-cta-link').click()
        page.wait_for_load_state('domcontentloaded')
        expect(page.get_by_text('We read chapters 1-3.')).to_be_visible()
        assert page.url.endswith(f'/events/{meeting.pk}/book-club-week-1/recap')
        context.close()


@pytest.mark.django_db(transaction=True)
class TestUnpublishedRecap:
    def test_upcoming_event_never_leaks_draft_notes_to_visitors(
        self, django_server, page,
    ):
        _clear_events()
        event = _create_event(
            slug='upcoming-with-draft-notes',
            status='upcoming',
            recap_notes='Draft notes nobody should read yet.',
        )

        page.goto(
            f'{django_server}{event.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'Read the recap' not in body
        assert 'Draft notes nobody should read yet.' not in body

        page.goto(
            f'{django_server}/events/{event.pk}/upcoming-with-draft-notes/recap',
            wait_until='domcontentloaded',
        )
        # Redirected to the event page, not a 404 dead end.
        assert page.url.rstrip('/') == (
            f'{django_server}{event.get_absolute_url()}'
        )
        assert 'event-anonymous-email-form' in page.content()

    def test_staff_can_preview_an_unpublished_recap_from_studio(
        self, django_server, browser,
    ):
        _clear_events()
        create_staff_user('admin@test.com')
        event = _create_event(
            slug='preview-before-the-session',
            status='upcoming',
            recap_notes='Draft agenda for the session.',
        )

        context = auth_context(browser, 'admin@test.com')
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/events/{event.pk}/edit',
            wait_until='domcontentloaded',
        )
        _dismiss_analytics_prompt(page)
        page.get_by_test_id('event-recap-view-link').click()
        page.wait_for_load_state('domcontentloaded')

        expect(
            page.get_by_test_id('event-recap-unpublished-notice')
        ).to_contain_text('Not visible to members yet')
        expect(page.get_by_text('Draft agenda for the session.')).to_be_visible()
        context.close()


@pytest.mark.django_db(transaction=True)
class TestLegacyAndMissingRecapUrls:
    def test_stale_legacy_recap_links_still_land_somewhere_useful(
        self, django_server, page,
    ):
        _clear_events()
        with_recap = _create_event(
            slug='legacy-with-recap',
            recap_notes='## Recap\n\nWhat happened.',
        )
        without_recap = _create_event(slug='legacy-without-recap')

        page.goto(
            f'{django_server}/events/legacy-with-recap/recap',
            wait_until='domcontentloaded',
        )
        assert page.url.endswith(
            f'/events/{with_recap.pk}/legacy-with-recap/recap'
        )
        expect(page.get_by_text('What happened.')).to_be_visible()

        page.goto(
            f'{django_server}/events/legacy-without-recap/recap',
            wait_until='domcontentloaded',
        )
        assert page.url.rstrip('/') == (
            f'{django_server}{without_recap.get_absolute_url()}'
        )

    def test_event_that_never_got_notes_shows_no_card_and_404s(
        self, django_server, page,
    ):
        _clear_events()
        event = _create_event(slug='test-no-recap')

        page.goto(
            f'{django_server}{event.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'Read the recap' not in body
        assert 'recap coming soon' not in body.lower()

        response = page.goto(
            f'{django_server}/events/{event.pk}/test-no-recap/recap',
            wait_until='domcontentloaded',
        )
        assert response.status == 404


@pytest.mark.django_db(transaction=True)
class TestSyncedContentRepoRecap:
    def test_synced_recap_moves_to_its_own_page_and_still_renders(
        self, django_server, page,
    ):
        _clear_events()
        event = _create_event(
            slug='synced-launch',
            recap_file='launch/recap.md',
            recap_html=SYNCED_RECAP_HTML,
        )

        page.goto(
            f'{django_server}{event.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'A live community session.' in body
        assert 'Ship real projects.' not in body
        expect(page.get_by_test_id('event-recap-cta')).to_be_visible()

        page.get_by_test_id('event-recap-cta-link').click()
        page.wait_for_load_state('domcontentloaded')
        body = page.content()
        assert 'Watch the recording' in body
        assert 'youtube.com/embed/WQAs1LNxdvM' in body
        assert 'Execution' in body
        assert 'Ship real projects.' in body
        assert '<!-- include:' not in body


@pytest.mark.django_db(transaction=True)
class TestRecapThroughTheStaffApi:
    def test_organiser_publishes_weekly_notes_from_a_script(
        self, django_server, page,
    ):
        _clear_events()
        create_staff_user('admin@test.com')
        token = _create_staff_token('admin@test.com')
        event = _create_event(slug='api-published-recap')

        response = page.request.patch(
            f'{django_server}/api/events/api-published-recap',
            headers={
                'Authorization': f'Token {token}',
                'Content-Type': 'application/json',
            },
            data=json.dumps(
                {'recap_notes': '## What we covered\n\nBatching and KV cache.'}
            ),
        )
        assert response.status == 200
        body = response.json()
        assert body['has_recap'] is True
        assert body['recap_published'] is True
        assert body['recap_url'] == (
            f'/events/{event.pk}/api-published-recap/recap'
        )

        page.goto(
            f'{django_server}{body["recap_url"]}',
            wait_until='domcontentloaded',
        )
        expect(
            page.get_by_role('heading', name='What we covered')
        ).to_be_visible()
        expect(page.get_by_text('Batching and KV cache.')).to_be_visible()

        cleared = page.request.patch(
            f'{django_server}/api/events/api-published-recap',
            headers={
                'Authorization': f'Token {token}',
                'Content-Type': 'application/json',
            },
            data=json.dumps({'recap_notes': ''}),
        )
        assert cleared.status == 200
        assert cleared.json()['has_recap'] is False

        gone = page.goto(
            f'{django_server}{body["recap_url"]}',
            wait_until='domcontentloaded',
        )
        assert gone.status == 404


@pytest.mark.django_db(transaction=True)
class TestFollowupEmailRecapLink:
    def test_studio_email_preview_links_the_recap_instead_of_promising_notes(
        self, django_server, browser,
    ):
        _clear_events()
        create_staff_user('admin@test.com')

        context = auth_context(browser, 'admin@test.com')
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/email-templates/'
            f'post_event_followup/edit/',
            wait_until='domcontentloaded',
        )
        expect(page.get_by_test_id('preview-status')).to_have_text(
            'Up to date'
        )
        preview = page.frame_locator(
            '[data-testid="email-template-preview"]'
        ).locator('body')
        expect(preview).to_contain_text('Read the recap')
        rendered = preview.inner_text()
        assert 'notes are still being put together' not in rendered
        context.close()
