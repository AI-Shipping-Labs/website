"""Pod meeting notes and transcripts journeys (issue #1922).

Fixtures come from ``test_pods_1918``: enrolled anna (Anna K., Main,
Europe/Berlin), raj (Raj S., Main, Asia/Kolkata), mike (Mike K., Main,
America/New_York), basic (Bea S., Basic with course access); staff admin.
Every time is relative to now so the journeys never rot.
"""

import datetime
import os
import re
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.test_pod_meetings_1919 import _meeting, _meeting_pod, _messages, _row
from playwright_tests.test_pods_1918 import _page, _pod_url, _seed, _set_config
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

COURSE_SLUG = 'pods-buildcamp'
TIP = 'Tip: record the call and add the transcript or notes here afterwards.'
PRIVACY = 'Pod members and admins can read this. Remove anything private before uploading.'
VTT = (
    'WEBVTT\n\n'
    '00:00:01.000 --> 00:00:04.000\n'
    'Raj: Let us start with the eval harness demo.\n\n'
    '00:00:05.000 --> 00:00:09.000\n'
    'Anna: Next week I ship the retrieval baseline.\n'
)


def _now_plus(**delta):
    return timezone.now() + datetime.timedelta(**delta)


def _day_label(value, zone):
    local = value.astimezone(ZoneInfo(zone))
    return f'{local:%b} {local.day}'


def _artifact(pod, author, title, body, *, meeting=None, kind='notes'):
    from pods.models import PodArtifact

    artifact = PodArtifact.objects.create(
        pod=pod, meeting=meeting, kind=kind, title=title, body=body, source='pasted', added_by=author,
    )
    connection.close()
    return artifact


def _status(meeting):
    from pods.models import PodMeeting

    status = PodMeeting.objects.get(pk=meeting.pk).status
    connection.close()
    return status


def _is_primary(locator):
    return 'bg-accent' in (locator.get_attribute('class') or '')


def _notes(page):
    return page.get_by_test_id('pod-notes')


def _artifact_url(server, pod, artifact):
    return f'{_pod_url(server, pod)}/notes/{artifact.pk}'


@pytest.mark.core
@browser_journey
def test_member_sees_the_recording_tip_before_the_pods_next_meeting(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])
    _meeting(pod, _now_plus(days=1))
    _meeting(pod, _now_plus(days=8))

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(_row(page, 1).get_by_test_id('pod-meeting-tip')).to_have_text(TIP)
    expect(_row(page, 2).get_by_test_id('pod-meeting-tip')).to_have_count(0)
    expect(page.get_by_test_id('pod-meeting-tip')).to_have_count(1)
    context.close()


@pytest.mark.core
@browser_journey
def test_member_pastes_notes_after_a_meeting_and_the_meeting_counts_as_held(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])
    meeting = _meeting(pod, _now_plus(hours=-2))
    title = f'Meeting 1 - {_day_label(meeting.starts_at, "Europe/Berlin")}'

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    row = _row(page, 1)
    add_notes = row.get_by_test_id('pod-meeting-add-notes')
    assert _is_primary(add_notes)
    assert not _is_primary(row.get_by_test_id('pod-meeting-held'))
    expect(row.get_by_test_id('pod-meeting-not-held')).to_have_text("Didn't happen")

    add_notes.click()
    expect(page.get_by_role('heading', name='Add notes')).to_be_visible()
    expect(page.get_by_label('Meeting', exact=True)).to_have_value(str(meeting.pk))
    expect(page.get_by_label('Title', exact=True)).to_have_value(title)
    expect(page.get_by_test_id('pod-artifact-privacy')).to_have_text(PRIVACY)

    page.get_by_label('Text', exact=True).fill('We demoed the harness.\nRaj owns the retrieval baseline.')
    page.get_by_role('button', name='Save notes').click()

    expect(page).to_have_url(re.compile(r'#notes$'))
    expect(_messages(page)).to_contain_text('Notes saved and meeting marked as held.')
    expect(_row(page, 1).get_by_test_id('pod-meeting-badge')).to_have_text('Held')
    expect(page.get_by_test_id('pod-meetings-progress-summary')).to_have_text('1 of 4 held')
    expect(_row(page, 1).get_by_test_id('pod-meeting-artifact')).to_have_text(f'Notes: {title}')
    expect(_notes(page).get_by_test_id('pod-note-title')).to_have_text([title])
    context.close()


