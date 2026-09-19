"""Pin the contract of ``templates/community_base/public/base.html``.

The seam is the site's override of the community-base public base template.  It
exists because the package's shared public pages fill a block named ``content``
and a block named ``extra_js``, while this site's ``templates/base.html`` calls
those slots ``body`` and ``extra_scripts``.  Django resolves blocks by name, so
without the seam a package page renders as HTTP 200 with the site chrome and no
body at all -- no exception, no warning, nothing in the response to say why
(community-base#279).

Nothing else in this repo would catch that regression.  Deleting the seam is
caught by the width-contract registry, but renaming ``body`` or ``extra_scripts``
in ``templates/base.html`` would re-break every shared public page silently, and
only at the next package pin bump, when the failure surfaces as a blank page
nobody is looking at.  The package runs the same shape of probe for its Studio
shell contract (``community_base/studio/checks.py``).

Two things are pinned here, because the seam promises both:

* the two contracted block names reach the rendered page, and
* what reaches it is wrapped in this site's real chrome -- header, footer and a
  sanctioned page container -- rather than dropped naked into ``<body>``.

The probe deliberately renders through the real template engine and the real
``base.html`` rather than reading the seam's source.  Source assertions would
pass on exactly the inheritance break this test exists to catch.
"""

from __future__ import annotations

from importlib import import_module

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template import engines
from django.test import RequestFactory, TestCase, tag

SEAM_TEMPLATE = "community_base/public/base.html"

# The two block names a shared community-base public template fills and this
# site's base.html does not define.  The seam's whole job is to bridge them.
CONTENT_SENTINEL = "cb-public-seam-content-sentinel"
EXTRA_JS_SENTINEL = "cb-public-seam-extra-js-sentinel"

# Stands in for a package public page: fills the contracted names, and brings its
# own <main class="cb-page"> the way community_base/mail/unsubscribe.html,
# accounts/login.html and events/registration_result.html all do.
PROBE_SOURCE = (
    '{%% extends "%(seam)s" %%}'
    '{%% block content %%}<main class="cb-page">%(content)s</main>{%% endblock %%}'
    "{%% block extra_js %%}<script>%(extra_js)s</script>{%% endblock %%}"
) % {"seam": SEAM_TEMPLATE, "content": CONTENT_SENTINEL, "extra_js": EXTRA_JS_SENTINEL}

# Chrome markers, each taken from the template that owns it:
# templates/includes/header.html, templates/includes/footer.html, and the Frame
# tier of _docs/design-system.md -> Spacing and Layout.
HEADER_MARKER = 'id="site-header"'
FOOTER_MARKER = "<footer"
CONTAINER_MARKER = "mx-auto max-w-7xl px-4 sm:px-6 lg:px-8"


def _render_probe() -> str:
    """Render the probe the way a request would, so context processors run."""
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = import_module(settings.SESSION_ENGINE).SessionStore()
    return engines["django"].from_string(PROBE_SOURCE).render({}, request)


@tag("core")
class PublicSeamBlockContractTest(TestCase):
    """The contracted block names must survive the trip through the seam."""

    @classmethod
    def setUpTestData(cls):
        cls.rendered = _render_probe()

    def test_content_block_reaches_the_rendered_page(self):
        self.assertIn(
            CONTENT_SENTINEL,
            self.rendered,
            f"A package page's `content` block never reached the page. Either "
            f"{SEAM_TEMPLATE} stopped mapping `content` onto this site's body "
            f"slot, or templates/base.html renamed that slot. Shared public "
            f"pages render blank when this breaks.",
        )

    def test_extra_js_block_reaches_the_rendered_page(self):
        self.assertIn(
            EXTRA_JS_SENTINEL,
            self.rendered,
            f"A package page's `extra_js` block never reached the page. Either "
            f"{SEAM_TEMPLATE} stopped mapping `extra_js` onto this site's script "
            f"slot, or templates/base.html renamed that slot.",
        )


@tag("core")
class PublicSeamChromeContractTest(TestCase):
    """Package content must land inside this site's chrome, not beside it.

    ``templates/base.html`` renders no header and no footer of its own: every
    page supplies them inside its body block, as the three forked package pages
    in ``templates/knowledge_base/`` do.  A seam that forwards ``content`` with
    nothing around it therefore yields a navless, unbranded, container-less page.
    """

    @classmethod
    def setUpTestData(cls):
        cls.rendered = _render_probe()

    def test_header_is_present(self):
        self.assertIn(
            HEADER_MARKER,
            self.rendered,
            "Seam-routed package pages render without the site header "
            "(templates/includes/header.html).",
        )

    def test_footer_is_present(self):
        self.assertIn(
            FOOTER_MARKER,
            self.rendered,
            "Seam-routed package pages render without the site footer "
            "(templates/includes/footer.html).",
        )

    def test_content_sits_inside_the_sanctioned_container(self):
        """Ordering, not mere presence: chrome around the content, not after it."""
        header_at = self.rendered.find(HEADER_MARKER)
        self.assertNotEqual(header_at, -1, "The site header is missing entirely.")
        # base.html wraps its own message drain in the same Frame container just
        # above {% block body %}, so search past the header for the page's own.
        container_at = self.rendered.find(CONTAINER_MARKER, header_at)
        content_at = self.rendered.find(CONTENT_SENTINEL)
        footer_at = self.rendered.rfind(FOOTER_MARKER)

        self.assertNotEqual(
            container_at,
            -1,
            f"No {CONTAINER_MARKER!r} page container wraps seam-routed content "
            f"(_docs/design-system.md -> Spacing and Layout, Frame tier).",
        )
        self.assertLess(container_at, content_at, "Package content must render inside the container.")
        self.assertLess(content_at, footer_at, "The footer must close after the package content.")
