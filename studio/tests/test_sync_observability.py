import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from integrations.models import ContentSource, SyncLog

User = get_user_model()


class StudioSyncObservabilityTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='sync-observability@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='sync-observability-member@test.com', password='pw',
        )
        cls.source = ContentSource.objects.create(
            repo_name='org/a-very-long-private-repository-name',
            last_sync_status='partial',
            last_synced_at=timezone.now() - timedelta(days=8),
        )
        cls.other = ContentSource.objects.create(
            repo_name='org/other', last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        cls.batch_id = uuid.uuid4()
        cls.log = SyncLog.objects.create(
            source=cls.source,
            batch_id=cls.batch_id,
            status='failed',
            errors=[
                {'file': 'very/long/path.md', 'error': 'Repeated failure'},
                {'file': 'very/long/path.md', 'error': 'Repeated failure'},
            ],
        )
        SyncLog.objects.create(
            source=cls.other,
            batch_id=cls.batch_id,
            status='running',
        )

    def setUp(self):
        self.client.login(email=self.staff.email, password='pw')

    def test_dashboard_full_and_fragment_share_health_errors_anchors_and_help(self):
        for url in ('/studio/sync/', '/studio/sync/?fragment=status'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'data-testid="sync-health-summary"')
            self.assertContains(response, f'href="#sync-source-{self.source.pk}"')
            self.assertContains(response, f'id="sync-source-{self.source.pk}"')
            self.assertContains(response, 'Content refreshed')
            self.assertContains(response, '2 total / 1 unique')
            self.assertContains(response, '2 errors (1 unique)')
            self.assertContains(response, '×2')
            self.assertContains(
                response,
                'Sync now applies changed files; Force resync re-imports everything from the repo.',
            )
            self.assertContains(response, '<details', html=False)
            self.assertEqual(response.content.decode().count('Repeated failure'), 1)

    def test_history_source_filter_uses_scoped_batch_status(self):
        response = self.client.get(
            f'/studio/sync/history/?source={self.source.pk}&status=failed',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['batches']), 1)
        self.assertEqual(response.context['batches'][0]['overall_status'], 'failed')
        self.assertContains(response, 'name="source"')
        self.assertContains(response, 'name="status"')
        self.assertContains(response, 'Completed with errors')

    def test_invalid_filters_never_broaden_and_have_clear_action(self):
        response = self.client.get('/studio/sync/history/?source=tampered&status=ok')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['batches'], [])
        self.assertContains(response, 'No sync history matches these filters.')
        self.assertContains(response, 'Clear filters')

    def test_access_matrix(self):
        self.client.logout()
        anonymous = self.client.get('/studio/sync/history/')
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn('next=', anonymous.url)
        self.client.login(email=self.member.email, password='pw')
        self.assertEqual(self.client.get('/studio/sync/history/').status_code, 403)

    def test_51_logical_batches_filter_preserving_pager(self):
        for _ in range(51):
            SyncLog.objects.create(source=self.source, status='failed')
        response = self.client.get(
            f'/studio/sync/history/?source={self.source.pk}&status=failed&page=1',
        )
        self.assertEqual(len(response.context['batches']), 50)
        self.assertContains(response, f'source={self.source.pk}')
        self.assertContains(response, 'status=failed')
        self.assertContains(response, 'page=2')
