"""Pod meeting notes and transcripts (issue #1922): service rules, meeting
rows, the Notes and transcripts section, the Add notes form, the artifact
page, removal, access and the kill switch."""

import datetime
import re
from datetime import UTC

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings, tag
from django.utils import timezone
from freezegun import freeze_time

from accounts.templatetags.accounts_extras import button_classes
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from pods.models import PodArtifact, PodJoinRequest, PodMeeting, PodMembership
from pods.services import artifacts as artifact_svc
from pods.services import config as pods_config
from pods.tests.fixtures import COURSE_SLUG, enroll, make_cohort, make_course, make_meeting, make_pod, make_user
from pods.tests.test_meeting_views import message_texts, texts

ENABLED = override_settings(PODS_COURSE_SLUGS=COURSE_SLUG)
EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')
HOUR = datetime.timedelta(hours=1)
DAY = datetime.timedelta(days=1)
TIP = 'Tip: record the call and add the transcript or notes here afterwards.'
PRIVACY = 'Pod members and admins can read this. Remove anything private before uploading.'


def _upload(name, content):
    return SimpleUploadedFile(name, content if isinstance(content, bytes) else content.encode('utf-8'))


def _artifact(pod, author, title='Notes', body='We agreed on the demo.', *, meeting=None, kind='notes',
              minutes_ago=0):
    artifact = PodArtifact.objects.create(
        pod=pod, meeting=meeting, kind=kind, title=title, body=body, source='pasted', added_by=author,
    )
    if minutes_ago:
        PodArtifact.objects.filter(pk=artifact.pk).update(
            created_at=timezone.now() - datetime.timedelta(minutes=minutes_ago),
        )
        artifact.refresh_from_db()
    return artifact


class ArtifactFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.anna = make_user('anna@test.com', first_name='Anna', last_name='Klein', timezone_name='Europe/Berlin')
        cls.raj = make_user('raj@test.com', first_name='Raj', last_name='Patel')
        cls.mike = make_user('mike@test.com', first_name='Mike', last_name='Kowalski')
        cls.lena = make_user('lena@test.com', first_name='Lena', last_name='Weber')
        cls.staff = make_user('staff@test.com', first_name='Sam', staff=True)
        for user in (cls.anna, cls.raj, cls.mike, cls.lena):
            enroll(user, cls.cohort)

    def setUp(self):
        self.pod = make_pod(self.cohort, 'RAG evals', [self.anna, self.raj], owner=self.anna)
        self.addCleanup(clear_config_cache)
        clear_config_cache()

    def _set(self, key, value):
        IntegrationSetting.objects.update_or_create(key=key, defaults={'value': value})
        clear_config_cache()

    def base(self, pod=None):
        pod = pod or self.pod
        return f'/courses/{COURSE_SLUG}/home/pods/{pod.pk}'

    def pod_url(self, pod=None):
        return f'{self.base(pod)}?cohort=4'

    def new_url(self, pod=None, meeting=None):
        url = f'{self.base(pod)}/notes/new'
        return f'{url}?meeting={meeting.pk}' if meeting is not None else url

    def artifact_url(self, artifact, pod=None):
        return f'{self.base(pod)}/notes/{artifact.pk}'

    def section_html(self, response, testid='pod-notes'):
        html = response.content.decode()
        start = html.find(f'data-testid="{testid}"')
        self.assertNotEqual(start, -1, f'{testid} missing')
        return html[start:html.find('</section>', start)]

    def row_html(self, response, meeting):
        html = response.content.decode()
        start = html.find(f'data-meeting-id="{meeting.pk}"')
        self.assertNotEqual(start, -1, 'meeting row missing')
        return html[start:html.find('</li>', start)]

    def action_class(self, row, testid):
        match = re.search(rf'class="([^"]*)" data-testid="{testid}"', row)
        self.assertIsNotNone(match, f'{testid} missing')
        return match.group(1)


# --- Service rules -------------------------------------------------------------

