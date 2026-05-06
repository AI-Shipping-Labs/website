"""Tests for access control and content gating (issue #71)."""

from datetime import date, timedelta

from django.contrib.auth.models import AnonymousUser
from django.test import Client, TestCase, tag
from django.utils import timezone

from accounts.models import TierOverride, User
from accounts.signals import mark_email_verified_on_social_login
from content.access import (
    LEVEL_BASIC,
    LEVEL_MAIN,
    LEVEL_OPEN,
    LEVEL_PREMIUM,
    build_gating_context,
    can_access,
    get_required_tier_name,
    get_teaser_text,
    get_user_level,
)
from content.models import (
    Article,
    Course,
    CourseAccess,
    CuratedLink,
    Download,
    Module,
    Project,
    Tutorial,
    Unit,
)
from events.models import Event
from tests.fixtures import TierSetupMixin

# --- Unit Tests for access.py utilities ---


@tag('core')
class GetUserLevelTest(TierSetupMixin, TestCase):
    """Test get_user_level for various user states.

    Consolidated in #261: previously 13 separate tests for each
    (tier x staff/superuser) combination -> 2 parameterized tests.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.anon = AnonymousUser()
        cls.no_tier_user = User.objects.create_user(email='notier@example.com')
        cls.no_tier_user.tier = None
        cls.no_tier_user.save()

        cls.free_user = User.objects.create_user(email='free@example.com')
        cls.free_user.tier = cls.free_tier
        cls.free_user.save()

        cls.basic_user = User.objects.create_user(email='basic@example.com')
        cls.basic_user.tier = cls.basic_tier
        cls.basic_user.save()

        cls.main_user = User.objects.create_user(email='main@example.com')
        cls.main_user.tier = cls.main_tier
        cls.main_user.save()

        cls.premium_user = User.objects.create_user(email='premium@example.com')
        cls.premium_user.tier = cls.premium_tier
        cls.premium_user.save()

    def test_returns_correct_level_per_tier(self):
        cases = [
            ('anonymous', self.anon, 0),
            ('none', None, 0),
            ('user without tier', self.no_tier_user, 0),
            ('free', self.free_user, 0),
            ('basic', self.basic_user, 10),
            ('main', self.main_user, 20),
            ('premium', self.premium_user, 30),
        ]
        for label, user, expected in cases:
            with self.subTest(user=label):
                self.assertEqual(get_user_level(user), expected)

    def test_staff_and_superuser_always_return_premium(self):
        cases = [
            ('staff with free tier', dict(email='s1@example.com', tier=self.free_tier, is_staff=True)),
            ('staff with basic tier', dict(email='s2@example.com', tier=self.basic_tier, is_staff=True)),
            ('staff without tier', dict(email='s3@example.com', tier=None, is_staff=True)),
            ('superuser with free tier', dict(email='s4@example.com', tier=self.free_tier, is_superuser=True)),
            ('superuser without tier', dict(email='s5@example.com', tier=None, is_superuser=True)),
            ('staff and superuser', dict(email='s6@example.com', tier=self.free_tier, is_staff=True, is_superuser=True)),
        ]
        for label, attrs in cases:
            with self.subTest(user=label):
                user = User.objects.create_user(email=attrs['email'])
                user.tier = attrs.get('tier')
                user.is_staff = attrs.get('is_staff', False)
                user.is_superuser = attrs.get('is_superuser', False)
                user.save()
                self.assertEqual(get_user_level(user), LEVEL_PREMIUM)


@tag('core')
class CanAccessTest(TierSetupMixin, TestCase):
    """Test the can_access utility function.

    Consolidated in #261: previously 14 separate tests for each
    (user, content level) combination -> 2 parameterized tests
    (matrix + privileged-users-bypass-all).
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.contrib.auth.models import AnonymousUser

        cls.anon = AnonymousUser()

        cls.free_user = User.objects.create_user(email='free@test.com', email_verified=True)
        cls.free_user.tier = cls.free_tier
        cls.free_user.save()

        cls.basic_user = User.objects.create_user(email='basic@test.com', email_verified=True)
        cls.basic_user.tier = cls.basic_tier
        cls.basic_user.save()

        cls.main_user = User.objects.create_user(email='main@test.com', email_verified=True)
        cls.main_user.tier = cls.main_tier
        cls.main_user.save()

        cls.premium_user = User.objects.create_user(email='prem@test.com', email_verified=True)
        cls.premium_user.tier = cls.premium_tier
        cls.premium_user.save()

        cls.open_article = Article.objects.create(
            title='Open', slug='open', date=date(2025, 1, 1),
            required_level=LEVEL_OPEN,
        )
        cls.basic_article = Article.objects.create(
            title='Basic', slug='basic', date=date(2025, 1, 1),
            required_level=LEVEL_BASIC,
        )
        cls.main_article = Article.objects.create(
            title='Main', slug='main', date=date(2025, 1, 1),
            required_level=LEVEL_MAIN,
        )
        cls.premium_article = Article.objects.create(
            title='Premium', slug='premium', date=date(2025, 1, 1),
            required_level=LEVEL_PREMIUM,
        )

    def test_tier_access_matrix(self):
        """Each user can access content at or below their level only."""
        cases = [
            # (label, user, content, expected)
            ('anon vs open',    self.anon,        self.open_article,    True),
            ('anon vs basic',   self.anon,        self.basic_article,   False),
            ('free vs open',    self.free_user,   self.open_article,    True),
            ('free vs basic',   self.free_user,   self.basic_article,   False),
            ('basic vs basic',  self.basic_user,  self.basic_article,   True),
            ('basic vs main',   self.basic_user,  self.main_article,    False),
            ('main vs basic',   self.main_user,   self.basic_article,   True),
            ('main vs main',    self.main_user,   self.main_article,    True),
            ('main vs premium', self.main_user,   self.premium_article, False),
            ('premium vs open', self.premium_user, self.open_article,    True),
            ('premium vs basic',self.premium_user, self.basic_article,   True),
            ('premium vs main', self.premium_user, self.main_article,    True),
            ('premium vs premium', self.premium_user, self.premium_article, True),
        ]
        for label, user, article, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(can_access(user, article), expected)

    def test_staff_and_superuser_bypass_all_gates(self):
        """Staff / superusers (with or without a tier) can access everything."""
        privileged = []

        staff_with_tier = User.objects.create_user(email='staff-access@test.com')
        staff_with_tier.tier = self.free_tier
        staff_with_tier.is_staff = True
        staff_with_tier.save()
        privileged.append(('staff with free tier', staff_with_tier))

        superuser_with_tier = User.objects.create_user(email='super-access@test.com')
        superuser_with_tier.tier = self.free_tier
        superuser_with_tier.is_superuser = True
        superuser_with_tier.save()
        privileged.append(('superuser with free tier', superuser_with_tier))

        staff_no_tier = User.objects.create_user(email='staff-notier@test.com')
        staff_no_tier.tier = None
        staff_no_tier.is_staff = True
        staff_no_tier.save()
        privileged.append(('staff without tier', staff_no_tier))

        for label, user in privileged:
            for article in [self.open_article, self.basic_article, self.main_article, self.premium_article]:
                with self.subTest(user=label, article=article.slug):
                    self.assertTrue(can_access(user, article))


