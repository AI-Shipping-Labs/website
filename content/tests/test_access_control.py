"""Tests for access control and content gating (issue #71)."""

from datetime import date

from django.test import Client, TestCase, tag
from django.utils import timezone

from accounts.models import User
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
from content.models import Article, CuratedLink, Project, Tutorial
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
        from django.contrib.auth.models import AnonymousUser

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

        cls.free_user = User.objects.create_user(email='free@test.com')
        cls.free_user.tier = cls.free_tier
        cls.free_user.save()

        cls.basic_user = User.objects.create_user(email='basic@test.com')
        cls.basic_user.tier = cls.basic_tier
        cls.basic_user.save()

        cls.main_user = User.objects.create_user(email='main@test.com')
        cls.main_user.tier = cls.main_tier
        cls.main_user.save()

        cls.premium_user = User.objects.create_user(email='prem@test.com')
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
        response = self.client.get('/event-recordings/gated-recording')
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


