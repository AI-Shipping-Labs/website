"""Unit tests for the ``login_url`` template tag and the header
sign-in link that renders it (issue #594).

The tag mirrors :func:`accounts.templatetags.accounts_extras.logout_url`
and exists so the header's ``Sign in`` link captures the user's current
path as a sanitized ``?next=`` value, sending them back to that page
after successful authentication. The exclusion list and sanitizer are
shared with the logout flow.
"""

from datetime import timedelta
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import RequestFactory, TestCase, tag
from django.utils import timezone

from accounts.templatetags.accounts_extras import login_url
from content.access import LEVEL_OPEN
from content.models import Article

User = get_user_model()


class _AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.anchors.append(dict(attrs))


def _anchors(response):
    parser = _AnchorParser()
    parser.feed(response.content.decode())
    return parser.anchors


def _header_sign_in_links(anchors):
    return [
        anchor
        for anchor in anchors
        if anchor.get("data-testid") == "header-sign-in-link"
    ]


def _render(path):
    """Render the ``login_url`` tag against ``path`` and return the href."""
    factory = RequestFactory()
    request = factory.get(path)
    return login_url({"request": request})


@tag("core")
class LoginUrlTagTest(TestCase):
    """Behavioural coverage for the new ``login_url`` simple tag."""

    def test_returns_base_login_url_when_request_missing(self):
        """No request in context -> plain login URL with no ``?next=``.

        The header is sometimes rendered in error pages or management
        commands without a request; the tag must degrade gracefully.
        """
        self.assertEqual(login_url({}), "/accounts/login/")

    def test_returns_base_login_url_for_homepage(self):
        """On ``/`` the round-trip is pointless -- omit ``?next=``."""
        self.assertEqual(_render("/"), "/accounts/login/")

    def test_appends_next_for_blog_detail_page(self):
        """Public detail page -> capture full path as ``?next=``."""
        self.assertEqual(
            _render("/blog/getting-started-with-llms"),
            "/accounts/login/?next=%2Fblog%2Fgetting-started-with-llms",
        )

    def test_appends_next_for_event_detail_page(self):
        self.assertEqual(
            _render("/events/sample-event"),
            "/accounts/login/?next=%2Fevents%2Fsample-event",
        )

    def test_appends_next_for_course_unit_page(self):
        self.assertEqual(
            _render("/courses/intro-to-ai/module-1/welcome"),
            "/accounts/login/?next=%2Fcourses%2Fintro-to-ai%2Fmodule-1%2Fwelcome",
        )

    def test_appends_next_for_workshop_detail_page(self):
        self.assertEqual(
            _render("/workshops/sample-workshop"),
            "/accounts/login/?next=%2Fworkshops%2Fsample-workshop",
        )

    def test_preserves_query_string_in_next(self):
        """``sanitize_next_url`` keeps query strings on safe local paths."""
        self.assertEqual(
            _render("/pricing?tier=main&billing=monthly"),
            "/accounts/login/?next=%2Fpricing%3Ftier%3Dmain%26billing%3Dmonthly",
        )

    # --- Excluded prefixes: never set ``next=`` to a member-only or auth surface

    def test_excludes_account_settings_page(self):
        self.assertEqual(_render("/account/"), "/accounts/login/")

    def test_excludes_account_subpath(self):
        self.assertEqual(_render("/account/profile"), "/accounts/login/")

    def test_excludes_login_page_itself(self):
        """Sign-in from the login page -> no ``?next=/accounts/login/``.

        Otherwise the post-auth redirect would bounce the user back to
        a page they no longer need to see.
        """
        self.assertEqual(_render("/accounts/login/"), "/accounts/login/")

    def test_excludes_register_page(self):
        self.assertEqual(_render("/accounts/register/"), "/accounts/login/")

    def test_excludes_studio(self):
        self.assertEqual(_render("/studio/articles/"), "/accounts/login/")

    def test_excludes_admin(self):
        self.assertEqual(_render("/admin/"), "/accounts/login/")

    def test_excludes_notifications_feed(self):
        self.assertEqual(_render("/notifications"), "/accounts/login/")

    # --- Open-redirect protection: shared sanitizer rejects unsafe paths

    def test_rejects_request_path_with_backslash(self):
        """``request.get_full_path`` URL-encodes backslashes; the
        sanitizer rejects them anyway as a defense-in-depth check."""
        # Simulate a tampered path. Django's RequestFactory accepts
        # arbitrary bytes; the sanitizer must still drop ``next``.
        factory = RequestFactory()
        request = factory.get("/foo")
        # Rewrite get_full_path to bypass Django's normalization and
        # exercise the sanitizer directly.
        request.get_full_path = lambda: "/foo\\bar"
        self.assertEqual(login_url({"request": request}), "/accounts/login/")

    def test_works_through_template_load(self):
        """Smoke-test that ``{% load accounts_extras %}`` exposes the tag."""
        template = Template(
            "{% load accounts_extras %}{% login_url %}"
        )
        factory = RequestFactory()
        request = factory.get("/blog/example")
        rendered = template.render(Context({"request": request}))
        self.assertEqual(
            rendered, "/accounts/login/?next=%2Fblog%2Fexample"
        )


@tag("core")
class HeaderSignInLinkTest(TestCase):
    """Render the header on a public detail page as an anonymous visitor
    and confirm BOTH the desktop and mobile ``Sign in`` links carry the
    same captured ``?next=`` value (issue #594 acceptance criteria)."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title="Header Login URL Article",
            slug="header-login-url-article",
            status="published",
            published_at=timezone.now() - timedelta(days=1),
            content_markdown="A free article used to render the public header.",
            required_level=LEVEL_OPEN,
            date=timezone.now().date(),
        )

    def test_blog_detail_header_sign_in_links_carry_next(self):
        """Both desktop and mobile ``Sign in`` hrefs include ``?next=``
        pointing back at the current blog post."""
        response = self.client.get(f"/blog/{self.article.slug}")
        self.assertEqual(response.status_code, 200)

        expected_href = (
            f'/accounts/login/?next=%2Fblog%2F{self.article.slug}'
        )
        anchors = _anchors(response)
        header_sign_in_links = _header_sign_in_links(anchors)
        self.assertEqual(
            len(header_sign_in_links),
            2,
            (
                f"Expected the desktop AND mobile Sign-in links to render "
                f"href={expected_href!r}; found {header_sign_in_links!r}."
            ),
        )
        for link in header_sign_in_links:
            self.assertEqual(link.get("href"), expected_href)
            self.assertIn("nofollow", link.get("rel", "").split())
        self.assertNotIn(
            "/accounts/login/",
            [anchor.get("href") for anchor in anchors],
        )

    def test_homepage_header_sign_in_links_have_no_next(self):
        """On ``/`` both Sign-in links render the bare URL with no
        ``?next=`` (round-trip is pointless)."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        header_sign_in_links = _header_sign_in_links(_anchors(response))
        self.assertEqual(len(header_sign_in_links), 2)
        for link in header_sign_in_links:
            self.assertEqual(link.get("href"), "/accounts/login/")
            self.assertIn("nofollow", link.get("rel", "").split())