@tag('core')
class CanAccessEmailVerifiedTest(TierSetupMixin, TestCase):
    """Email verification gates only authenticated free users on free content."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.anon = AnonymousUser()
        cls.open_article = Article.objects.create(
            title='Open Email Gate', slug='open-email-gate',
            date=date(2025, 1, 1), required_level=LEVEL_OPEN,
        )
        cls.basic_article = Article.objects.create(
            title='Basic Email Gate', slug='basic-email-gate',
            date=date(2025, 1, 1), required_level=LEVEL_BASIC,
        )

    def _user(self, label, tier, verified, **kwargs):
        user = User.objects.create_user(
            email=f'{label}-{verified}@example.com',
            email_verified=verified,
            **kwargs,
        )
        user.tier = tier
        user.save()
        return user

    def test_email_verified_access_matrix(self):
        free_unverified = self._user('free', self.free_tier, False)
        free_verified = self._user('free', self.free_tier, True)
        basic_unverified = self._user('basic', self.basic_tier, False)
        basic_verified = self._user('basic', self.basic_tier, True)
        main_unverified = self._user('main', self.main_tier, False)
        main_verified = self._user('main', self.main_tier, True)
        premium_unverified = self._user('premium', self.premium_tier, False)
        premium_verified = self._user('premium', self.premium_tier, True)
        staff_unverified = self._user('staff', self.free_tier, False, is_staff=True)
        staff_verified = self._user('staff', self.free_tier, True, is_staff=True)

        override_user = self._user('override', self.free_tier, False)
        TierOverride.objects.create(
            user=override_user,
            original_tier=self.free_tier,
            override_tier=self.basic_tier,
            expires_at=timezone.now() + timedelta(days=7),
            is_active=True,
        )

        cases = [
            ('anonymous', self.anon, True, False),
            ('free unverified', free_unverified, False, False),
            ('free verified', free_verified, True, False),
            ('basic unverified', basic_unverified, True, True),
            ('basic verified', basic_verified, True, True),
            ('main unverified', main_unverified, True, True),
            ('main verified', main_verified, True, True),
            ('premium unverified', premium_unverified, True, True),
            ('premium verified', premium_verified, True, True),
            ('staff unverified', staff_unverified, True, True),
            ('staff verified', staff_verified, True, True),
            ('free override unverified', override_user, True, True),
        ]
        for label, user, expected_open, expected_basic in cases:
            with self.subTest(user=label, level=LEVEL_OPEN):
                self.assertEqual(can_access(user, self.open_article), expected_open)
            with self.subTest(user=label, level=LEVEL_BASIC):
                self.assertEqual(can_access(user, self.basic_article), expected_basic)

    def test_build_gating_context_unverified_free(self):
        user = self._user('ctx-free', self.free_tier, False)
        ctx = build_gating_context(user, self.open_article, 'article')
        self.assertTrue(ctx['is_gated'])
        self.assertEqual(ctx['gated_reason'], 'unverified_email')
        self.assertEqual(ctx['verify_email_address'], user.email)
        self.assertEqual(ctx['verify_resend_url'], '/account/api/resend-verification')

    def test_build_gating_context_insufficient_tier(self):
        user = self._user('ctx-tier', self.free_tier, True)
        ctx = build_gating_context(user, self.basic_article, 'article')
        self.assertTrue(ctx['is_gated'])
        self.assertEqual(ctx['gated_reason'], 'insufficient_tier')
        self.assertEqual(ctx['cta_message'], 'Upgrade to Basic to read this article')

    def test_course_access_row_bypasses_email_check(self):
        user = self._user('course-access', self.free_tier, False)
        course = Course.objects.create(
            title='Granted Course', slug='granted-course',
            required_level=LEVEL_BASIC, status='published',
        )
        CourseAccess.objects.create(user=user, course=course, access_type='granted')
        self.assertTrue(can_access(user, course))

    def test_oauth_signal_marks_email_verified_and_unblocks_free_content(self):
        user = self._user('oauth', self.free_tier, False)

        class SocialLogin:
            pass

        sociallogin = SocialLogin()
        sociallogin.user = user
        mark_email_verified_on_social_login(sender=None, request=None, sociallogin=sociallogin)

        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertTrue(can_access(user, self.open_article))


@tag('core')
class GetRequiredTierNameTest(TestCase):
    """Test tier name mapping."""

    def test_open(self):
        self.assertEqual(get_required_tier_name(0), 'Free')

    def test_basic(self):
        self.assertEqual(get_required_tier_name(10), 'Basic')

    def test_main(self):
        self.assertEqual(get_required_tier_name(20), 'Main')

    def test_premium(self):
        self.assertEqual(get_required_tier_name(30), 'Premium')

    def test_unknown_defaults_to_premium(self):
        self.assertEqual(get_required_tier_name(99), 'Premium')


class GetTeaserTextTest(TestCase):
    """Test teaser text extraction."""

    def test_uses_description(self):
        article = Article(description='Short description', content_markdown='Long content')
        self.assertEqual(get_teaser_text(article), 'Short description')

    def test_falls_back_to_markdown(self):
        article = Article(description='', content_markdown='Markdown content here')
        self.assertEqual(get_teaser_text(article), 'Markdown content here')

    def test_truncates_at_max_chars(self):
        article = Article(description='x' * 300)
        teaser = get_teaser_text(article, max_chars=200)
        self.assertEqual(len(teaser), 200)

    def test_empty_content(self):
        article = Article(description='', content_markdown='')
        self.assertEqual(get_teaser_text(article), '')


@tag('core')
class BuildGatingContextTest(TierSetupMixin, TestCase):
    """Test build_gating_context."""

    def setUp(self):
        self.article = Article.objects.create(
            title='Gated Article', slug='gated', date=date(2025, 1, 1),
            description='This is the description',
            required_level=LEVEL_BASIC,
        )

    def test_not_gated_for_matching_user(self):
        user = User.objects.create_user(email='basic@test.com')
        user.tier = self.basic_tier
        user.save()
        ctx = build_gating_context(user, self.article, 'article')
        self.assertFalse(ctx['is_gated'])

    def test_gated_for_anonymous(self):
        from django.contrib.auth.models import AnonymousUser
        ctx = build_gating_context(AnonymousUser(), self.article, 'article')
        self.assertTrue(ctx['is_gated'])
        self.assertEqual(ctx['cta_message'], 'Upgrade to Basic to read this article')
        self.assertEqual(ctx['required_tier_name'], 'Basic')
        self.assertEqual(ctx['pricing_url'], '/pricing')
        self.assertIn('This is the description', ctx['teaser'])

    def test_gated_for_free_user(self):
        user = User.objects.create_user(email='free@test.com')
        user.tier = self.free_tier
        user.save()
        ctx = build_gating_context(user, self.article, 'article')
        self.assertTrue(ctx['is_gated'])

    def test_not_gated_for_staff_user(self):
        user = User.objects.create_user(email='staff@test.com')
        user.tier = self.free_tier
        user.is_staff = True
        user.save()
        ctx = build_gating_context(user, self.article, 'article')
        self.assertFalse(ctx['is_gated'])

    def test_not_gated_for_superuser(self):
        user = User.objects.create_user(email='super@test.com')
        user.tier = self.free_tier
        user.is_superuser = True
        user.save()
        ctx = build_gating_context(user, self.article, 'article')
        self.assertFalse(ctx['is_gated'])

    def test_recording_cta_message(self):
        recording = Event.objects.create(
            title='Gated Recording', slug='gated-rec', start_datetime=timezone.make_aware(timezone.datetime(2025, 1, 1, 12, 0)), status='completed',
            description='Recording desc', required_level=LEVEL_MAIN,
        )
        from django.contrib.auth.models import AnonymousUser
        ctx = build_gating_context(AnonymousUser(), recording, 'recording')
        self.assertEqual(ctx['cta_message'], 'Upgrade to Main to watch this recording')


# --- Model field tests ---


@tag('core')
class RequiredLevelFieldTest(TestCase):
    """Test that required_level field exists and defaults to 0 on all models."""

    def test_article_default_level(self):
        article = Article.objects.create(
            title='Test', slug='test-rl', date=date(2025, 1, 1),
        )
        self.assertEqual(article.required_level, 0)

    def test_recording_default_level(self):
        recording = Event.objects.create(
            title='Test', slug='test-rl', start_datetime=timezone.make_aware(timezone.datetime(2025, 1, 1, 12, 0)), status='completed',
        )
        self.assertEqual(recording.required_level, 0)

    def test_project_default_level(self):
        project = Project.objects.create(
            title='Test', slug='test-rl', date=date(2025, 1, 1),
        )
        self.assertEqual(project.required_level, 0)

    def test_tutorial_default_level(self):
        tutorial = Tutorial.objects.create(
            title='Test', slug='test-rl', date=date(2025, 1, 1),
        )
        self.assertEqual(tutorial.required_level, 0)

    def test_curated_link_default_level(self):
        link = CuratedLink.objects.create(
            item_id='test-rl', title='Test',
            url='https://example.com', category='tools',
        )
        self.assertEqual(link.required_level, 0)

    def test_article_custom_level(self):
        article = Article.objects.create(
            title='Premium', slug='prem', date=date(2025, 1, 1),
            required_level=LEVEL_PREMIUM,
        )
        self.assertEqual(article.required_level, 30)


# --- View integration tests ---


# Per-content-type detail view tier matrix tests removed in #261:
# the full Article/Recording/Project/Tutorial gating matrix is exercised
# end-to-end by playwright_tests/test_access_control.py
# (TestScenario1-7 cover open/gated content for each content type and tier).
# Function-level access logic stays covered by CanAccessTest /
# BuildGatingContextTest above. One smoke test per detail view remains
# below to catch URL/template breakage that wouldn't bubble up via the
# Playwright suite during unit-test runs.


@tag('core')
class BlogDetailAccessControlTest(TierSetupMixin, TestCase):
    """Smoke test: blog detail view renders gated CTA when user lacks access.

    Per-tier matrix coverage lives in
    playwright_tests/test_access_control.py.
    """

    def setUp(self):
        self.client = Client()
        self.basic_article = Article.objects.create(
            title='Basic Article', slug='basic-article',
            description='Basic description',
            content_html='<p>Full basic content</p>',
            date=date(2025, 6, 15), published=True,
            required_level=LEVEL_BASIC,
        )

    def test_anonymous_sees_gated_basic_article(self):
        response = self.client.get('/blog/basic-article')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Full basic content')
        self.assertContains(response, 'Upgrade to Basic to read this article')


@tag('core')
class RecordingDetailAccessControlTest(TierSetupMixin, TestCase):
    """Smoke test: recording detail view renders gated CTA when user lacks access.

    Per-tier matrix and "video URL never leaks" coverage lives in
    playwright_tests/test_access_control.py::TestScenario7BasicMemberBlockedFromMainRecording.
    """

    def setUp(self):
        self.client = Client()
        self.gated_recording = Event.objects.create(
            title='Gated Recording', slug='gated-recording',
            description='Recording description',
            recording_url='https://youtube.com/watch?v=test',
            start_datetime=timezone.make_aware(timezone.datetime(2025, 7, 20, 12, 0)), status='completed', published=True,
            required_level=LEVEL_MAIN,
        )

    def test_anonymous_does_not_see_video(self):
        response = self.client.get('/events/gated-recording')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'youtube.com/embed')
        self.assertContains(response, 'Upgrade to Main to watch this recording')


@tag('core')
class ProjectDetailAccessControlTest(TierSetupMixin, TestCase):
    """Smoke test: project detail view renders gated CTA when user lacks access.

    Per-tier matrix coverage lives in
    playwright_tests/test_access_control.py.
    """

    def setUp(self):
        self.client = Client()
        self.gated_project = Project.objects.create(
            title='Gated Project', slug='gated-project',
            description='Project description',
            content_html='<p>Secret project content</p>',
            date=date(2025, 8, 10), published=True,
            required_level=LEVEL_BASIC,
        )

    def test_anonymous_sees_gated_project(self):
        response = self.client.get('/projects/gated-project')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Secret project content')
        self.assertContains(response, 'Upgrade to Basic to view this project')

    def test_basic_user_sees_full_project(self):
        # Replaces playwright_tests/test_project_showcase.py::TestScenario7BasicMemberUnlocksBasicProject::test_basic_member_sees_full_project_content
        # Kept on cross-layer dedup pass (#261) because #260 deleted the
        # Playwright test_project_showcase.py suite — this Django test is
        # now the sole authoritative coverage for basic-member project access.
        user = User.objects.create_user(email='basic@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/projects/gated-project')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Secret project content')
        # No upgrade CTA / blur overlay leaks through for an authorised viewer.
        self.assertNotContains(response, 'Upgrade to Basic to view this project')
        self.assertNotContains(response, 'filter: blur(8px)')

    def test_staff_user_sees_gated_project(self):
        # Kept on cross-layer dedup pass (#261): #260 deleted
        # playwright_tests/test_project_showcase.py and there is no
        # project-specific staff-bypass scenario in
        # playwright_tests/test_access_control.py, so this Django test
        # is the only line of defence for staff-bypass on projects.
        user = User.objects.create_user(
            email='staff-proj@test.com', password='testpass',
        )
        user.tier = self.free_tier
        user.is_staff = True
        user.save()
        self.client.login(email='staff-proj@test.com', password='testpass')
        response = self.client.get('/projects/gated-project')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Secret project content')


@tag('core')
class TutorialDetailAccessControlTest(TierSetupMixin, TestCase):
    """Smoke test: tutorial detail view renders gated CTA when user lacks access.

    Per-tier matrix coverage lives in
    playwright_tests/test_access_control.py.
    """

    def setUp(self):
        self.client = Client()
        self.gated_tutorial = Tutorial.objects.create(
            title='Gated Tutorial', slug='gated-tutorial',
            description='Tutorial description',
            content_html='<p>Secret tutorial content</p>',
            date=date(2025, 9, 1), published=True,
            required_level=LEVEL_PREMIUM,
        )

    def test_anonymous_sees_gated_tutorial(self):
        response = self.client.get('/tutorials/gated-tutorial')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Secret tutorial content')
        self.assertContains(response, 'Upgrade to Premium to read this tutorial')


@tag('core')
class FreeUnverifiedDetailGateTest(TierSetupMixin, TestCase):
    """Each content detail surface renders the verify-email gate for free users."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email='unverified-detail@example.com',
            password='testpass',
            email_verified=False,
        )
        self.user.tier = self.free_tier
        self.user.save()
        self.client.login(email='unverified-detail@example.com', password='testpass')

    def assert_verify_gate(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="verify-email-required-card"')
        self.assertContains(response, 'unverified-detail@example.com')
        self.assertContains(response, 'Resend verification email')
        self.assertNotContains(response, 'data-testid="gated-access-card"')

    def test_blog_detail_renders_verify_gate(self):
        Article.objects.create(
            title='Free Blog Gate', slug='free-blog-gate',
            content_html='<p>Free blog body</p>',
            date=date(2025, 1, 1), published=True, required_level=LEVEL_OPEN,
        )
        response = self.client.get('/blog/free-blog-gate')
        self.assert_verify_gate(response)
        self.assertNotContains(response, 'Free blog body')

    def test_tutorial_detail_renders_verify_gate(self):
        Tutorial.objects.create(
            title='Free Tutorial Gate', slug='free-tutorial-gate',
            content_html='<p>Free tutorial body</p>',
            date=date(2025, 1, 1), published=True, required_level=LEVEL_OPEN,
        )
        response = self.client.get('/tutorials/free-tutorial-gate')
        self.assert_verify_gate(response)
        self.assertNotContains(response, 'Free tutorial body')

    def test_project_detail_renders_verify_gate(self):
        Project.objects.create(
            title='Free Project Gate', slug='free-project-gate',
            content_html='<p>Free project body</p>',
            date=date(2025, 1, 1), published=True, required_level=LEVEL_OPEN,
        )
        response = self.client.get('/projects/free-project-gate')
        self.assert_verify_gate(response)
        self.assertNotContains(response, 'Free project body')

    def test_recording_detail_renders_verify_gate(self):
        Event.objects.create(
            title='Free Recording Gate', slug='free-recording-gate',
            description='Recording description',
            recording_url='https://youtube.com/watch?v=free',
            start_datetime=timezone.make_aware(timezone.datetime(2025, 7, 20, 12, 0)),
            status='completed', published=True, required_level=LEVEL_OPEN,
        )
        response = self.client.get('/events/free-recording-gate')
        self.assert_verify_gate(response)
        self.assertNotContains(response, 'youtube.com/embed')

    def test_event_detail_renders_verify_gate(self):
        Event.objects.create(
            title='Free Event Gate', slug='free-event-gate',
            description='Event description',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming', published=True, required_level=LEVEL_OPEN,
        )
        response = self.client.get('/events/free-event-gate')
        self.assert_verify_gate(response)

    def test_course_detail_renders_verify_gate(self):
        Course.objects.create(
            title='Free Course Gate', slug='free-course-gate',
            description='Free course description',
            status='published', required_level=LEVEL_OPEN,
        )
        response = self.client.get('/courses/free-course-gate')
        self.assert_verify_gate(response)

    def test_course_unit_detail_renders_verify_gate(self):
        course = Course.objects.create(
            title='Free Unit Course Gate', slug='free-unit-course-gate',
            status='published', required_level=LEVEL_OPEN,
        )
        module = Module.objects.create(course=course, title='Module 1', slug='module-1')
        Unit.objects.create(
            module=module, title='Unit 1', slug='unit-1',
            body='<p>Free unit body</p>', is_preview=False,
        )
        response = self.client.get('/courses/free-unit-course-gate/module-1/unit-1')
        self.assert_verify_gate(response)
        self.assertNotContains(response, 'data-testid="teaser-cta"')

    def test_curated_link_click_through_renders_verify_gate(self):
        link = CuratedLink.objects.create(
            item_id='free-link-gate',
            title='Free Link Gate',
            description='Free link description',
            url='https://example.com/free-link',
            category='tools',
            required_level=LEVEL_OPEN,
        )
        response = self.client.get(f'/resources/{link.pk}/go')
        self.assert_verify_gate(response)

    def test_download_file_renders_verify_gate(self):
        Download.objects.create(
            title='Free Download Gate',
            slug='free-download-gate',
            description='Free download description',
            file_url='https://example.com/free.pdf',
            required_level=LEVEL_OPEN,
            published=True,
        )
        response = self.client.get('/api/downloads/free-download-gate/file')
        self.assert_verify_gate(response)

    def test_free_listings_still_render_normally(self):
        Article.objects.create(
            title='Listed Free Blog', slug='listed-free-blog',
            date=date(2025, 1, 1), published=True, required_level=LEVEL_OPEN,
        )
        Tutorial.objects.create(
            title='Listed Free Tutorial', slug='listed-free-tutorial',
            date=date(2025, 1, 1), published=True, required_level=LEVEL_OPEN,
        )
        Project.objects.create(
            title='Listed Free Project', slug='listed-free-project',
            date=date(2025, 1, 1), published=True, required_level=LEVEL_OPEN,
        )
        Event.objects.create(
            title='Listed Free Recording', slug='listed-free-recording',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed', published=True, required_level=LEVEL_OPEN,
            recording_url='https://youtube.com/watch?v=listed',
        )
        Event.objects.create(
            title='Listed Free Event', slug='listed-free-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming', published=True, required_level=LEVEL_OPEN,
        )
        Course.objects.create(
            title='Listed Free Course', slug='listed-free-course',
            status='published', required_level=LEVEL_OPEN,
        )
        CuratedLink.objects.create(
            item_id='listed-free-link',
            title='Listed Free Link',
            url='https://example.com/listed-link',
            category='tools',
            required_level=LEVEL_OPEN,
        )
        Download.objects.create(
            title='Listed Free Download',
            slug='listed-free-download',
            file_url='https://example.com/listed.pdf',
            required_level=LEVEL_OPEN,
            published=True,
        )
        cases = [
            ('/blog', 'Listed Free Blog'),
            ('/tutorials', 'Listed Free Tutorial'),
            ('/projects', 'Listed Free Project'),
            ('/events?filter=past', 'Listed Free Recording'),
            ('/events', 'Listed Free Event'),
            ('/courses', 'Listed Free Course'),
            ('/resources', 'Listed Free Link'),
            ('/downloads', 'Listed Free Download'),
        ]
        for path, text in cases:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, text)


