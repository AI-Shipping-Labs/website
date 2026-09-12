"""End-to-end coverage for the transcript & recap pipeline (issue #1597).

Scenarios from the groomed issue:

1. Staff opens the Studio event page for an event with a stored transcript
   and recap draft: transcript status reads "stored", the recap notes
   textarea contains the drafted markdown, and the sync action is visible.
2. Anonymous visitor opens a past, published event's recap page after an
   auto-draft: recap content renders, the recording surface is present,
   and no staff-only action is visible.
3. Event whose transcript is marked unavailable: Studio shows
   "unavailable", no recap draft exists, and the public recap page stays
   absent for that event.

Usage:
    uv run pytest playwright_tests/test_event_transcript_recap.py -v
"""

import datetime
import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Issue #656: this module seeds Events via Event.objects.create and
# cannot run against the deployed dev environment.
pytestmark = [pytest.mark.local_only, pytest.mark.core]

from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

DRAFTED_RECAP = (
    '## What we covered\n\nBatching and KV caching for inference.\n\n'
    '## Key takeaways\n\n- Batch requests where possible.'
)


def _clear_events():
    from events.models import Event

    Event.objects.all().delete()
    connection.close()


def _create_past_event(slug, **kwargs):
    from events.models import Event

    start_dt = timezone.now() - datetime.timedelta(days=2)
    end_dt = start_dt + datetime.timedelta(hours=1)
    defaults = {
        'title': slug.replace('-', ' ').title(),
        'description': 'A live community session.',
        'start_datetime': start_dt,
        'end_datetime': end_dt,
        'status': 'completed',
        'published': True,
        'transcript_text': 'A stored transcript of the call.',
        'transcript_url': 'https://zoom.us/rec/download/e2e.vtt',
        'recap_notes': DRAFTED_RECAP,
    }
    defaults.update(kwargs)
    event = Event.objects.create(slug=slug, **defaults)
    connection.close()
    return event


def _dismiss_analytics_prompt(page):
    button = page.get_by_role('button', name='Keep analytics off')
    if button.is_visible():
        with page.expect_navigation(wait_until='domcontentloaded'):
            button.click()


@pytest.mark.django_db(transaction=True)
class TestStudioTranscriptPanel:
    @browser_journey
    def test_staff_sees_stored_status_drafted_notes_and_sync_action(
        self, django_server, browser,
    ):
        _clear_events()
        create_staff_user('admin@test.com')
        archive_url = (
            'https://private-recordings.s3.eu-central-1.amazonaws.com/'
            'recordings/2026/transcript-studio-stored.vtt'
        )
        event = _create_past_event(
            'transcript-studio-stored',
            transcript_s3_url=archive_url,
        )

        context = auth_context(browser, 'admin@test.com')
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/events/{event.pk}/edit',
            wait_until='domcontentloaded',
        )
        _dismiss_analytics_prompt(page)

        status = page.get_by_test_id('transcript-status')
        expect(status).to_be_visible()
        expect(status).to_contain_text('Transcript stored')

        preview = page.get_by_test_id('studio-transcript-preview')
        summary = preview.locator('summary')
        expect(preview).not_to_have_attribute('open', '')

        # Reach the disclosure through the same keyboard sequence an operator
        # uses so :focus-visible, not programmatic focus, drives the ring.
        for _ in range(100):
            page.keyboard.press('Tab')
            if summary.evaluate('(node) => node === document.activeElement'):
                break
        else:
            pytest.fail('Preview transcript was not reachable by keyboard')
        expect(summary).to_be_focused()
        assert summary.evaluate('(node) => node.matches(":focus-visible")')
        focus_style = summary.evaluate(
            '(node) => ({'
            'outlineStyle: getComputedStyle(node).outlineStyle, '
            'boxShadow: getComputedStyle(node).boxShadow'
            '})'
        )
        assert (
            focus_style['outlineStyle'] != 'none'
            or focus_style['boxShadow'] != 'none'
        )
        summary.press('Enter')
        expect(preview).to_have_attribute('open', '')
        body = page.get_by_test_id('studio-transcript-body')
        expect(body).to_have_text('A stored transcript of the call.')
        body_style = body.evaluate(
            '(node) => ({'
            'overflowY: getComputedStyle(node).overflowY, '
            'maxHeight: getComputedStyle(node).maxHeight'
            '})'
        )
        assert body_style['overflowY'] == 'auto'
        assert body_style['maxHeight'].endswith('px')
        assert float(body_style['maxHeight'][:-2]) > 0
        expect(page.get_by_text(archive_url, exact=True)).to_have_count(0)
        expect(
            page.get_by_text('https://zoom.us/rec/download/e2e.vtt', exact=True),
        ).to_have_count(0)
        summary.press('Enter')
        expect(preview).not_to_have_attribute('open', '')

        notes = page.get_by_test_id('event-recap-notes')
        expect(notes).to_be_visible()
        assert 'What we covered' in notes.input_value()

        expect(page.get_by_test_id('sync-transcript-button')).to_be_visible()
        expect(
            page.get_by_test_id('recap-draft-status'),
        ).to_contain_text('Recap draft present')
        context.close()

    @browser_journey
    def test_unavailable_transcript_hides_recap_draft(
        self, django_server, browser,
    ):
        from django.utils import timezone as tz

        _clear_events()
        create_staff_user('admin@test.com')
        events = [
            (
                _create_past_event(
                    'transcript-studio-unavailable',
                    transcript_text='',
                    transcript_url='',
                    transcript_unavailable_at=tz.now(),
                    recap_notes='',
                ),
                'Transcript unavailable',
            ),
            (
                _create_past_event(
                    'transcript-studio-waiting',
                    transcript_text='',
                    transcript_url='https://zoom.us/rec/download/waiting.vtt',
                    recap_notes='',
                ),
                'Transcript URL captured',
            ),
            (
                _create_past_event(
                    'transcript-studio-absent',
                    transcript_text='',
                    transcript_url='',
                    recap_notes='',
                ),
                'No transcript captured yet',
            ),
        ]

        context = auth_context(browser, 'admin@test.com')
        page = context.new_page()
        for event, expected_status in events:
            page.goto(
                f'{django_server}/studio/events/{event.pk}/edit',
                wait_until='domcontentloaded',
            )
            _dismiss_analytics_prompt(page)

            expect(page.get_by_test_id('transcript-status')).to_contain_text(
                expected_status,
            )
            expect(
                page.get_by_test_id('recap-draft-status'),
            ).to_contain_text('No recap draft yet')
            expect(
                page.get_by_test_id('studio-transcript-preview'),
            ).to_have_count(0)
            # The sync action stays available as the explicit recovery path.
            expect(page.get_by_test_id('sync-transcript-button')).to_be_visible()
        context.close()


