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
    """Test get_user_level for various user states."""

    def test_anonymous_user_returns_0(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(get_user_level(AnonymousUser()), 0)

    def test_none_user_returns_0(self):
        self.assertEqual(get_user_level(None), 0)

    def test_user_without_tier_returns_0(self):
        user = User.objects.create_user(email='notier@example.com')
        user.tier = None
        user.save()
        self.assertEqual(get_user_level(user), 0)

    def test_free_user_returns_0(self):
        user = User.objects.create_user(email='free@example.com')
        user.tier = self.free_tier
        user.save()
        self.assertEqual(get_user_level(user), 0)

    def test_basic_user_returns_10(self):
        user = User.objects.create_user(email='basic@example.com')
        user.tier = self.basic_tier
        user.save()
        self.assertEqual(get_user_level(user), 10)

    def test_main_user_returns_20(self):
        user = User.objects.create_user(email='main@example.com')
        user.tier = self.main_tier
        user.save()
        self.assertEqual(get_user_level(user), 20)

    def test_premium_user_returns_30(self):
        user = User.objects.create_user(email='premium@example.com')
        user.tier = self.premium_tier
        user.save()
        self.assertEqual(get_user_level(user), 30)

    def test_staff_user_with_free_tier_returns_30(self):
        user = User.objects.create_user(email='staff-free@example.com')
        user.tier = self.free_tier
        user.is_staff = True
        user.save()
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

    def test_staff_user_without_tier_returns_30(self):
        user = User.objects.create_user(email='staff-notier@example.com')
        user.tier = None
        user.is_staff = True
        user.save()
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

    def test_superuser_with_free_tier_returns_30(self):
        user = User.objects.create_user(email='super-free@example.com')
        user.tier = self.free_tier
        user.is_superuser = True
        user.save()
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

    def test_superuser_without_tier_returns_30(self):
        user = User.objects.create_user(email='super-notier@example.com')
        user.tier = None
        user.is_superuser = True
        user.save()
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

    def test_staff_superuser_returns_30(self):
        user = User.objects.create_user(email='staffsuper@example.com')
        user.tier = self.free_tier
        user.is_staff = True
        user.is_superuser = True
        user.save()
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

    def test_staff_with_basic_tier_returns_30(self):
        user = User.objects.create_user(email='staff-basic@example.com')
        user.tier = self.basic_tier
        user.is_staff = True
        user.save()
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)


