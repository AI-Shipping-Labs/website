from datetime import date

from django.core.cache import cache
from django.test import TestCase, override_settings

from content.models import Project


@override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
class ProjectsListCacheTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            title='Cached Project',
            slug='cached-project',
            description='A project shown in the public listing.',
            date=date(2025, 8, 10),
            published=True,
        )

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_anonymous_cache_hit_and_save_delete_invalidation(self):
        initial_response = self.client.get('/projects')

        self.assertContains(initial_response, 'Cached Project')
        with self.assertNumQueries(0):
            cached_response = self.client.get('/projects')
        self.assertContains(cached_response, 'Cached Project')

        self.project.title = 'Updated Project'
        self.project.save()

        updated_response = self.client.get('/projects')
        self.assertContains(updated_response, 'Updated Project')
        self.assertNotContains(updated_response, 'Cached Project')

        self.project.delete()

        deleted_response = self.client.get('/projects')
        self.assertContains(deleted_response, 'No project ideas yet')
        self.assertNotContains(deleted_response, 'Updated Project')
