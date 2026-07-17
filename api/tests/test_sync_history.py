import uuid
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django_q.models import Task

from accounts.models import Token
from content.models import Article
from integrations.models import ContentSource, SyncLog

User = get_user_model()


class SyncHistoryApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email='history-staff@test.com', is_staff=True)
        cls.member = User.objects.create_user(email='history-member@test.com')
        cls.token = Token.objects.create(user=cls.staff, name='history')
        cls.member_token = Token(
            key='sync-history-nonstaff-token',
            user=cls.member,
            name='history-member',
        )
        Token.objects.bulk_create([cls.member_token])
        cls.source = ContentSource.objects.create(
            repo_name='private-org/private-content',
            webhook_secret='secret-never-returned',
            last_sync_status='failed',
            last_synced_at=timezone.now() - timedelta(days=8),
        )
        cls.article = Article.objects.create(
            title='Broken article', slug='broken-article', date=date(2026, 1, 1),
        )
        cls.batch_id = uuid.uuid4()
        cls.log = SyncLog.objects.create(
            source=cls.source,
            batch_id=cls.batch_id,
            status='partial',
            errors=[
                {'file': 'broken-article.md', 'error': 'broken-article parse failed'},
                {'file': 'broken-article.md', 'error': 'broken-article parse failed'},
            ],
            items_detail=[{'content_type': 'article', 'slug': 'broken-article', 'title': 'Broken article', 'action': 'updated'}],
        )

    def auth(self, token=None):
        token = token or self.token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def test_auth_matrix_is_indistinguishable_for_invalid_and_nonstaff(self):
        for headers in ({}, {'HTTP_AUTHORIZATION': 'Token nope'}, self.auth(self.member_token)):
            response = self.client.get('/api/sync/history', **headers)
            self.assertEqual(response.status_code, 401)
            self.assertNotContains(response, self.source.repo_name, status_code=401)

    def test_list_is_compact_and_filterable(self):
        response = self.client.get(
            f'/api/sync/history?source={self.source.pk}&status=partial&page_size=1',
            **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['pagination']['count'], 1)
        self.assertEqual(body['items'][0]['history_id'], str(self.batch_id))
        self.assertEqual(body['items'][0]['status'], 'partial')
        self.assertEqual(body['items'][0]['errors_total'], 2)
        self.assertEqual(body['items'][0]['errors_unique'], 1)
        self.assertNotIn('errors', body['items'][0])
        self.assertNotIn('parse failed', response.content.decode())

    def test_detail_has_complete_deduped_errors_and_target(self):
        response = self.client.get(f'/api/sync/history/{self.batch_id}', **self.auth())
        self.assertEqual(response.status_code, 200)
        error = response.json()['errors'][0]
        self.assertEqual(error['count'], 2)
        self.assertEqual(error['target']['type'], 'article')
        self.assertEqual(error['target']['studio_url'], f'/studio/articles/{self.article.pk}/edit')

    def test_validation_unknowns_and_read_only_methods(self):
        invalid_queries = ('source=nope', 'status=ok', 'page=0', 'page_size=wat')
        for query in invalid_queries:
            response = self.client.get(f'/api/sync/history?{query}', **self.auth())
            self.assertEqual(response.status_code, 422, query)
            self.assertEqual(response.json()['code'], 'validation_error')
        unknown = self.client.get(
            '/api/sync/history?source=00000000-0000-0000-0000-000000000000',
            **self.auth(),
        )
        self.assertEqual(unknown.status_code, 404)
        for method in ('post', 'patch', 'delete'):
            response = getattr(self.client, method)('/api/sync/history', **self.auth())
            self.assertEqual(response.status_code, 405)

    def test_sources_health_is_nested_deterministic_and_private(self):
        response = self.client.get('/api/sync/sources', **self.auth())
        source = response.json()['sources'][0]
        self.assertEqual(source['health']['status'], 'failed')
        self.assertTrue(source['health']['stale'])
        self.assertEqual(source['health']['latest_history_id'], str(self.batch_id))
        self.assertEqual(source['health']['errors_total'], 2)
        self.assertNotIn('webhook_secret', source)
        self.assertNotIn('last_sync_log', source)

    def test_openapi_registers_real_statuses_and_history_paths(self):
        from api.openapi import build_spec
        from api.urls import urlpatterns

        spec = build_spec(urlpatterns)
        example = spec['paths']['/api/sync/sources']['get']['responses']['200']['content']['application/json']['example']['sources'][0]
        self.assertEqual(example['last_sync_status'], 'success')
        self.assertIn('health', example)
        self.assertIn('/api/sync/history', spec['paths'])
        self.assertIn('/api/sync/history/{history_id}', spec['paths'])
        collection_example = spec['paths']['/api/sync/history']['get']['responses']['200']['content']['application/json']['example']
        self.assertEqual(collection_example['items'][0]['status'], 'partial')
        detail_example = spec['paths']['/api/sync/history/{history_id}']['get']['responses']['200']['content']['application/json']['example']
        self.assertEqual(detail_example['status'], 'partial')
        self.assertEqual(detail_example['errors'][0]['count'], 3)

    def test_caught_sync_failure_is_history_not_worker_failure(self):
        Task.objects.create(
            id=uuid.uuid4().hex,
            name='caught-sync-failure',
            func='integrations.services.github.sync_content_source',
            started=timezone.now(),
            stopped=timezone.now(),
            success=True,
            result=None,
        )
        worker = self.client.get('/api/worker/tasks/failed', **self.auth())
        history = self.client.get('/api/sync/history?status=partial', **self.auth())
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker.json()['count'], 0)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()['pagination']['count'], 1)