@pytest.mark.django_db(transaction=True)
class TestPublicRecapAfterAutoDraft:
    @browser_journey
    def test_anonymous_visitor_reads_drafted_recap_with_recording(
        self, django_server, browser,
    ):
        _clear_events()
        archive_url = (
            'https://private-recordings.s3.eu-central-1.amazonaws.com/'
            'recordings/2026/transcript-public-recap.vtt'
        )
        event = _create_past_event(
            'transcript-public-recap',
            recording_s3_url=(
                'https://private-recordings.s3.amazonaws.com/recordings/'
                '2026/transcript-public-recap.mp4'
            ),
            transcript_s3_url=archive_url,
        )

        page = browser.new_page()
        page.goto(
            f'{django_server}/events/{event.pk}/{event.slug}/recap',
            wait_until='domcontentloaded',
        )
        expect(
            page.get_by_text('Batching and KV caching for inference.'),
        ).to_be_visible()
        expect(
            page.get_by_test_id('event-recap-recording-cta'),
        ).to_be_visible()
        expect(page.get_by_test_id('sync-transcript-button')).to_have_count(0)
        expect(page.locator('a[href^="/studio/"]')).to_have_count(0)
        expect(page.get_by_text(archive_url, exact=True)).to_have_count(0)
        expect(
            page.get_by_text('https://zoom.us/rec/download/e2e.vtt', exact=True),
        ).to_have_count(0)
        page.close()

    @browser_journey
    def test_unavailable_transcript_means_no_public_recap(
        self, django_server, browser,
    ):
        _clear_events()
        event = _create_past_event(
            'transcript-none-recap',
            transcript_text='',
            recap_notes='',
            transcript_unavailable_at=timezone.now(),
        )

        page = browser.new_page()
        response = page.goto(
            f'{django_server}/events/{event.pk}/{event.slug}/recap',
            wait_until='domcontentloaded',
        )
        # No recap was drafted, so the public recap page stays absent.
        assert response.status == 404
        page.close()


@pytest.mark.django_db(transaction=True)
class TestSyncTranscriptActionFlow:
    @browser_journey
    def test_staff_sync_requeues_and_reports_via_message(
        self, django_server, browser,
    ):
        _clear_events()
        create_staff_user('admin@test.com')
        event = _create_past_event('transcript-studio-sync')

        context = auth_context(browser, 'admin@test.com')
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/events/{event.pk}/edit',
            wait_until='domcontentloaded',
        )
        _dismiss_analytics_prompt(page)

        with page.expect_navigation(wait_until='domcontentloaded'):
            page.get_by_test_id('sync-transcript-button').click()

        # Back on the edit page with the queued confirmation flashed; the
        # django-q task itself is covered by unit tests.
        assert f"/studio/events/{event.pk}/edit" in page.url
        expect(
            page.get_by_text('Transcript & recap sync queued.'),
        ).to_be_visible()
        context.close()
