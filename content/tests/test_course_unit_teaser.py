"""Tests for the locked-lesson teaser preview — issue #248.

When a non-eligible visitor (anonymous or wrong-tier) opens a gated unit
the view used to return a 403 with a bare upgrade CTA. The new behavior
is to return a 403 with a teaser body (title, breadcrumb, video
thumbnail, ~150 words of body with a fade-out, homework intro, upgrade
CTA) so the visitor sees what they'd be unlocking.

Covers:

* Non-eligible authenticated user → teaser body + upgrade CTA
* Anonymous user → teaser body + signup CTA + upgrade CTA
* Eligible user → full unit page (regression check)
* Preview unit → full unit page for everyone (regression check)
* Empty unit body → fall back to the original lock card (no teaser
  leakage / no empty fade-out)
* Course outline syllabus rows are clickable for locked units
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.access import LEVEL_MAIN
from content.models import Course, Module, Unit
from tests.fixtures import TierSetupMixin

User = get_user_model()


class CourseUnitTeaserSetupMixin(TierSetupMixin):
    """Provides a paid course with a unit that has body, homework, video."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            title='Teaser Course', slug='teaser-course',
            status='published', required_level=LEVEL_MAIN,
            description='A paid course used for teaser tests.',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module One', slug='module-one', sort_order=1,
        )
        # Build a body that is clearly longer than 150 words so we can
        # assert truncation. We mix in headings and lists so the
        # tag-balanced truncator gets exercised.
        long_body_paragraphs = []
        for i in range(8):
            long_body_paragraphs.append(
                f'Paragraph {i} sentence one with several distinct teaser words. '
                f'Paragraph {i} sentence two adds even more recognisable words.'
            )
        long_body = (
            '# Lesson Intro\n\n'
            'This first paragraph contains the unique phrase TEASERINTROMARKER which '
            'we expect to render. '
            + '\n\n'.join(long_body_paragraphs)
            + '\n\nFINAL_PARAGRAPH_MARKER which should NOT appear in the teaser '
            'because it sits well past the 150-word cutoff and would only show '
            'to a paying user who can read the whole lesson.'
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Lesson One', slug='lesson-one', sort_order=1,
            body=long_body,
            homework='## Build a thing\nAssemble a working pipeline. Then write a brief report.',
            video_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        )
        cls.preview_unit = Unit.objects.create(
            module=cls.module, title='Free Preview', slug='free-preview', sort_order=2,
            body='Preview body that anyone can read.',
            is_preview=True,
        )
        cls.empty_body_unit = Unit.objects.create(
            module=cls.module, title='Empty Body Unit', slug='empty-body-unit', sort_order=3,
            body='',
        )
        cls.unit_url = '/courses/teaser-course/module-one/lesson-one'
        cls.preview_url = '/courses/teaser-course/module-one/free-preview'
        cls.empty_url = '/courses/teaser-course/module-one/empty-body-unit'


# ------------------------------------------------------------
# Non-eligible authenticated user
# ------------------------------------------------------------