@tag('core')
class CreateArtifactServiceTest(ArtifactFixture):
    def data(self, **values):
        return {'meeting': '', 'kind': 'notes', 'title': 'Our notes', 'text': 'We agreed on the demo.', **values}

    def errors(self, data, upload=None, user=None):
        with self.assertRaises(artifact_svc.ArtifactError) as caught:
            artifact_svc.create_artifact(self.pod, user or self.anna, data, upload)
        return caught.exception.errors

    def test_only_current_members_may_add(self):
        for user in (self.mike, self.staff):
            with self.assertRaises(artifact_svc.ArtifactError) as caught:
                artifact_svc.create_artifact(self.pod, user, self.data())
            self.assertEqual(caught.exception.code, artifact_svc.CODE_NOT_A_MEMBER)
        self.assertFalse(PodArtifact.objects.exists())

    def test_notes_for_a_past_scheduled_meeting_mark_it_held_by_the_member(self):
        meeting = make_meeting(self.pod, timezone.now() - 3 * HOUR)
        artifact, marked = artifact_svc.create_artifact(self.pod, self.raj, self.data(meeting=str(meeting.pk)))
        self.assertTrue(marked)
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'held')
        self.assertEqual(meeting.status_changed_by, self.raj)
        self.assertIsNotNone(meeting.status_changed_at)
        self.assertEqual((artifact.meeting, artifact.added_by, artifact.source), (meeting, self.raj, 'pasted'))

    def test_a_meeting_still_running_is_marked_held_too(self):
        meeting = make_meeting(self.pod, timezone.now() - datetime.timedelta(minutes=10))
        _artifact, marked = artifact_svc.create_artifact(self.pod, self.anna, self.data(meeting=str(meeting.pk)))
        self.assertTrue(marked)
        self.assertEqual(PodMeeting.objects.get(pk=meeting.pk).status, 'held')

    def test_held_meeting_or_no_meeting_keeps_statuses(self):
        held = make_meeting(self.pod, timezone.now() - 2 * DAY, status='held', status_changed_by=self.anna)
        other = make_meeting(self.pod, timezone.now() - 3 * HOUR)
        for raw in (str(held.pk), ''):
            _artifact, marked = artifact_svc.create_artifact(self.pod, self.raj, self.data(meeting=raw))
            self.assertFalse(marked)
        held.refresh_from_db()
        self.assertEqual((held.status, held.status_changed_by), ('held', self.anna))
        self.assertEqual(PodMeeting.objects.get(pk=other.pk).status, 'scheduled')

    def test_meetings_that_did_not_happen_are_rejected(self):
        other_pod = make_pod(self.cohort, 'Other', [self.mike])
        meetings = [
            make_meeting(self.pod, timezone.now() - 3 * HOUR, status='cancelled'),
            make_meeting(self.pod, timezone.now() - 3 * HOUR, status='proposed'),
            make_meeting(self.pod, timezone.now() + DAY),
            make_meeting(other_pod, timezone.now() - 3 * HOUR, status='held'),
        ]
        for meeting in meetings:
            errors = self.errors(self.data(meeting=str(meeting.pk)))
            self.assertEqual(errors, {'meeting': 'Choose a meeting that has already happened.'})
        self.assertEqual(self.errors(self.data(meeting='abc')), {'meeting': 'Choose a meeting that has already happened.'})
        self.assertFalse(PodArtifact.objects.exists())
        self.assertEqual(PodMeeting.objects.get(pk=meetings[2].pk).status, 'scheduled')

    def test_title_is_required_and_capped(self):
        self.assertEqual(self.errors(self.data(title='   ')), {'title': 'Add a title.'})
        self.assertEqual(self.errors(self.data(title='x' * 201)), {'title': 'Titles can be up to 200 characters.'})
        artifact, _ = artifact_svc.create_artifact(self.pod, self.anna, self.data(title='  ' + 'x' * 200 + ' '))
        self.assertEqual(artifact.title, 'x' * 200)

    def test_neither_text_nor_file_is_rejected(self):
        self.assertEqual(self.errors(self.data(text='  \n ')), {'body': 'Paste the notes or choose a file.'})
        self.assertEqual(
            self.errors(self.data(text=''), _upload('empty.txt', ' \n')),
            {'body': 'Paste the notes or choose a file.'},
        )

    def test_all_field_errors_come_together(self):
        meeting = make_meeting(self.pod, timezone.now() + DAY)
        errors = self.errors(self.data(meeting=str(meeting.pk), title='', text=''))
        self.assertEqual(set(errors), {'meeting', 'title', 'body'})

    def test_uploaded_text_files_store_their_utf8_text(self):
        for name in ('notes.txt', 'notes.MD', 'zoom.vtt'):
            content = 'WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nAnna: Grüße aus Berlin\n'
            artifact, _ = artifact_svc.create_artifact(
                self.pod, self.anna, self.data(text='', kind='transcript'), _upload(name, content),
            )
            self.assertEqual(artifact.body, content.strip())
            self.assertEqual((artifact.source, artifact.original_filename, artifact.kind), ('uploaded', name, 'transcript'))

    def test_byte_order_mark_is_dropped(self):
        artifact, _ = artifact_svc.create_artifact(
            self.pod, self.anna, self.data(text=''), _upload('notes.txt', b'\xef\xbb\xbfHello'),
        )
        self.assertEqual(artifact.body, 'Hello')

    def test_other_extensions_and_non_utf8_files_are_rejected(self):
        self.assertEqual(
            self.errors(self.data(text=''), _upload('notes.pdf', '%PDF-1.4')),
            {'body': 'Upload a .txt, .md or .vtt file.'},
        )
        self.assertEqual(
            self.errors(self.data(text=''), _upload('notes', 'no extension')),
            {'body': 'Upload a .txt, .md or .vtt file.'},
        )
        self.assertEqual(
            self.errors(self.data(text=''), _upload('notes.txt', 'Gr\xfc\xdfe'.encode('latin-1'))),
            {'body': 'This file is not plain text. Paste the text instead.'},
        )

    def test_pasted_text_wins_over_a_file(self):
        artifact, _ = artifact_svc.create_artifact(
            self.pod, self.anna, self.data(text='Pasted text'), _upload('notes.pdf', 'ignored'),
        )
        self.assertEqual((artifact.body, artifact.source, artifact.original_filename), ('Pasted text', 'pasted', ''))

    def test_size_cap_follows_the_setting_for_text_and_files(self):
        self._set('PODS_ARTIFACT_MAX_KB', '1')
        too_big = 'é' * 513  # 1026 UTF-8 bytes, 513 characters
        message = 'Notes can be up to 1 KB. Trim the text or split it into two.'
        self.assertEqual(self.errors(self.data(text=too_big)), {'body': message})
        self.assertEqual(self.errors(self.data(text=''), _upload('big.txt', too_big)), {'body': message})
        artifact, _ = artifact_svc.create_artifact(self.pod, self.anna, self.data(text='é' * 512))
        self.assertEqual(artifact.size_bytes, 1024)

    def test_size_cap_falls_back_to_500_kb(self):
        for value in ('abc', '0', '2049', ''):
            self._set('PODS_ARTIFACT_MAX_KB', value)
            self.assertEqual(pods_config.artifact_max_kb(), 500, value)
        self._set('PODS_ARTIFACT_MAX_KB', '2048')
        self.assertEqual(pods_config.artifact_max_kb(), 2048)

    def test_kill_switch_defaults_on(self):
        self.assertTrue(pods_config.artifacts_enabled())
        self._set('PODS_ARTIFACTS_ENABLED', 'false')
        self.assertFalse(pods_config.artifacts_enabled())


