"""Self-hosted Swagger UI docs page checks for issue #1906.

The operator API docs page (``/api/docs``) still referenced
swagger-ui-dist 5.17.14 on cdn.jsdelivr.net after #1846 vendored the
same assets for the member docs; it now serves the pinned files from
the repo via ``{% static %}``. This test pins the template wiring;
``api.tests.test_openapi.DocsPageViewTest`` remains the authority on
the access matrix.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

User = get_user_model()

VENDORED_SWAGGER_REFS = (
    "vendor/swagger-ui-dist/5.17.14/swagger-ui.css",
    "vendor/swagger-ui-dist/5.17.14/swagger-ui-bundle.js",
    "vendor/swagger-ui-dist/5.17.14/swagger-ui-standalone-preset.js",
)


@tag("core")
class ApiDocsSelfHostedAssetsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="docs-staff-1906@test.com", password="pw", is_staff=True,
        )

    def test_staff_gets_vendored_assets_only(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("api_docs"))

        # assertContains enforces a 200 response plus the behavioral
        # contract: the served page is the docs template wired to the
        # vendored swagger-ui-dist assets and our spec endpoint.
        self.assertTemplateUsed(response, "api/docs.html")
        self.assertContains(response, 'id="swagger-ui"')
        self.assertContains(response, "/api/openapi.json")
        content = response.content.decode()
        for ref in VENDORED_SWAGGER_REFS:
            with self.subTest(ref=ref):
                self.assertIn(ref, content)
        # No external asset host may remain on the page: the operator
        # docs used to depend on cdn.jsdelivr.net (issue #1906).
        self.assertNotIn("cdn.jsdelivr.net", content)