class NonEligibleUserTeaserTest(CourseUnitTeaserSetupMixin, TestCase):
    """Authenticated user without the required tier sees a teaser, not a wall."""

    def setUp(self):
        self.user = User.objects.create_user(email='basic@test.com', password='testpass')
        self.user.tier = self.basic_tier
        self.user.save()
        self.client.login(email='basic@test.com', password='testpass')

    def test_returns_403(self):
        response = self.client.get(self.unit_url)
        self.assertEqual(response.status_code, 403)

    def test_includes_teaser_body_marker(self):
        """The first ~150 words of the body should render."""
        response = self.client.get(self.unit_url)
        self.assertContains(response, 'TEASERINTROMARKER', status_code=403)

    def test_omits_late_body_content(self):
        """Content past the 150-word cutoff should NOT render."""
        response = self.client.get(self.unit_url)
        self.assertNotContains(
            response, 'FINAL_PARAGRAPH_MARKER', status_code=403,
        )

    def test_renders_teaser_body_container(self):
        response = self.client.get(self.unit_url)
        self.assertContains(response, 'data-testid="teaser-body"', status_code=403)

    def test_renders_video_thumbnail_block(self):
        response = self.client.get(self.unit_url)
        self.assertContains(
            response, 'data-testid="teaser-video-thumbnail"', status_code=403,
        )

    def test_video_thumbnail_url_for_youtube(self):
        response = self.client.get(self.unit_url)
        # img.youtube.com hqdefault is the stable thumbnail endpoint.
        self.assertContains(
            response, 'img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg',
            status_code=403,
        )

    def test_video_player_iframe_not_embedded(self):
        """Teaser must NOT auto-load the actual video player."""
        response = self.client.get(self.unit_url)
        # The unlocked layout includes ``video-player`` divs; the gated
        # path should not.
        self.assertNotContains(response, 'class="video-player', status_code=403)

    def test_renders_upgrade_cta(self):
        response = self.client.get(self.unit_url)
        self.assertContains(response, 'Upgrade to Main to access this lesson', status_code=403)
        # Issue #481: paywall pill reads "Main or above required".
        self.assertContains(response, 'Main or above required', status_code=403)
        self.assertNotContains(response, 'Main+ required', status_code=403)
        self.assertContains(response, 'Current access: Basic member', status_code=403)
        self.assertContains(
            response, 'data-testid="teaser-upgrade-cta"', status_code=403,
        )

    def test_does_not_render_signup_cta_for_authenticated(self):
        response = self.client.get(self.unit_url)
        self.assertNotContains(
            response, 'data-testid="teaser-signup-cta"', status_code=403,
        )

    def test_renders_homework_teaser(self):
        response = self.client.get(self.unit_url)
        self.assertContains(response, 'data-testid="teaser-homework"', status_code=403)
        # First sentence of the homework should appear.
        self.assertContains(response, 'Assemble a working pipeline.', status_code=403)
        # Second sentence ("Then write a brief report.") should NOT.
        self.assertNotContains(response, 'brief report', status_code=403)
        # And the upsell line.
        self.assertContains(
            response, 'Unlock to see the assignment', status_code=403,
        )

    def test_renders_breadcrumb(self):
        response = self.client.get(self.unit_url)
        self.assertContains(response, 'data-testid="teaser-breadcrumb"', status_code=403)
        # Module title should be in the breadcrumb.
        self.assertContains(response, 'Module One', status_code=403)

    def test_renders_title(self):
        response = self.client.get(self.unit_url)
        self.assertContains(
            response, 'data-testid="teaser-title"', status_code=403,
        )
        self.assertContains(response, 'Lesson One', status_code=403)

    def test_does_not_render_mark_complete_button(self):
        """Mark-complete button is replaced by the upgrade CTA."""
        response = self.client.get(self.unit_url)
        self.assertNotContains(
            response, 'id="mark-complete-btn"', status_code=403,
        )


# ------------------------------------------------------------
# Anonymous user
# ------------------------------------------------------------


class AnonymousUserTeaserTest(CourseUnitTeaserSetupMixin, TestCase):

    def test_returns_403(self):
        response = self.client.get(self.unit_url)
        self.assertEqual(response.status_code, 403)

    def test_includes_teaser_body(self):
        response = self.client.get(self.unit_url)
        self.assertContains(response, 'TEASERINTROMARKER', status_code=403)

    def test_renders_pricing_and_signup_ctas(self):
        response = self.client.get(self.unit_url)
        self.assertContains(
            response, 'data-testid="teaser-upgrade-cta"', status_code=403,
            count=1,
        )
        # Issue #481: paywall pill reads "Main or above required".
        self.assertContains(response, 'Main or above required', status_code=403)
        self.assertNotContains(response, 'Main+ required', status_code=403)
        self.assertContains(response, 'View Pricing', status_code=403)
        self.assertContains(
            response, 'data-testid="teaser-signup-cta"', status_code=403,
            count=1,
        )
        self.assertContains(
            response, 'Sign in or create a free account', status_code=403,
        )
        self.assertContains(response, '/accounts/signup/', status_code=403)


# ------------------------------------------------------------
# Eligible user (regression)
# ------------------------------------------------------------


class EligibleUserNoRegressionTest(CourseUnitTeaserSetupMixin, TestCase):
    """The teaser path must not affect users who can already access the unit."""

    def setUp(self):
        self.user = User.objects.create_user(email='paid@test.com', password='testpass')
        self.user.tier = self.main_tier
        self.user.save()
        self.client.login(email='paid@test.com', password='testpass')

    def test_returns_200(self):
        response = self.client.get(self.unit_url)
        self.assertEqual(response.status_code, 200)

    def test_renders_full_video_player(self):
        response = self.client.get(self.unit_url)
        self.assertContains(response, 'class="video-player')

    def test_renders_full_body(self):
        """All paragraphs render — including the FINAL_PARAGRAPH_MARKER
        that the teaser hides."""
        response = self.client.get(self.unit_url)
        self.assertContains(response, 'FINAL_PARAGRAPH_MARKER')

    def test_renders_mark_complete_button(self):
        response = self.client.get(self.unit_url)
        self.assertContains(response, 'id="mark-complete-btn"')

    def test_does_not_render_teaser_markers(self):
        response = self.client.get(self.unit_url)
        self.assertNotContains(response, 'data-testid="teaser-body"')
        self.assertNotContains(response, 'data-testid="teaser-upgrade-cta"')


