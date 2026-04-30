from django.conf import settings
from django.contrib.staticfiles.views import serve
from django.test import RequestFactory, SimpleTestCase

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
    def setUp(self):
        self.request_factory = RequestFactory()

    def get_static_response(self, path):
        request = self.request_factory.get(f'{settings.STATIC_URL}{path}')
        return serve(request, path, insecure=True)

    def test_project_static_file_is_served_by_staticfiles_finders(self):
        response = self.get_static_response('js/mermaid-render.js')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/javascript'))

    def test_admin_prepopulate_static_files_are_served_by_staticfiles_finders(self):
        for path in (
            'admin/js/prepopulate.js',
            'admin/js/prepopulate_init.js',
        ):
            with self.subTest(path=path):
                response = self.get_static_response(path)

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response['Content-Type'].startswith('text/javascript'))