@tag('core')
class CanAccessTest(TierSetupMixin, TestCase):
    """Test the can_access utility function."""

    def setUp(self):
        self.open_article = Article.objects.create(
            title='Open', slug='open', date=date(2025, 1, 1),
            required_level=LEVEL_OPEN,
        )
        self.basic_article = Article.objects.create(
            title='Basic', slug='basic', date=date(2025, 1, 1),
            required_level=LEVEL_BASIC,
        )
        self.main_article = Article.objects.create(
            title='Main', slug='main', date=date(2025, 1, 1),
            required_level=LEVEL_MAIN,
        )
        self.premium_article = Article.objects.create(
            title='Premium', slug='premium', date=date(2025, 1, 1),
            required_level=LEVEL_PREMIUM,
        )

    def test_anonymous_can_access_open(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertTrue(can_access(AnonymousUser(), self.open_article))

    def test_anonymous_cannot_access_basic(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(can_access(AnonymousUser(), self.basic_article))

    def test_free_user_can_access_open(self):
        user = User.objects.create_user(email='free@test.com')
        user.tier = self.free_tier
        user.save()
        self.assertTrue(can_access(user, self.open_article))

    def test_free_user_cannot_access_basic(self):
        user = User.objects.create_user(email='free2@test.com')
        user.tier = self.free_tier
        user.save()
        self.assertFalse(can_access(user, self.basic_article))

    def test_basic_user_can_access_basic(self):
        user = User.objects.create_user(email='basic@test.com')
        user.tier = self.basic_tier
        user.save()
        self.assertTrue(can_access(user, self.basic_article))

    def test_basic_user_cannot_access_main(self):
        user = User.objects.create_user(email='basic2@test.com')
        user.tier = self.basic_tier
        user.save()
        self.assertFalse(can_access(user, self.main_article))

    def test_main_user_can_access_main(self):
        user = User.objects.create_user(email='main@test.com')
        user.tier = self.main_tier
        user.save()
        self.assertTrue(can_access(user, self.main_article))

    def test_main_user_can_access_basic(self):
        user = User.objects.create_user(email='main2@test.com')
        user.tier = self.main_tier
        user.save()
        self.assertTrue(can_access(user, self.basic_article))

    def test_main_user_cannot_access_premium(self):
        user = User.objects.create_user(email='main3@test.com')
        user.tier = self.main_tier
        user.save()
        self.assertFalse(can_access(user, self.premium_article))

    def test_premium_user_can_access_premium(self):
        user = User.objects.create_user(email='prem@test.com')
        user.tier = self.premium_tier
        user.save()
        self.assertTrue(can_access(user, self.premium_article))

    def test_premium_user_can_access_all(self):
        user = User.objects.create_user(email='prem2@test.com')
        user.tier = self.premium_tier
        user.save()
        self.assertTrue(can_access(user, self.open_article))
        self.assertTrue(can_access(user, self.basic_article))
        self.assertTrue(can_access(user, self.main_article))
        self.assertTrue(can_access(user, self.premium_article))

    def test_staff_user_can_access_all(self):
        user = User.objects.create_user(email='staff-access@test.com')
        user.tier = self.free_tier
        user.is_staff = True
        user.save()
        self.assertTrue(can_access(user, self.open_article))
        self.assertTrue(can_access(user, self.basic_article))
        self.assertTrue(can_access(user, self.main_article))
        self.assertTrue(can_access(user, self.premium_article))

    def test_superuser_can_access_all(self):
        user = User.objects.create_user(email='super-access@test.com')
        user.tier = self.free_tier
        user.is_superuser = True
        user.save()
        self.assertTrue(can_access(user, self.open_article))
        self.assertTrue(can_access(user, self.basic_article))
        self.assertTrue(can_access(user, self.main_article))
        self.assertTrue(can_access(user, self.premium_article))

    def test_staff_without_tier_can_access_all(self):
        user = User.objects.create_user(email='staff-notier@test.com')
        user.tier = None
        user.is_staff = True
        user.save()
        self.assertTrue(can_access(user, self.open_article))
        self.assertTrue(can_access(user, self.basic_article))
        self.assertTrue(can_access(user, self.main_article))
        self.assertTrue(can_access(user, self.premium_article))


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


@tag('core')
class BlogDetailAccessControlTest(TierSetupMixin, TestCase):
    """Test blog detail view access control."""

    def setUp(self):
        self.client = Client()
        self.open_article = Article.objects.create(
            title='Open Article', slug='open-article',
            description='Open description',
            content_html='<p>Full open content</p>',
            date=date(2025, 6, 15), published=True,
            required_level=LEVEL_OPEN,
        )
        self.basic_article = Article.objects.create(
            title='Basic Article', slug='basic-article',
            description='Basic description',
            content_html='<p>Full basic content</p>',
            date=date(2025, 6, 15), published=True,
            required_level=LEVEL_BASIC,
        )
        self.main_article = Article.objects.create(
            title='Main Article', slug='main-article',
            description='Main description',
            content_html='<p>Full main content</p>',
            date=date(2025, 6, 15), published=True,
            required_level=LEVEL_MAIN,
        )

    def test_anonymous_sees_open_article_full_content(self):
        response = self.client.get('/blog/open-article')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Full open content')

    def test_anonymous_sees_gated_basic_article(self):
        response = self.client.get('/blog/basic-article')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Full basic content')
        self.assertContains(response, 'Upgrade to Basic to read this article')
        self.assertContains(response, '/pricing')

    def test_gated_article_never_returns_404(self):
        response = self.client.get('/blog/basic-article')
        self.assertEqual(response.status_code, 200)

    def test_anonymous_sees_teaser_for_basic_article(self):
        response = self.client.get('/blog/basic-article')
        self.assertContains(response, 'Basic description')

    def test_free_user_sees_gated_basic_article(self):
        user = User.objects.create_user(email='free@test.com', password='testpass')
        user.tier = self.free_tier
        user.save()
        self.client.login(email='free@test.com', password='testpass')
        response = self.client.get('/blog/basic-article')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Full basic content')
        self.assertContains(response, 'Upgrade to Basic')

    def test_basic_user_sees_basic_article_full_content(self):
        user = User.objects.create_user(email='basic@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/blog/basic-article')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Full basic content')

    def test_main_user_sees_basic_article_full_content(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/blog/basic-article')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Full basic content')

    def test_main_user_sees_main_article_full_content(self):
        user = User.objects.create_user(email='main2@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main2@test.com', password='testpass')
        response = self.client.get('/blog/main-article')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Full main content')

    def test_basic_user_cannot_see_main_article(self):
        user = User.objects.create_user(email='basic2@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic2@test.com', password='testpass')
        response = self.client.get('/blog/main-article')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Full main content')
        self.assertContains(response, 'Upgrade to Main to read this article')

    def test_staff_user_sees_all_gated_articles(self):
        user = User.objects.create_user(
            email='staff-blog@test.com', password='testpass',
        )
        user.tier = self.free_tier
        user.is_staff = True
        user.save()
        self.client.login(email='staff-blog@test.com', password='testpass')
        for slug, content_snippet in [
            ('open-article', 'Full open content'),
            ('basic-article', 'Full basic content'),
            ('main-article', 'Full main content'),
        ]:
            response = self.client.get(f'/blog/{slug}')
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, content_snippet)

    def test_superuser_sees_all_gated_articles(self):
        user = User.objects.create_user(
            email='super-blog@test.com', password='testpass',
        )
        user.tier = self.free_tier
        user.is_superuser = True
        user.save()
        self.client.login(email='super-blog@test.com', password='testpass')
        response = self.client.get('/blog/main-article')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Full main content')


@tag('core')
class RecordingDetailAccessControlTest(TierSetupMixin, TestCase):
    """Test recording detail view access control."""

    def setUp(self):
        self.client = Client()
        self.gated_recording = Event.objects.create(
            title='Gated Recording', slug='gated-recording',
            description='Recording description',
            recording_url='https://youtube.com/watch?v=test',
            start_datetime=timezone.make_aware(timezone.datetime(2025, 7, 20, 12, 0)), status='completed', published=True,
            required_level=LEVEL_MAIN,
        )

    def test_anonymous_sees_title_and_description(self):
        response = self.client.get('/event-recordings/gated-recording')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gated Recording')
        self.assertContains(response, 'Recording description')

    def test_anonymous_does_not_see_video(self):
        response = self.client.get('/event-recordings/gated-recording')
        self.assertNotContains(response, 'youtube.com/embed')
        self.assertContains(response, 'Upgrade to Main to watch this recording')

    def test_main_user_sees_video(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/event-recordings/gated-recording')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Upgrade to Main')

    def test_free_user_sees_gated_recording(self):
        user = User.objects.create_user(email='free@test.com', password='testpass')
        user.tier = self.free_tier
        user.save()
        self.client.login(email='free@test.com', password='testpass')
        response = self.client.get('/event-recordings/gated-recording')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Upgrade to Main')

    def test_staff_user_sees_gated_recording(self):
        user = User.objects.create_user(
            email='staff-rec@test.com', password='testpass',
        )
        user.tier = self.free_tier
        user.is_staff = True
        user.save()
        self.client.login(email='staff-rec@test.com', password='testpass')
        response = self.client.get('/event-recordings/gated-recording')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Upgrade to Main')


@tag('core')
class ProjectDetailAccessControlTest(TierSetupMixin, TestCase):
    """Test project detail view access control."""

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
    """Test tutorial detail view access control."""

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

    def test_premium_user_sees_full_tutorial(self):
        user = User.objects.create_user(email='prem@test.com', password='testpass')
        user.tier = self.premium_tier
        user.save()
        self.client.login(email='prem@test.com', password='testpass')
        response = self.client.get('/tutorials/gated-tutorial')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Secret tutorial content')

    def test_main_user_cannot_see_premium_tutorial(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/tutorials/gated-tutorial')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Secret tutorial content')
        self.assertContains(response, 'Upgrade to Premium')

    def test_staff_user_sees_premium_tutorial(self):
        user = User.objects.create_user(
            email='staff-tut@test.com', password='testpass',
        )
        user.tier = self.free_tier
        user.is_staff = True
        user.save()
        self.client.login(email='staff-tut@test.com', password='testpass')
        response = self.client.get('/tutorials/gated-tutorial')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Secret tutorial content')


# --- Lock icon in listing pages ---


class BlogListLockIconTest(TierSetupMixin, TestCase):
    """Test lock icon display on blog listing page."""

    def setUp(self):
        self.client = Client()
        self.open_article = Article.objects.create(
            title='Open Post', slug='open-post',
            description='Open', date=date(2025, 6, 15),
            published=True, required_level=LEVEL_OPEN,
        )
        self.gated_article = Article.objects.create(
            title='Gated Post', slug='gated-post',
            description='Gated', date=date(2025, 6, 15),
            published=True, required_level=LEVEL_BASIC,
        )

    def test_lock_icon_on_gated_article(self):
        response = self.client.get('/blog')
        content = response.content.decode()
        # The lock icon should appear near the gated article
        self.assertIn('data-lucide="lock"', content)

    def test_open_article_no_lock(self):
        # Delete the gated article so only open remains
        self.gated_article.delete()
        response = self.client.get('/blog')
        content = response.content.decode()
        self.assertNotIn('data-lucide="lock"', content)


class RecordingsListLockIconTest(TierSetupMixin, TestCase):
    """Test lock icon on recordings listing page."""

    def setUp(self):
        self.client = Client()
        self.gated_recording = Event.objects.create(
            title='Gated Rec', slug='gated-rec',
            description='Gated', start_datetime=timezone.make_aware(timezone.datetime(2025, 7, 20, 12, 0)), status='completed',
            recording_url='https://youtube.com/watch?v=test',
            published=True, required_level=LEVEL_MAIN,
        )

    def test_lock_icon_on_gated_recording(self):
        response = self.client.get('/event-recordings')
        self.assertContains(response, 'data-lucide="lock"')


class ProjectsListLockIconTest(TierSetupMixin, TestCase):
    """Test lock icon on projects listing page."""

    def setUp(self):
        self.client = Client()
        self.gated_project = Project.objects.create(
            title='Gated Proj', slug='gated-proj',
            description='Gated', date=date(2025, 8, 10),
            published=True, required_level=LEVEL_BASIC,
        )

    def test_lock_icon_on_gated_project(self):
        response = self.client.get('/projects')
        self.assertContains(response, 'data-lucide="lock"')


class TutorialsListLockIconTest(TierSetupMixin, TestCase):
    """Test lock icon on tutorials listing page."""

    def setUp(self):
        self.client = Client()
        self.gated_tutorial = Tutorial.objects.create(
            title='Gated Tut', slug='gated-tut',
            description='Gated', date=date(2025, 9, 1),
            published=True, required_level=LEVEL_PREMIUM,
        )

    def test_lock_icon_on_gated_tutorial(self):
        response = self.client.get('/tutorials')
        self.assertContains(response, 'data-lucide="lock"')


class CollectionListLockIconTest(TierSetupMixin, TestCase):
    """Test lock icon on collection listing page."""

    def setUp(self):
        self.client = Client()
        self.gated_link = CuratedLink.objects.create(
            item_id='gated-link', title='Gated Link',
            description='Gated', url='https://example.com',
            category='tools', published=True,
            required_level=LEVEL_BASIC,
        )

    def test_lock_icon_on_gated_link(self):
        response = self.client.get('/collection')
        self.assertContains(response, 'data-lucide="lock"')


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