# --- Pod page ----------------------------------------------------------------------

@tag('core')
@ENABLED
class MeetingRowNotesTest(ArtifactFixture):
    def test_tip_shows_only_on_the_next_scheduled_meeting(self):
        proposal = make_meeting(self.pod, timezone.now() + 12 * HOUR, status='proposed', proposed_by=self.raj)
        tomorrow = make_meeting(self.pod, timezone.now() + DAY)
        next_week = make_meeting(self.pod, timezone.now() + 7 * DAY)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        self.assertEqual(texts(response, 'pod-meeting-tip'), [TIP])
        self.assertIn('data-testid="pod-meeting-tip"', self.row_html(response, tomorrow))
        for meeting in (proposal, next_week):
            self.assertNotIn('pod-meeting-tip', self.row_html(response, meeting))

    def test_add_notes_is_the_primary_action_after_the_end(self):
        ended = make_meeting(self.pod, timezone.now() - 2 * HOUR)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        row = self.row_html(response, ended)
        self.assertEqual(texts(response, 'pod-meeting-actions'), ["Add notes Mark as held Didn't happen"])
        self.assertEqual(self.action_class(row, 'pod-meeting-add-notes'), button_classes('primary', size='sm'))
        self.assertEqual(self.action_class(row, 'pod-meeting-held'), button_classes('secondary', size='sm'))
        self.assertIn(f'href="{self.new_url(meeting=ended)}"', row)

    def test_running_meeting_keeps_join_call_and_mark_as_held(self):
        self.pod.meeting_url = 'https://meet.google.com/abc-defg-hij'
        self.pod.save(update_fields=['meeting_url'])
        running = make_meeting(self.pod, timezone.now() - datetime.timedelta(minutes=10))
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        row = self.row_html(response, running)
        self.assertNotIn('pod-meeting-add-notes', row)
        self.assertEqual(texts(response, 'pod-meeting-actions'), ["Join call Mark as held Didn't happen"])

    def test_held_rows_offer_secondary_add_notes_cancelled_and_proposals_none(self):
        held = make_meeting(self.pod, timezone.now() - 2 * DAY, status='held')
        cancelled = make_meeting(self.pod, timezone.now() - 3 * DAY, status='cancelled')
        expired = make_meeting(self.pod, timezone.now() - 4 * DAY, status='proposed')
        live = make_meeting(self.pod, timezone.now() + 2 * DAY, status='proposed')
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        row = self.row_html(response, held)
        self.assertEqual(self.action_class(row, 'pod-meeting-add-notes'), button_classes('secondary', size='sm'))
        for meeting in (cancelled, expired, live):
            self.assertNotIn('pod-meeting-add-notes', self.row_html(response, meeting))

    def test_staff_non_member_sees_notes_but_no_add_notes_or_tip(self):
        ended = make_meeting(self.pod, timezone.now() - 2 * HOUR)
        make_meeting(self.pod, timezone.now() + DAY)
        _artifact(self.pod, self.anna, 'Kickoff notes')
        self.client.force_login(self.staff)
        response = self.client.get(self.pod_url())
        self.assertEqual(texts(response, 'pod-note-title'), ['Kickoff notes'])
        self.assertNotContains(response, 'Add notes')
        self.assertNotContains(response, TIP)
        row = self.row_html(response, ended)
        self.assertEqual(self.action_class(row, 'pod-meeting-held'), button_classes('primary', size='sm'))

    def test_meeting_rows_link_their_notes_and_transcripts(self):
        held = make_meeting(self.pod, timezone.now() - 2 * DAY, status='held')
        notes = _artifact(self.pod, self.anna, 'Meeting 1 - recap', meeting=held, minutes_ago=20)
        transcript = _artifact(self.pod, self.raj, 'Zoom transcript', meeting=held, kind='transcript')
        _artifact(self.pod, self.raj, 'Loose notes')
        self.client.force_login(self.raj)
        response = self.client.get(self.pod_url())
        self.assertEqual(
            texts(response, 'pod-meeting-artifact'),
            ['Transcript: Zoom transcript', 'Notes: Meeting 1 - recap'],
        )
        row = self.row_html(response, held)
        self.assertIn(f'href="{self.artifact_url(transcript)}"', row)
        self.assertIn(f'href="{self.artifact_url(notes)}"', row)