# --- Lock icon in listing pages ---
# Lock icon presence on listing pages is covered end-to-end by:
#   playwright_tests/test_articles_blog.py  (blog listing lock icon)
#   playwright_tests/test_event_recordings.py  (recordings listing lock icon)
#   playwright_tests/test_project_showcase.py  (projects listing lock icon)
#   playwright_tests/test_curated_links.py  (collection listing lock icon)
# The Django string-match versions are removed in #261 because they
# only assert on a CSS-attribute literal (`data-lucide="lock"`) and pass
# even when the icon never renders for the user (Rule 4).
# /tutorials lock-icon coverage has no Playwright equivalent today; the
# behavior is acceptable to drop because tutorial gating is exercised
# by `TutorialDetailAccessControlTest` above.


# --- Template tags tests ---


@tag('core')
class AccessTemplateTagsTest(TierSetupMixin, TestCase):
    """Test the access_tags template tags."""

    def setUp(self):
        self.open_article = Article.objects.create(
            title='Open', slug='open-tt', date=date(2025, 1, 1),
            required_level=LEVEL_OPEN,
        )
        self.basic_article = Article.objects.create(
            title='Basic', slug='basic-tt', date=date(2025, 1, 1),
            required_level=LEVEL_BASIC,
        )

    def test_can_access_content_tag_with_matching_user(self):
        from content.templatetags.access_tags import can_access_content
        user = User.objects.create_user(email='basic@test.com')
        user.tier = self.basic_tier
        user.save()
        self.assertTrue(can_access_content(user, self.basic_article))

    def test_can_access_content_tag_with_anonymous(self):
        from django.contrib.auth.models import AnonymousUser

        from content.templatetags.access_tags import can_access_content
        self.assertFalse(can_access_content(AnonymousUser(), self.basic_article))

    def test_is_gated_tag_for_open_content(self):
        from content.templatetags.access_tags import is_gated
        self.assertFalse(is_gated(self.open_article))

    def test_is_gated_tag_for_gated_content(self):
        from content.templatetags.access_tags import is_gated
        self.assertTrue(is_gated(self.basic_article))

    def test_required_tier_name_filter(self):
        from content.templatetags.access_tags import required_tier_name
        self.assertEqual(required_tier_name(0), 'Free')
        self.assertEqual(required_tier_name(10), 'Basic')
        self.assertEqual(required_tier_name(20), 'Main')
        self.assertEqual(required_tier_name(30), 'Premium')
