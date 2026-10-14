"""Artifact list metadata loads without transcript bodies (perf lane).

Artifact lists (member pod page, Studio table, staff API) used to load
every full row, UTF-8-encoding entire transcript bodies just to show a
size (measured 48.8 MiB of body data for one hundred 500-KiB transcripts).
The size is now a stored column, and list loaders defer ``body``.
"""

from django.test import TestCase, tag

from accounts.models import Token
from pods.models import PodArtifact
from pods.services import artifacts as artifact_svc
from pods.tests.fixtures import (
    enroll,
    make_cohort,
    make_course,
    make_pod,
    make_user,
)

BODY = 'Ünïcode transcript\n' * 1000


@tag('core')
class ArtifactSizeBytesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.anna = make_user('anna-artifacts@example.com', first_name='Anna', last_name='Klein')
        cls.staff = make_user('staff-artifacts@example.com', staff=True)
        cls.token = Token.objects.create(user=cls.staff, name='pods')
        enroll(cls.anna, cls.cohort)
        cls.pod = make_pod(cls.cohort, 'Artifact size pod', [cls.anna], owner=cls.anna)
        cls.artifact = PodArtifact.objects.create(
            pod=cls.pod, meeting=None, kind='transcript',
            title='Session transcript', body=BODY, source='pasted',
            added_by=cls.anna,
        )

    def get_list(self):
        return self.client.get(
            f'/api/pods/{self.pod.pk}/artifacts',
            HTTP_AUTHORIZATION=f'Token {self.token.key}',
        )

    def test_create_stores_utf8_byte_size(self):
        self.artifact.refresh_from_db()
        self.assertEqual(self.artifact.size_bytes, len(BODY.encode('utf-8')))
        # Non-ASCII proves byte semantics, not character counts.
        self.assertNotEqual(self.artifact.size_bytes, len(BODY))

    def test_list_loader_defers_body_and_keeps_metadata(self):
        rows = artifact_svc.pod_artifacts(self.pod)
        self.assertEqual(len(rows), 1)
        self.assertIn('body', rows[0].get_deferred_fields())
        self.assertEqual(rows[0].size_bytes, len(BODY.encode('utf-8')))
        self.assertEqual(rows[0].title, 'Session transcript')

    def test_detail_loader_still_returns_the_body(self):
        artifact = PodArtifact.objects.select_related('added_by').get(pk=self.artifact.pk)
        self.assertEqual(artifact.body, BODY)

    def test_staff_api_list_reports_stored_size_without_bodies(self):
        response = self.get_list()
        payload = response.json()
        self.assertEqual(payload['total'], 1)
        row = payload['artifacts'][0]
        self.assertEqual(row['title'], 'Session transcript')
        self.assertEqual(row['size_bytes'], len(BODY.encode('utf-8')))
        self.assertNotIn('body', row)