@tag('core')
@ENABLED
class NotesSectionTest(ArtifactFixture):
    def test_section_follows_meetings_with_subtitle_and_add_notes(self):
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        html = response.content.decode()
        self.assertIn('id="notes"', html)
        meetings_at = html.index('data-testid="pod-meetings"')
        notes_at = html.index('data-testid="pod-notes"')
        self.assertLess(meetings_at, notes_at)
        # Only the notes section's own opening tag sits between the two.
        self.assertEqual(html.count('<section', meetings_at, notes_at), 1)
        section = self.section_html(response)
        self.assertIn('Notes and transcripts', section)
        self.assertIn('Pod members and admins can read these.', section)
        self.assertEqual(texts(response, 'pod-notes-add'), ['Add notes'])
        self.assertIn(f'href="{self.new_url()}"', section)

    def test_empty_section(self):
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        empty = texts(response, 'pod-notes-empty')[0]
        self.assertIn('No notes yet', empty)
        self.assertIn(
            'After a meeting, add the transcript or your notes so the pod can look back at what you agreed.', empty,
        )

    def test_rows_are_newest_first_with_badge_meta_and_no_email(self):
        make_meeting(self.pod, timezone.now() - 9 * DAY, status='held')
        second = make_meeting(self.pod, timezone.now() - 2 * DAY, status='held')
        _artifact(self.pod, self.raj, 'Loose notes', minutes_ago=60)
        newest = _artifact(self.pod, self.anna, 'Zoom transcript', meeting=second, kind='transcript')
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        self.assertEqual(texts(response, 'pod-note-title'), ['Zoom transcript', 'Loose notes'])
        self.assertEqual(texts(response, 'pod-note-kind'), ['Transcript', 'Notes'])
        day = artifact_svc.compact_date(newest.created_at, 'UTC')
        meta = texts(response, 'pod-note-meta')
        self.assertEqual(meta[0], f'Meeting 2 · Added by Anna K. (you) on {day}')
        self.assertRegex(meta[1], r'^Added by Raj P\. on [A-Z][a-z]{2} \d{1,2}$')
        self.assertIsNone(EMAIL_RE.search(self.section_html(response)))
        self.assertIn(f'href="{self.artifact_url(newest)}"', self.section_html(response))

    def test_kill_switch_hides_section_tip_and_add_notes(self):
        self._set('PODS_ARTIFACTS_ENABLED', 'false')
        ended = make_meeting(self.pod, timezone.now() - 2 * HOUR)
        make_meeting(self.pod, timezone.now() + DAY)
        _artifact(self.pod, self.anna, 'Kickoff notes', meeting=ended)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        self.assertNotContains(response, 'data-testid="pod-notes"')
        self.assertNotContains(response, TIP)
        self.assertNotContains(response, 'Add notes')
        self.assertNotContains(response, 'Kickoff notes')
        row = self.row_html(response, ended)
        self.assertEqual(self.action_class(row, 'pod-meeting-held'), button_classes('primary', size='sm'))


