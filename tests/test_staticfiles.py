from django.conf import settings
from django.test import SimpleTestCase

from website import settings as project_settings


class StaticFilesConfigurationTest(SimpleTestCase):
    def test_debug_uses_unhashed_staticfiles_storage(self):
        self.assertEqual(
            settings.STORAGES['staticfiles']['BACKEND'],
            'django.contrib.staticfiles.storage.StaticFilesStorage',
        )

    def test_production_uses_whitenoise_manifest_storage(self):
        self.assertEqual(
            project_settings._staticfiles_storage_backend(debug=False),
            'whitenoise.storage.CompressedManifestStaticFilesStorage',
        )

    def test_whitenoise_middleware_remains_enabled(self):
        self.assertIn(
            'whitenoise.middleware.WhiteNoiseMiddleware',
            settings.MIDDLEWARE,
        )


class DebugStaticFilesServingTest(SimpleTestCase):
    def test_project_static_file_is_served_by_staticfiles_finders(self):
        response = self.client.get('/static/js/mermaid-render.js')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/javascript'))

    def test_admin_prepopulate_static_files_are_served_by_staticfiles_finders(self):
        for path in (
            '/static/admin/js/prepopulate.js',
            '/static/admin/js/prepopulate_init.js',
        ):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response['Content-Type'].startswith('text/javascript'))
