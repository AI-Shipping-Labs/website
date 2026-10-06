"""Self-hosted Swagger UI docs page checks for issue #1846.

The docs page used to reference swagger-ui-dist 5.17.14 on
cdn.jsdelivr.net; it now serves the same pinned files from the repo via
``{% static %}``. These tests pin the template wiring and confirm the
access rules are unchanged.
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
class MemberApiDocsSelfHostedAssetsTest(TestCase):
    def test_anonymous_docs_request_redirects_to_login(self):
        # Issue #1846: the remedy must not touch access rules — anonymous
        # visitors are still bounced to login with a next parameter.
        response = self.client.get(reverse("member_api_docs"))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/accounts/login/"))
        self.assertIn("next=/member-api/docs", response["Location"])

    def test_logged_in_member_gets_vendored_assets_only(self):
        user = User.objects.create_user(email="docs-member-1846@test.com")
        self.client.force_login(user)

        response = self.client.get(reverse("member_api_docs"))

        # assertContains enforces a 200 response plus the behavioral
        # contract: the served page is the docs template wired to the
        # vendored swagger-ui-dist assets.
        self.assertTemplateUsed(response, "member_api/docs.html")
        self.assertContains(response, 'data-testid="member-api-docs"')
        content = response.content.decode()
        for ref in VENDORED_SWAGGER_REFS:
            with self.subTest(ref=ref):
                self.assertIn(ref, content)
        # No external asset host may remain on the page: the docs used to
        # gate DOMContentLoaded on a third-party CDN (issue #1846). The
        # usage-guide navigation link still points at GitHub on purpose.
        self.assertNotIn("cdn.jsdelivr.net", content)