@browser_journey
def test_member_uploads_a_zoom_transcript_file(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj']])
    _meeting(pod, _now_plus(days=-8), status='held')
    _meeting(pod, _now_plus(days=-1), status='held')

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    row = _row(page, 2)
    add_notes = row.get_by_test_id('pod-meeting-add-notes')
    assert not _is_primary(add_notes)
    add_notes.click()

    page.get_by_label('Transcript', exact=True).check()
    page.get_by_label('Or upload a file').set_input_files(
        files=[{'name': 'meeting-2.vtt', 'mimeType': 'text/vtt', 'buffer': VTT.encode('utf-8')}],
    )
    page.get_by_role('button', name='Save notes').click()

    expect(_messages(page)).to_contain_text('Notes saved.')
    notes = _notes(page)
    expect(notes.get_by_test_id('pod-note-kind')).to_have_text(['Transcript'])
    expect(notes.get_by_test_id('pod-note-meta')).to_contain_text('Meeting 2 · Added by Raj S. (you) on ')

    notes.get_by_test_id('pod-note-title').click()
    body = page.get_by_test_id('pod-artifact-body')
    expect(body).to_contain_text('Raj: Let us start with the eval harness demo.')
    # Line breaks are kept: the two cues render on separate lines.
    lines = body.evaluate('el => el.innerText').split('\n')
    assert 'Raj: Let us start with the eval harness demo.' in lines
    assert 'Anna: Next week I ship the retrieval baseline.' in lines
    context.close()


@browser_journey
def test_member_gets_clear_help_when_the_upload_is_wrong(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj']])

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    _notes(page).get_by_test_id('pod-notes-add').click()
    page.get_by_label('Title', exact=True).fill('Kickoff recap')
    page.get_by_role('button', name='Save notes').click()
    expect(page.get_by_test_id('pod-artifact-error-body')).to_have_text('Paste the notes or choose a file.')

    page.get_by_label('Or upload a file').set_input_files(
        files=[{'name': 'notes.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.4'}],
    )
    page.get_by_role('button', name='Save notes').click()
    expect(page.get_by_test_id('pod-artifact-error-body')).to_have_text('Upload a .txt, .md or .vtt file.')
    expect(page.get_by_label('Title', exact=True)).to_have_value('Kickoff recap')
    context.close()


@browser_journey
def test_podmate_reads_the_notes_another_member_added(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['mike']])
    held = _meeting(pod, _now_plus(days=-2), status='held')
    _artifact(pod, users['anna'], 'Meeting 1 recap', 'We agreed on weekly demos.\nMike owns the deploy.', meeting=held)

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    notes = _notes(page)
    notes.scroll_into_view_if_needed()
    expect(notes.get_by_test_id('pod-note-meta')).to_contain_text('Added by Anna K. on ')
    expect(notes).not_to_contain_text('anna@test.com')

    notes.get_by_test_id('pod-note-title').click()
    expect(page.get_by_test_id('pod-artifact-title')).to_have_text('Meeting 1 recap')
    expect(page.get_by_test_id('pod-artifact-body')).to_contain_text('Mike owns the deploy.')
    expect(page.get_by_test_id('pod-artifact-remove-toggle')).to_have_count(0)
    context.close()


@browser_journey
def test_member_removes_notes_they_shared_by_mistake(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj']])
    held = _meeting(pod, _now_plus(days=-2), status='held')
    artifact = _artifact(pod, users['anna'], 'Meeting 1 recap', 'My phone number is in here', meeting=held)

    context, page = _page(browser, 'anna@test.com')
    page.goto(_artifact_url(django_server, pod, artifact), wait_until='domcontentloaded')
    page.get_by_test_id('pod-artifact-remove-toggle').click()
    expect(page.get_by_text("Remove these notes? This can't be undone.")).to_be_visible()
    page.get_by_role('button', name='Yes, remove').click()

    expect(page).to_have_url(re.compile(r'#notes$'))
    expect(_messages(page)).to_contain_text('Notes removed.')
    expect(_notes(page).get_by_test_id('pod-notes-empty')).to_contain_text('No notes yet')
    expect(_row(page, 1).get_by_test_id('pod-meeting-badge')).to_have_text('Held')
    assert _status(held) == 'held'
    context.close()


@browser_journey
def test_someone_outside_the_pod_cannot_read_its_notes(django_server, browser):
    from pods.models import PodJoinRequest

    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj']])
    artifact = _artifact(pod, users['anna'], 'Meeting 1 recap', 'Private pod notes')
    PodJoinRequest.objects.create(pod=pod, user=users['basic'], status='pending')
    connection.close()

    context, page = _page(browser, 'basic@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-detail')).to_be_visible()
    expect(page.get_by_test_id('pod-notes')).to_have_count(0)
    response = page.goto(_artifact_url(django_server, pod, artifact), wait_until='domcontentloaded')
    assert response.status == 404
    expect(page.get_by_text('Private pod notes')).to_have_count(0)
    context.close()


@browser_journey
def test_staff_review_a_pods_transcripts_in_studio(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj']])
    held = _meeting(pod, _now_plus(days=-2), status='held')
    transcript = _artifact(pod, users['raj'], 'Zoom transcript', VTT, meeting=held, kind='transcript')
    notes = _artifact(pod, users['anna'], 'Kickoff notes', 'Short notes')

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/{pod.pk}/#notes', wait_until='domcontentloaded')
    section = page.get_by_test_id('studio-pod-notes')
    expect(section.get_by_test_id('studio-pod-notes-heading')).to_have_text('Notes and transcripts (2)')
    transcript_row = section.locator(f'[data-testid="studio-pod-note"][data-artifact-id="{transcript.pk}"]')
    expect(transcript_row.get_by_test_id('studio-pod-note-kind')).to_have_text('Transcript')
    expect(transcript_row.get_by_test_id('studio-pod-note-meeting')).to_have_text('Meeting 1')
    expect(transcript_row.get_by_test_id('studio-pod-note-author')).to_contain_text('Raj S.')
    expect(transcript_row.get_by_test_id('studio-pod-note-email')).to_have_text('raj@test.com')
    expect(transcript_row.get_by_test_id('studio-pod-note-size')).to_have_text(re.compile(r'^\d+\.\d KB$'))

    transcript_row.get_by_role('link', name='View', exact=True).click()
    expect(page.get_by_test_id('pod-artifact-body')).to_contain_text('Anna: Next week I ship the retrieval baseline.')
    expect(page.get_by_text('Add notes', exact=True)).to_have_count(0)
    expect(page.get_by_test_id('pod-artifact-remove-toggle')).to_have_count(0)

    page.go_back(wait_until='domcontentloaded')
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator(f'[data-testid="studio-pod-note"][data-artifact-id="{notes.pk}"]').get_by_test_id(
        'studio-pod-note-delete',
    ).click()
    expect(page.get_by_text('Notes deleted.')).to_be_visible()
    expect(page.get_by_test_id('studio-pod-notes-heading')).to_have_text('Notes and transcripts (1)')
    context.close()


@browser_journey
def test_pods_without_the_notes_feature_keep_the_plain_held_flow(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj']])
    _meeting(pod, _now_plus(hours=-2))
    _set_config('PODS_ARTIFACTS_ENABLED', 'false')
    try:
        context, page = _page(browser, 'anna@test.com')
        page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
        row = _row(page, 1)
        held = row.get_by_test_id('pod-meeting-held')
        assert _is_primary(held)
        expect(row.get_by_test_id('pod-meeting-add-notes')).to_have_count(0)
        expect(page.get_by_test_id('pod-meeting-tip')).to_have_count(0)
        expect(page.get_by_test_id('pod-notes')).to_have_count(0)

        held.click()
        expect(_messages(page)).to_contain_text('Marked as held.')
        expect(_row(page, 1).get_by_test_id('pod-meeting-badge')).to_have_text('Held')
        context.close()
    finally:
        _set_config('PODS_ARTIFACTS_ENABLED', None)