# ------------------------------------------------------------
# Preview units (regression)
# ------------------------------------------------------------


class PreviewUnitNoRegressionTest(CourseUnitTeaserSetupMixin, TestCase):

    def test_anonymous_gets_200(self):
        response = self.client.get(self.preview_url)
        self.assertEqual(response.status_code, 200)

    def test_basic_user_gets_200(self):
        user = User.objects.create_user(email='basic2@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic2@test.com', password='testpass')
        response = self.client.get(self.preview_url)
        self.assertEqual(response.status_code, 200)

    def test_preview_renders_full_body_for_anonymous(self):
        response = self.client.get(self.preview_url)
        self.assertContains(response, 'Preview body that anyone can read.')

    def test_preview_does_not_render_teaser_markers(self):
        response = self.client.get(self.preview_url)
        self.assertNotContains(response, 'data-testid="teaser-body"')


# ------------------------------------------------------------
# Empty body fallback
# ------------------------------------------------------------


class EmptyBodyFallbackTest(CourseUnitTeaserSetupMixin, TestCase):
    """A unit with no body should fall back to the bare paywall card —
    we don't want an empty fade-out / dangling 'Unlock to see' card."""

    def setUp(self):
        user = User.objects.create_user(email='basic3@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic3@test.com', password='testpass')

    def test_returns_403(self):
        response = self.client.get(self.empty_url)
        self.assertEqual(response.status_code, 403)

    def test_no_teaser_body_container(self):
        response = self.client.get(self.empty_url)
        self.assertNotContains(
            response, 'data-testid="teaser-body"', status_code=403,
        )

    def test_no_teaser_video_block(self):
        response = self.client.get(self.empty_url)
        self.assertNotContains(
            response, 'data-testid="teaser-video-thumbnail"', status_code=403,
        )

    def test_still_shows_upgrade_cta(self):
        response = self.client.get(self.empty_url)
        self.assertContains(response, 'Upgrade to Main to access this lesson', status_code=403)
        self.assertContains(response, 'View Pricing', status_code=403)


# ------------------------------------------------------------
# Course outline syllabus DOM
# ------------------------------------------------------------


class CourseOutlineLockedRowsTest(CourseUnitTeaserSetupMixin, TestCase):
    """Locked unit rows in the course outline should be ``<a>`` links,
    not non-interactive ``<span>``s, so visitors can click through to
    the teaser preview."""

    def test_anonymous_locked_unit_is_anchor(self):
        response = self.client.get('/courses/teaser-course')
        self.assertEqual(response.status_code, 200)
        # Marker testid is only emitted for locked, non-preview rows.
        self.assertContains(response, 'data-testid="syllabus-locked-link"')
        # Rendered as an <a href=…> pointing at the unit.
        self.assertContains(
            response, 'href="/courses/teaser-course/module-one/lesson-one"',
        )

    def test_anonymous_locked_unit_has_lock_icon(self):
        response = self.client.get('/courses/teaser-course')
        self.assertContains(response, 'data-testid="syllabus-lock-icon"')

    def test_basic_user_sees_locked_rows_clickable(self):
        user = User.objects.create_user(email='basic4@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic4@test.com', password='testpass')
        response = self.client.get('/courses/teaser-course')
        self.assertContains(response, 'data-testid="syllabus-locked-link"')
        self.assertContains(
            response, 'href="/courses/teaser-course/module-one/lesson-one"',
        )

    def test_eligible_user_sees_no_lock_marker(self):
        """Lock testids are only for the gated path."""
        user = User.objects.create_user(email='paid2@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='paid2@test.com', password='testpass')
        response = self.client.get('/courses/teaser-course')
        self.assertNotContains(response, 'data-testid="syllabus-lock-icon"')
        self.assertNotContains(response, 'data-testid="syllabus-locked-link"')

    def test_preview_unit_row_is_anchor_for_anonymous(self):
        """Preview rows were already clickable; make sure they still are
        and that they don't pick up the locked-link testid."""
        response = self.client.get('/courses/teaser-course')
        self.assertContains(
            response, 'href="/courses/teaser-course/module-one/free-preview"',
        )