# --- Add notes form -----------------------------------------------------------------

@tag('core')
@ENABLED
class AddNotesFormTest(ArtifactFixture):
    @freeze_time('2026-10-16 10:00:00')  # date-rot-ok: frozen form prefill
    def test_title_prefill_from_meeting_and_today(self):
        first = make_meeting(self.pod, datetime.datetime(2026, 10, 7, 17, 0, tzinfo=UTC), status='held')
        second = make_meeting(self.pod, datetime.datetime(2026, 10, 14, 17, 0, tzinfo=UTC))
        make_meeting(self.pod, datetime.datetime(2026, 10, 21, 17, 0, tzinfo=UTC))
        self.client.force_login(self.anna)
        response = self.client.get(self.new_url(meeting=second))
        html = response.content.decode()
        self.assertIn('value="Meeting 2 - Oct 14"', html)
        self.assertIn(f'<option value="{second.pk}" selected>Meeting 2 - Oct 14</option>', html)
        self.assertIn(f'<option value="{first.pk}">Meeting 1 - Oct 7</option>', html)
        # Only meetings that already happened are offered.
        self.assertEqual(len(re.findall(r'<option value="\d+"', html)), 2)
        response = self.client.get(self.new_url())
        self.assertIn('value="Notes - Oct 16"', response.content.decode())
        self.assertIn('<option value="" selected>Not tied to a meeting</option>', response.content.decode())

    def test_invalid_meeting_falls_back_to_no_meeting(self):
        future = make_meeting(self.pod, timezone.now() + DAY)
        self.client.force_login(self.anna)
        for query in (f'?meeting={future.pk}', '?meeting=abc', '?meeting=999999'):
            response = self.client.get(f'{self.new_url()}{query}')
            self.assertIn('<option value="" selected>Not tied to a meeting</option>', response.content.decode())
            self.assertRegex(response.content.decode(), r'value="Notes - [A-Z][a-z]{2} \d{1,2}"')

    def test_form_fields_privacy_line_and_cancel(self):
        self.client.force_login(self.anna)
        response = self.client.get(self.new_url())
        html = response.content.decode()
        self.assertEqual(texts(response, 'pod-artifact-privacy'), [PRIVACY])
        self.assertIn('enctype="multipart/form-data"', html)
        self.assertIn('accept=".txt,.md,.vtt"', html)
        order = [html.index(f'name="{name}"') for name in ('meeting', 'kind', 'title', 'text', 'file')]
        self.assertEqual(order, sorted(order))
        self.assertIn('value="notes" checked', html)
        self.assertEqual(texts(response, 'pod-artifact-submit'), ['Save notes'])
        self.assertIn(f'href="/courses/{COURSE_SLUG}/home/pods/{self.pod.pk}#notes"', html)

    def test_saving_notes_for_a_past_meeting_marks_it_held(self):
        meeting = make_meeting(self.pod, timezone.now() - 2 * HOUR)
        self.client.force_login(self.anna)
        response = self.client.post(self.new_url(), {
            'meeting': meeting.pk, 'kind': 'notes', 'title': 'Meeting 1 - recap', 'text': 'Line one\nLine two',
        })
        self.assertRedirects(
            response, f'/courses/{COURSE_SLUG}/home/pods/{self.pod.pk}#notes', fetch_redirect_response=False,
        )
        page = self.client.get(self.pod_url())
        self.assertIn('Notes saved and meeting marked as held.', message_texts(page))
        self.assertEqual(texts(page, 'pod-meeting-badge'), ['Held'])
        self.assertEqual(texts(page, 'pod-meeting-artifact'), ['Notes: Meeting 1 - recap'])
        self.assertEqual(PodArtifact.objects.get().body, 'Line one\nLine two')

    def test_saving_without_a_meeting_says_notes_saved(self):
        meeting = make_meeting(self.pod, timezone.now() - 2 * HOUR)
        self.client.force_login(self.raj)
        response = self.client.post(self.new_url(), {'meeting': '', 'title': 'Ideas', 'text': 'x'}, follow=True)
        self.assertIn('Notes saved.', message_texts(response))
        self.assertNotIn('marked as held', message_texts(response))
        self.assertEqual(PodMeeting.objects.get(pk=meeting.pk).status, 'scheduled')

    def test_uploaded_vtt_is_stored_as_a_transcript(self):
        self.client.force_login(self.raj)
        self.client.post(self.new_url(), {
            'kind': 'transcript', 'title': 'Zoom', 'text': '',
            'file': _upload('meeting-2.vtt', 'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi'),
        })
        artifact = PodArtifact.objects.get()
        self.assertEqual(
            (artifact.kind, artifact.source, artifact.original_filename),
            ('transcript', 'uploaded', 'meeting-2.vtt'),
        )

    def test_errors_keep_typed_values(self):
        meeting = make_meeting(self.pod, timezone.now() - 2 * DAY, status='held')
        self.client.force_login(self.anna)
        response = self.client.post(self.new_url(), {
            'meeting': meeting.pk, 'kind': 'transcript', 'title': 'Kept title', 'text': '',
            'file': _upload('notes.pdf', '%PDF'),
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(texts(response, 'pod-artifact-error-body'), ['Upload a .txt, .md or .vtt file.'])
        html = response.content.decode()
        self.assertIn('value="Kept title"', html)
        self.assertIn('value="transcript" checked', html)
        self.assertIn(f'<option value="{meeting.pk}" selected>', html)
        response = self.client.post(self.new_url(), {'title': 'x' * 201, 'text': 'Typed <b>text</b>'})
        self.assertEqual(texts(response, 'pod-artifact-error-title'), ['Titles can be up to 200 characters.'])
        self.assertIn('Typed &lt;b&gt;text&lt;/b&gt;</textarea>', response.content.decode())
        response = self.client.post(self.new_url(), {'title': 'Empty', 'text': ''})
        self.assertEqual(texts(response, 'pod-artifact-error-body'), ['Paste the notes or choose a file.'])
        self.assertFalse(PodArtifact.objects.exists())

    def test_ineligible_meeting_is_rejected_on_post(self):
        proposal = make_meeting(self.pod, timezone.now() + DAY, status='proposed')
        self.client.force_login(self.anna)
        response = self.client.post(self.new_url(), {'meeting': proposal.pk, 'title': 'T', 'text': 'x'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(texts(response, 'pod-artifact-error-meeting'), ['Choose a meeting that has already happened.'])

    def test_over_cap_text_shows_the_setting_in_the_message(self):
        self._set('PODS_ARTIFACT_MAX_KB', '1')
        self.client.force_login(self.anna)
        response = self.client.post(self.new_url(), {'title': 'Big', 'text': 'a' * 1025})
        self.assertEqual(
            texts(response, 'pod-artifact-error-body'),
            ['Notes can be up to 1 KB. Trim the text or split it into two.'],
        )


# --- Artifact page and removal -----------------------------------------------------

@tag('core')
@ENABLED
class ArtifactPageTest(ArtifactFixture):
    def test_body_renders_as_escaped_plain_text_with_line_breaks(self):
        artifact = _artifact(self.pod, self.anna, 'Recap <i>1</i>', '<script>alert(1)</script>\nSecond line')
        self.client.force_login(self.raj)
        response = self.client.get(self.artifact_url(artifact))
        html = response.content.decode()
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;\nSecond line</div>', html)
        self.assertEqual(texts(response, 'pod-artifact-title'), ['Recap <i>1</i>'])
        self.assertEqual(texts(response, 'pod-artifact-kind'), ['Notes'])
        self.assertRegex(texts(response, 'pod-artifact-meta')[0], r'^Added by Anna K\. on ')
        self.assertIn(f'href="/courses/{COURSE_SLUG}/home/pods/{self.pod.pk}#notes"', html)
        self.assertNotContains(response, 'pod-artifact-remove')

    def test_author_removes_after_confirm_and_meeting_stays_held(self):
        held = make_meeting(self.pod, timezone.now() - 2 * DAY, status='held')
        artifact = _artifact(self.pod, self.anna, 'Private detail', meeting=held)
        self.client.force_login(self.anna)
        page = self.client.get(self.artifact_url(artifact))
        self.assertIn("Remove these notes? This can't be undone.", page.content.decode())
        self.assertEqual(texts(page, 'pod-artifact-remove-confirm'), ['Yes, remove'])
        response = self.client.post(f'{self.artifact_url(artifact)}/delete', follow=True)
        self.assertIn('Notes removed.', message_texts(response))
        self.assertFalse(PodArtifact.objects.exists())
        self.assertEqual(PodMeeting.objects.get(pk=held.pk).status, 'held')

    def test_other_members_and_staff_cannot_remove(self):
        artifact = _artifact(self.pod, self.anna)
        for user in (self.raj, self.staff):
            self.client.force_login(user)
            self.assertEqual(self.client.post(f'{self.artifact_url(artifact)}/delete').status_code, 403)
        self.assertTrue(PodArtifact.objects.filter(pk=artifact.pk).exists())

    def test_artifact_of_another_pod_is_404(self):
        other = make_pod(self.cohort, 'Other', [self.mike])
        artifact = _artifact(other, self.mike)
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(self.artifact_url(artifact)).status_code, 404)
        self.assertEqual(self.client.post(f'{self.artifact_url(artifact)}/delete').status_code, 404)


@tag('core')
@ENABLED
class ArtifactAccessTest(ArtifactFixture):
    def urls(self, artifact):
        return [
            ('get', self.new_url()),
            ('post', self.new_url()),
            ('get', self.artifact_url(artifact)),
            ('post', f'{self.artifact_url(artifact)}/delete'),
        ]

    def assert_all(self, artifact, status):
        for method, url in self.urls(artifact):
            response = getattr(self.client, method)(url, {'title': 'T', 'text': 'x'} if method == 'post' else None)
            self.assertEqual(response.status_code, status, f'{method} {url}')

    def test_outsiders_get_404_everywhere(self):
        artifact = _artifact(self.pod, self.anna)
        former = make_user('former@test.com', first_name='Fay')
        enroll(former, self.cohort)
        membership = PodMembership.objects.create(pod=self.pod, user=former, source='staff')
        membership.delete()
        requester = make_user('req@test.com', first_name='Rita')
        waiter = make_user('wait@test.com', first_name='Walt')
        for user in (requester, waiter):
            enroll(user, self.cohort)
        PodJoinRequest.objects.create(pod=self.pod, user=requester, status='pending')
        PodJoinRequest.objects.create(pod=self.pod, user=waiter, status='waitlisted')
        for user in (self.lena, former, requester, waiter):
            self.client.force_login(user)
            page = self.client.get(self.pod_url())
            self.assertNotContains(page, 'data-testid="pod-notes"')
            self.assert_all(artifact, 404)
        self.assertEqual(PodArtifact.objects.count(), 1)

    def test_staff_read_but_cannot_add(self):
        artifact = _artifact(self.pod, self.anna, 'Kickoff')
        self.client.force_login(self.staff)
        self.assertEqual(texts(self.client.get(self.artifact_url(artifact)), 'pod-artifact-title'), ['Kickoff'])
        self.assertEqual(self.client.get(self.new_url()).status_code, 403)
        self.assertEqual(self.client.post(self.new_url(), {'title': 'T', 'text': 'x'}).status_code, 403)
        self.assertEqual(PodArtifact.objects.count(), 1)

    def test_archived_pod_is_404_for_members(self):
        artifact = _artifact(self.pod, self.anna)
        self.pod.status = 'archived'
        self.pod.save(update_fields=['status'])
        self.client.force_login(self.anna)
        self.assert_all(artifact, 404)

    def test_kill_switch_makes_every_member_url_404(self):
        artifact = _artifact(self.pod, self.anna)
        self._set('PODS_ARTIFACTS_ENABLED', 'false')
        for user in (self.anna, self.staff):
            self.client.force_login(user)
            self.assert_all(artifact, 404)
        self.assertTrue(PodArtifact.objects.filter(pk=artifact.pk).exists())

    @override_settings(PODS_COURSE_SLUGS='another-course')
    def test_course_not_switched_on_is_404(self):
        artifact = _artifact(self.pod, self.anna)
        self.client.force_login(self.anna)
        self.assert_all(artifact, 404)

    def test_anonymous_goes_to_login(self):
        artifact = _artifact(self.pod, self.anna)
        response = self.client.get(self.artifact_url(artifact))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
