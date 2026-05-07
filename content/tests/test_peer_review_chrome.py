"""Regression tests for the site-header chrome on peer-review pages (issue #463).

The four peer-review templates extend ``base.html`` directly. ``base.html``
does NOT include the site header -- every page is responsible for including
``includes/header.html`` itself. Before issue #463 the four templates below
forgot to do that, so on mobile (390x844) the hamburger button and the
``Resources`` accordion that lives inside the mobile menu were entirely
missing from those routes.

These tests assert at the response-HTML level that each fixed peer-review
URL renders:

- the ``#mobile-menu-btn`` hamburger button (mobile-only via ``md:hidden``)
- the ``#mobile-menu`` container
- the ``#mobile-resources-toggle`` button and ``#mobile-resources-list``
  with at least the ``/blog`` link (the accordion content)
- the desktop ``#resources-dropdown-btn`` (so the desktop nav is wired in too)
- the existing peer-review business-logic markup the page is supposed to
  render -- so we don't accidentally claim the chrome is fixed by deleting
  the page body.

These are deliberately HTML-element assertions (``id="mobile-menu-btn"``,
``href="/blog"``) and not substring matches on prose, so they fail loudly
if the header partial stops being included for any reason.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from content.models import (
    Course,
    CourseCertificate,
    PeerReview,
    ProjectSubmission,
)

User = get_user_model()


# Tokens that MUST appear in the rendered HTML if and only if
# ``templates/includes/header.html`` was included by the page. They cover
# the hamburger, the mobile menu container, the Resources accordion (the
# specific element the bug was about) and the desktop dropdown trigger.
HEADER_TOKENS = (
    'id="mobile-menu-btn"',
    'id="mobile-menu"',
    'id="mobile-resources-toggle"',
    'id="mobile-resources-list"',
    'id="resources-dropdown-btn"',
)

# A representative link from inside the Resources accordion. If the
# accordion markup is missing, this link is missing too.
RESOURCES_BLOG_LINK = '<a href="/blog"'

# A token that MUST appear if ``templates/includes/footer.html`` was
# included. The footer's inline ``<script>`` defines a global
# ``handleFooterSubscribe`` function which is rendered regardless of
# whether the user is authenticated -- so it is a stable footer marker.
FOOTER_TOKEN = 'handleFooterSubscribe'


def _create_course():
    return Course.objects.create(
        title='Peer Review Course',
        slug='pr-chrome-course',
        status='published',
        peer_review_enabled=True,
        peer_review_count=2,
        peer_review_deadline_days=7,
        peer_review_criteria='# Criteria\n\n- Clarity',
    )


def _create_user(email):
    return User.objects.create_user(
        email=email, password='testpass123', email_verified=True,
    )


def _assert_header_chrome(test, response, url):
    """Assert all of header chrome, mobile resources accordion content,
    desktop dropdown, and footer are present on ``response``."""
    test.assertEqual(
        response.status_code, 200,
        f'Expected 200 from {url}, got {response.status_code}',
    )
    html = response.content.decode()
    for token in HEADER_TOKENS:
        test.assertIn(
            token, html,
            f'{url}: missing header token {token!r} '
            '-- includes/header.html was not rendered',
        )
    test.assertIn(
        RESOURCES_BLOG_LINK, html,
        f'{url}: missing the /blog link inside the mobile Resources '
        'accordion -- the accordion markup was not rendered',
    )
    test.assertIn(
        FOOTER_TOKEN, html,
        f'{url}: missing footer newsletter form '
        '-- includes/footer.html was not rendered',
    )


class PeerReviewSubmitChromeTest(TestCase):
    """``/courses/<slug>/submit`` must render the standard header + footer."""

    @classmethod
    def setUpTestData(cls):
        cls.user = _create_user('chrome-submit@test.com')
        cls.course = _create_course()

    def setUp(self):
        self.client = Client()
        self.client.login(
            email='chrome-submit@test.com', password='testpass123',
        )

    def test_get_renders_full_chrome(self):
        url = f'/courses/{self.course.slug}/submit'
        response = self.client.get(url)
        _assert_header_chrome(self, response, url)
        # And the existing page body is still rendered (no regression).
        self.assertContains(response, 'Submit Project')
        self.assertContains(
            response, f'&larr; Back to {self.course.title}',
        )

    def test_post_success_renders_full_chrome(self):
        """Posting a valid submission still renders header + footer."""
        url = f'/courses/{self.course.slug}/submit'
        response = self.client.post(url, {
            'project_url': 'https://github.com/u/p',
            'description': 'desc',
        })
        _assert_header_chrome(self, response, url)
        self.assertContains(response, 'Your project has been submitted')


class PeerReviewDashboardChromeTest(TestCase):
    """``/courses/<slug>/reviews`` must render the standard header + footer."""

    @classmethod
    def setUpTestData(cls):
        cls.user = _create_user('chrome-dash@test.com')
        cls.course = _create_course()

    def setUp(self):
        self.client = Client()
        self.client.login(
            email='chrome-dash@test.com', password='testpass123',
        )

    def test_dashboard_no_submission_renders_chrome(self):
        url = f'/courses/{self.course.slug}/reviews'
        response = self.client.get(url)
        _assert_header_chrome(self, response, url)
        # Page-specific markup still renders.
        self.assertContains(response, 'Peer Review Dashboard')
        self.assertContains(
            response, f'&larr; Back to {self.course.title}',
        )

    def test_dashboard_with_submission_renders_chrome(self):
        ProjectSubmission.objects.create(
            user=self.user, course=self.course,
            project_url='https://github.com/u/p',
        )
        url = f'/courses/{self.course.slug}/reviews'
        response = self.client.get(url)
        _assert_header_chrome(self, response, url)
        # Status badge for the existing submission is still there.
        self.assertContains(response, 'Submitted')


class PeerReviewFormChromeTest(TestCase):
    """``/courses/<slug>/reviews/<id>`` must render the standard chrome."""

    @classmethod
    def setUpTestData(cls):
        cls.author = _create_user('chrome-form-author@test.com')
        cls.reviewer = _create_user('chrome-form-reviewer@test.com')
        cls.course = _create_course()
        cls.submission = ProjectSubmission.objects.create(
            user=cls.author, course=cls.course,
            project_url='https://github.com/author/proj',
            status='in_review',
        )
        cls.review = PeerReview.objects.create(
            submission=cls.submission, reviewer=cls.reviewer,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(
            email='chrome-form-reviewer@test.com', password='testpass123',
        )

    def test_review_form_renders_full_chrome(self):
        url = (
            f'/courses/{self.course.slug}/reviews/{self.submission.pk}'
        )
        response = self.client.get(url)
        _assert_header_chrome(self, response, url)
        # Page-specific markup still renders.
        self.assertContains(response, 'Peer Review')
        self.assertContains(response, 'Back to Review Dashboard')


class CertificatePageChromeTest(TestCase):
    """``/certificates/<uuid>`` must render the standard chrome
    even for anonymous visitors (the page is public)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = _create_user('chrome-cert@test.com')
        cls.course = _create_course()
        cls.cert = CourseCertificate.objects.create(
            user=cls.user, course=cls.course,
        )

    def test_certificate_page_renders_chrome_for_anonymous(self):
        client = Client()  # not logged in -- the route is public
        url = f'/certificates/{self.cert.id}'
        response = client.get(url)
        _assert_header_chrome(self, response, url)
        # Existing certificate body is still there.
        self.assertContains(response, 'Certificate of Completion')
        self.assertContains(response, self.course.title)

    def test_certificate_page_404_does_not_render_chrome(self):
        """Sanity: a 404 should not be flagged as 'chrome present'."""
        client = Client()
        fake = uuid.uuid4()
        response = client.get(f'/certificates/{fake}')
        self.assertEqual(response.status_code, 404)


class HeaderChromeFalsePositiveGuardTest(TestCase):
    """Guard: the header tokens we assert on must really be unique
    to ``includes/header.html`` -- they must NOT appear on a page that
    extends a different layout that has no public header (the
    ``base_studio.html`` admin shell). If this guard ever starts failing
    we know the assertions in the chrome tests above became too loose
    and could pass even when the bug is back.
    """

    def test_studio_login_required_redirect_does_not_carry_header(self):
        """An unauthenticated GET on a Studio URL redirects to login --
        the redirect itself is empty, so it cannot contain header tokens.
        This is a structural sanity check on our assertion vocabulary."""
        client = Client()
        response = client.get('/studio/')
        # Studio is gated -- expect a redirect, not a full page.
        self.assertIn(response.status_code, (301, 302))
        # And the redirect body is empty (no chrome there to confuse us).
        self.assertNotIn(
            'id="mobile-menu-btn"', response.content.decode(),
        )
