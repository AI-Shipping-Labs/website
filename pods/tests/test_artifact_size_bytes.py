"""Artifact list metadata loads without transcript bodies (perf lane).

Artifact lists (member pod page, Studio table, staff API) used to load
every full row, UTF-8-encoding entire transcript bodies just to show a
size (measured 48.8 MiB of body data for one hundred 500-KiB transcripts).
The size is now a stored column, and list loaders defer ``body``.
"""

from django.db import connection
from django.test import TestCase, tag
from django.test.utils import CaptureQueriesContext

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

    def add_artifacts(self, count):
        PodArtifact.objects.bulk_create([
            PodArtifact(
                pod=self.pod, meeting=None, kind='transcript',
                title=f'Session transcript {index}', body=BODY,
                size_bytes=len(BODY.encode('utf-8')), source='pasted',
                added_by=self.anna,
            )
            for index in range(count)
        ])

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

    def test_shared_metadata_loader_stays_one_query_and_does_not_select_body(self):
        with CaptureQueriesContext(connection) as small_queries:
            small = artifact_svc.pod_artifacts(self.pod)
            small_authors = [row.added_by.email for row in small]
        self.assertEqual(len(small), 1)
        self.assertEqual(len(small_queries), 1)
        self.assertEqual(small_authors, [self.anna.email])

        self.add_artifacts(40)
        with CaptureQueriesContext(connection) as large_queries:
            large = artifact_svc.pod_artifacts(self.pod)
            large_authors = [row.added_by.email for row in large]
        self.assertEqual(len(large), 41)
        self.assertEqual(len(large_queries), 1)
        self.assertEqual(large_authors, [self.anna.email] * 41)

        list_sql = large_queries.captured_queries[0]['sql'].lower()
        self.assertIn('from "pods_podartifact"', list_sql)
        self.assertNotIn('"pods_podartifact"."body"', list_sql)
        self.assertIn('"pods_podartifact"."size_bytes"', list_sql)

    def test_staff_api_query_count_is_constant_and_list_sql_omits_body(self):
        with CaptureQueriesContext(connection) as small_queries:
            small = self.get_list()
        self.assertEqual(small.json()['total'], 1)

        self.add_artifacts(40)
        with CaptureQueriesContext(connection) as large_queries:
            large = self.get_list()
        payload = large.json()
        self.assertEqual(payload['total'], 41)
        self.assertEqual(len(payload['artifacts']), 41)
        self.assertTrue(all('body' not in row for row in payload['artifacts']))
        self.assertTrue(all(
            row['size_bytes'] == len(BODY.encode('utf-8'))
            for row in payload['artifacts']
        ))
        self.assertEqual(len(small_queries), len(large_queries))

        artifact_sql = [
            query['sql'].lower()
            for query in large_queries.captured_queries
            if 'pods_podartifact' in query['sql'].lower()
        ]
        self.assertEqual(len(artifact_sql), 2)  # one COUNT and one page read
        self.assertTrue(all(
            '"pods_podartifact"."body"' not in sql for sql in artifact_sql
        ))
