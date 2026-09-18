"""Package-owned Studio pages must render inside the Studio shell (#1737).

`/studio/api-keys/` returned HTTP 200 with the right ``<title>`` and an empty
body for months. The cause was a silent block-name mismatch: the package
template ``community_base/api/api_keys.html`` puts its body in
``{% block content %}``, while ``templates/studio/base.html`` -- reached
through the site override ``templates/community_base/studio/base.html`` --
defines only ``{% block studio_content %}``. Django drops an unmatched block
without warning, so nothing in the test suite or in monitoring noticed.

Any future package surface mounted into Studio can repeat that failure the
same way. This guard is therefore route-driven rather than a list of URLs
somebody has to remember to extend: it walks the Studio URLconf, keeps every
named route that is package-owned (by view module or by the ``community_base_``
route-name prefix), renders each one that serves HTML on a no-argument GET to
a superuser, and requires the Studio header partial in the body.

``data-testid="studio-header"`` is the marker because it is emitted by
``templates/studio/_partials/header_actions.html`` -- the mandatory owner for
every Studio page header -- and it lives inside ``studio_content``. A page
whose content block is dropped keeps its title and loses this.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.urls.exceptions import NoReverseMatch

User = get_user_model()

PACKAGE_MODULE_PREFIX = "community_base."
PACKAGE_ROUTE_NAME_PREFIX = "community_base_"

#: Routes this guard must still be watching. If a future refactor makes the
#: discovery walk return nothing, these keep the test from passing vacuously.
EXPECTED_GUARDED_ROUTES = {"community_base_api_keys", "studio_settings"}

#: Package Studio mounts that already have this exact bug today, found by
#: this guard when it was written for #1737. All four render an empty Studio
#: shell for the same reason ``/studio/api-keys/`` did: their package
#: templates extend ``community_base/studio/base.html`` and put their body in
#: ``{% block content %}``.
#:
#: They are excluded from the header assertion, not forgiven. #1737 scoped
#: itself to the API-key page and explicitly put "fix the block-name bug for
#: other consumers" outside its boundary, and all four of these are redundant
#: with site-owned pages (``/studio/sync/``, ``/studio/worker/``) that already
#: work -- so converting or unmounting them is a product decision, not a
#: drive-by. ``test_known_unconverted_mounts_are_still_broken`` pins them as
#: broken so that fixing or unmounting one fails this file and forces the
#: entry out of this list; the exemption cannot rot into a silent pass.
#: Tracked as #1743.
KNOWN_UNCONVERTED_PACKAGE_ROUTES = frozenset(
    {
        "community_base_content_sources",
        "community_base_content_sync_history",
        "community_base_content_sync_worker",
        "community_base_jobs",
    }
)


def package_owned_studio_routes():
    """Named ``/studio/`` routes served by package code.

    A route qualifies if its view lives in a ``community_base.*`` module (the
    settings dashboard) or if it carries a package route name the site still
    registers as an alias (the API-key pages, which the site now owns but
    which any consumer would reach under the package name).
    """
    found = {}

    def walk(resolver, prefix=""):
        for pattern in resolver.url_patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(pattern, route)
                continue
            if not isinstance(pattern, URLPattern) or not pattern.name:
                continue
            if not route.startswith("studio/"):
                continue
            module = getattr(pattern.callback, "__module__", "") or ""
            is_package = module.startswith(
                PACKAGE_MODULE_PREFIX
            ) or pattern.name.startswith(PACKAGE_ROUTE_NAME_PREFIX)
            if is_package:
                found.setdefault(pattern.name, route)

    walk(get_resolver())
    return found


class PackageStudioMountsRenderTheStudioShellTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            email="mountguard-1737@test.com",
            password="pw",
            is_staff=True,
            is_superuser=True,
        )

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_every_html_package_studio_page_renders_a_studio_header(self):
        guarded = []
        for route_name in sorted(package_owned_studio_routes()):
            try:
                url = reverse(route_name)
            except NoReverseMatch:
                # Takes arguments (per-key revoke, per-group save): not a
                # standalone HTML page, covered by its own view tests.
                continue
            response = self.client.get(url)
            if response.status_code != 200:
                # POST-only endpoints and redirects are not HTML pages.
                continue
            if "text/html" not in response.headers.get("Content-Type", ""):
                # Export endpoints stream JSON, not a Studio page.
                continue
            if route_name in KNOWN_UNCONVERTED_PACKAGE_ROUTES:
                continue
            guarded.append(route_name)
            with self.subTest(route=route_name, url=url):
                self.assertContains(
                    response,
                    'data-testid="studio-header"',
                    msg_prefix=(
                        f"{route_name} ({url}) rendered a Studio response with "
                        "no Studio header. A package template whose body sits "
                        "in {% block content %} is silently dropped by "
                        "studio/base.html, which defines studio_content -- add "
                        "a site override that uses the studio_content block."
                    ),
                )

        self.assertTrue(
            EXPECTED_GUARDED_ROUTES.issubset(set(guarded)),
            f"guard stopped covering {EXPECTED_GUARDED_ROUTES - set(guarded)}; "
            f"it only checked {sorted(guarded)}",
        )

    def test_known_unconverted_mounts_are_still_broken(self):
        """The exemption list is a ledger of open debt, not a mute button.

        Each entry must still reproduce the #1737 failure mode: HTTP 200 with
        the Studio shell and no Studio header. Convert or unmount one of these
        pages and this test fails, which is the prompt to delete its entry.
        """
        for route_name in sorted(KNOWN_UNCONVERTED_PACKAGE_ROUTES):
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                # assertContains/assertNotContains pin the 200 themselves.
                self.assertContains(response, 'id="studio-sidebar-nav"')
                self.assertNotContains(response, 'data-testid="studio-header"')

    def test_discovery_finds_the_package_owned_studio_routes(self):
        discovered = package_owned_studio_routes()

        self.assertIn("community_base_api_keys", discovered)
        self.assertEqual(discovered["community_base_api_keys"], "studio/api-keys/")
        self.assertIn("community_base_settings", discovered)
        self.assertIn("studio_settings", discovered)
