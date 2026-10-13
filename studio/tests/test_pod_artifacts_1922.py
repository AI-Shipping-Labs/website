"""Studio pod notes and transcripts (issue #1922)."""

import datetime

from django.test import TestCase, tag
from django.utils import timezone

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from pods.models import PodArtifact, PodMeeting
from pods.tests.fixtures import COURSE_SLUG, enroll, make_cohort, make_course, make_meeting, make_pod, make_user
from pods.tests.test_member_views import texts


@tag('core')
class StudioPodArtifactsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.staff = make_user('admin@test.com', first_name='Sam', last_name='Staff', staff=True)
        cls.anna = make_user('anna@test.com', first_name='Anna', last_name='Klein')
        cls.raj = make_user('raj@test.com', first_name='Raj', last_name='Patel')
        for user in (cls.anna, cls.raj):
            enroll(user, cls.cohort)

    def setUp(self):
        self.pod = make_pod(self.cohort, 'RAG evals', [self.anna, self.raj], owner=self.anna)
        self.client.force_login(self.staff)
        self.addCleanup(clear_config_cache)

    def url(self):
        return f'/studio/pods/{self.pod.pk}/'

    def artifact(self, author, title, body='x' * 2048, *, meeting=None, kind='notes', minutes_ago=0, **extra):
        artifact = PodArtifact.objects.create(
            pod=self.pod, meeting=meeting, kind=kind, title=title, body=body,
            source=extra.pop('source', 'pasted'), added_by=author, **extra,
        )
        if minutes_ago:
            PodArtifact.objects.filter(pk=artifact.pk).update(
                created_at=timezone.now() - datetime.timedelta(minutes=minutes_ago),
            )
        return artifact

    def test_section_after_meetings_lists_rows_with_author_email_and_size(self):
        held = make_meeting(self.pod, timezone.now() - datetime.timedelta(days=2), status='held')
        self.artifact(self.anna, 'Kickoff notes', minutes_ago=30)
        transcript = self.artifact(
            self.raj, 'Zoom transcript', meeting=held, kind='transcript',
            source='uploaded', original_filename='meeting-1.vtt',
        )
        response = self.client.get(self.url())
        html = response.content.decode()
        meetings_at = html.index('data-testid="studio-pod-meetings"')
        notes_at = html.index('data-testid="studio-pod-notes"')
        self.assertLess(meetings_at, notes_at)
        self.assertLess(notes_at, html.index('data-testid="studio-pod-chat"'))
        self.assertIn('id="notes"', html)
        self.assertEqual(texts(response, 'studio-pod-notes-heading'), ['Notes and transcripts (2)'])
        self.assertEqual(texts(response, 'studio-pod-note-title'), ['Zoom transcript meeting-1.vtt', 'Kickoff notes'])
        self.assertEqual(texts(response, 'studio-pod-note-kind'), ['Transcript', 'Notes'])
        self.assertEqual(texts(response, 'studio-pod-note-meeting'), ['Meeting 1', '-'])
        self.assertEqual(texts(response, 'studio-pod-note-author'), ['Raj P. raj@test.com', 'Anna K. anna@test.com'])
        self.assertEqual(texts(response, 'studio-pod-note-size'), ['2.0 KB', '2.0 KB'])
        member_view = f'/courses/{COURSE_SLUG}/home/pods/{self.pod.pk}/notes/{transcript.pk}'
        self.assertIn(f'href="{member_view}"', html)

    def test_section_shows_even_with_the_member_kill_switch_off(self):
        IntegrationSetting.objects.create(key='PODS_ARTIFACTS_ENABLED', value='false')
        clear_config_cache()
        self.artifact(self.anna, 'Kickoff notes')
        response = self.client.get(self.url())
        self.assertEqual(texts(response, 'studio-pod-note-title'), ['Kickoff notes'])

    def test_empty_state(self):
        response = self.client.get(self.url())
        self.assertEqual(texts(response, 'studio-pod-notes-heading'), ['Notes and transcripts (0)'])
        empty = texts(response, 'studio-pod-notes-empty')[0]
        self.assertIn('No notes yet.', empty)

    def test_delete_removes_the_artifact_and_keeps_the_meeting(self):
        held = make_meeting(self.pod, timezone.now() - datetime.timedelta(days=2), status='held')
        artifact = self.artifact(self.anna, 'Kickoff notes', meeting=held)
        keep = self.artifact(self.raj, 'Keep me')
        response = self.client.post(f'/studio/pods/{self.pod.pk}/notes/{artifact.pk}/delete', follow=True)
        self.assertRedirects(response, f'{self.url()}#notes')
        self.assertContains(response, 'Notes deleted.')
        self.assertEqual(list(PodArtifact.objects.values_list('pk', flat=True)), [keep.pk])
        self.assertEqual(PodMeeting.objects.get(pk=held.pk).status, 'held')
        self.assertEqual(texts(response, 'studio-pod-notes-heading'), ['Notes and transcripts (1)'])

    def test_delete_is_staff_only_post_and_scoped_to_the_pod(self):
        artifact = self.artifact(self.anna, 'Kickoff notes')
        other = make_pod(self.cohort, 'Other', [])
        url = f'/studio/pods/{self.pod.pk}/notes/{artifact.pk}/delete'
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(f'/studio/pods/{other.pk}/notes/{artifact.pk}/delete').status_code, 404)
        self.client.force_login(self.anna)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertTrue(PodArtifact.objects.filter(pk=artifact.pk).exists())
