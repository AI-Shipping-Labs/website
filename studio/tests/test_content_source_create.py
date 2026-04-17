"""Tests for the Studio "Add content source" form (issues #199, #213).

The form auto-detects the content type(s) and path(s) by inspecting the repo
on GitHub, so the tests mock both ``list_installation_repositories`` (the repo
dropdown) and ``detect_content_sources`` (the auto-detect helper). The repo
dropdown also hides repos that already have a ContentSource row.
"""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase

from integrations.models import ContentSource
from integrations.services.github import (
    INSTALLATION_REPOS_CACHE_KEY,
    GitHubSyncError,
)

User = get_user_model()


SAMPLE_REPOS = [
    {
        'full_name': 'AI-Shipping-Labs/blog',
        'private': False,
        'default_branch': 'main',
    },
    {
        'full_name': 'AI-Shipping-Labs/content',
        'private': True,
        'default_branch': 'main',
    },
]


class ContentSourceCreateViewTest(TestCase):
    """The Studio form to register a new ContentSource via the GitHub App."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def tearDown(self):
        cache.clear()

    # -- access control ------------------------------------------------------

    def test_anonymous_redirected_to_login(self):
        client = Client()
        response = client.get('/studio/content-sources/new/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_non_staff_user_gets_403(self):
        User.objects.create_user(
            email='member@test.com', password='testpass', is_staff=False,
        )
        client = Client()
        client.login(email='member@test.com', password='testpass')
        response = client.get('/studio/content-sources/new/')
        self.assertEqual(response.status_code, 403)

    # -- GET: rendering ------------------------------------------------------

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_get_renders_form_with_repo_dropdown(self, _mock_list):
        response = self.client.get('/studio/content-sources/new/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/content_sources/create.html')
        # Each repo full_name appears in an <option value="...">
        self.assertContains(
            response,
            'value="AI-Shipping-Labs/blog"',
        )
        self.assertContains(
            response,
            'value="AI-Shipping-Labs/content"',
        )

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_get_no_longer_renders_content_type_dropdown(self, _mock_list):
        """Issue #213: the manual content_type field is gone."""
        response = self.client.get('/studio/content-sources/new/')
        self.assertNotContains(response, 'name="content_type"')
        self.assertNotContains(response, 'id_content_type')

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_get_no_longer_renders_content_path_field(self, _mock_list):
        """Issue #213: the manual content_path field is gone."""
        response = self.client.get('/studio/content-sources/new/')
        self.assertNotContains(response, 'name="content_path"')
        self.assertNotContains(response, 'id_content_path')

    @patch('studio.views.content_sources.list_installation_repositories',
           side_effect=GitHubSyncError('GitHub App credentials not configured.'),
           )
    def test_get_shows_error_when_github_not_configured(self, _mock_list):
        response = self.client.get('/studio/content-sources/new/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'GitHub App credentials not configured',
        )
        # No repos listed in the dropdown beyond the placeholder option
        self.assertNotContains(response, 'AI-Shipping-Labs/blog')

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_get_marks_private_repos_in_label(self, _mock_list):
        response = self.client.get('/studio/content-sources/new/')
        # The private repo's label should include "(private)"
        self.assertContains(response, 'AI-Shipping-Labs/content (private)')

    # -- GET: filtering already-registered repos -----------------------------

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_get_hides_repos_with_existing_content_source(self, _mock_list):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        response = self.client.get('/studio/content-sources/new/')
        # Already-registered repo should not appear as a dropdown option.
        self.assertNotContains(response, 'value="AI-Shipping-Labs/blog"')
        # The other repo is still present.
        self.assertContains(response, 'value="AI-Shipping-Labs/content"')

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_get_hides_repo_with_any_content_type_registered(self, _mock_list):
        """A single ContentSource row of any content_type excludes the repo."""
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='event',
            content_path='events/',
        )
        response = self.client.get('/studio/content-sources/new/')
        self.assertNotContains(response, 'value="AI-Shipping-Labs/content"')

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_get_shows_empty_state_when_all_repos_registered(self, _mock_list):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog', content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content', content_type='event',
        )
        response = self.client.get('/studio/content-sources/new/')
        self.assertContains(
            response,
            'All accessible repos are already registered',
        )
        self.assertContains(response, 'Grant the GitHub App access to a new repo')
        # The repo dropdown / submit button should not render.
        self.assertNotContains(response, 'name="repo_name"')

    # -- POST: detection step (step=detect) ----------------------------------

    @patch('studio.views.content_sources.detect_content_sources',
           return_value=[
               {'content_type': 'article', 'content_path': 'blog',
                'summary': 'markdown with date frontmatter found in blog'},
           ])
    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_detect_shows_confirmation_step(self, _mock_list, _mock_detect):
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'detect',
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/content_sources/confirm.html')
        # Confirmation page lists the detected source.
        self.assertContains(response, 'article')
        self.assertContains(response, 'blog')
        self.assertContains(response, 'markdown with date frontmatter')
        # No row created yet -- this is just the preview.
        self.assertFalse(ContentSource.objects.exists())

    @patch('studio.views.content_sources.detect_content_sources',
           return_value=[
               {'content_type': 'course', 'content_path': 'courses',
                'summary': 'course.yaml found under courses/'},
               {'content_type': 'article', 'content_path': 'blog',
                'summary': 'markdown with date frontmatter found in blog'},
               {'content_type': 'event', 'content_path': 'events',
                'summary': 'YAML files with start_datetime found in events'},
           ])
    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_detect_monorepo_shows_all_matches(self, _mock_list, _mock_detect):
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'detect',
            'repo_name': 'AI-Shipping-Labs/content',
            'webhook_secret': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Detected')
        # All three detected types are offered as checkboxes.
        self.assertContains(response, 'value="course:courses"')
        self.assertContains(response, 'value="article:blog"')
        self.assertContains(response, 'value="event:events"')
        # Pluralised submit button copy reflects the count.
        self.assertContains(response, 'Create 3 sources')

    @patch('studio.views.content_sources.detect_content_sources',
           return_value=[])
    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_detect_shows_clear_error_when_nothing_matches(
        self, _mock_list, _mock_detect,
    ):
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'detect',
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': '',
        })
        self.assertEqual(response.status_code, 400)
        # Error tells the user what frontmatter / files to add.
        self.assertContains(
            response,
            'Could not detect a recognized content type',
            status_code=400,
        )
        self.assertContains(response, 'course.yaml', status_code=400)
        self.assertContains(response, 'date:', status_code=400)
        self.assertFalse(ContentSource.objects.exists())

    @patch('studio.views.content_sources.detect_content_sources',
           side_effect=GitHubSyncError('repo not found'))
    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_detect_shows_error_when_inspection_fails(
        self, _mock_list, _mock_detect,
    ):
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'detect',
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': '',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Could not inspect', status_code=400)
        self.assertFalse(ContentSource.objects.exists())

    # -- POST: confirm step (step=confirm) -----------------------------------

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_confirm_creates_single_source(self, _mock_list):
        detections = [
            {'content_type': 'article', 'content_path': 'blog',
             'summary': 'markdown with date frontmatter found in blog'},
        ]
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'confirm',
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': 'manual-secret',
            'detections_json': json.dumps(detections),
            'selected': ['article:blog'],
        })
        self.assertEqual(response.status_code, 302)
        # Redirects to the one-time webhook-secret display page (issue #213
        # PM blocker), not directly to the sync dashboard.
        self.assertEqual(
            response['Location'], '/studio/content-sources/created/',
        )

        source = ContentSource.objects.get(repo_name='AI-Shipping-Labs/blog')
        self.assertEqual(source.content_type, 'article')
        self.assertEqual(source.content_path, 'blog')
        self.assertEqual(source.webhook_secret, 'manual-secret')
        # ``is_private`` is taken from the API, not from the form
        self.assertFalse(source.is_private)

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_confirm_creates_multiple_sources_for_monorepo(self, _mock_list):
        detections = [
            {'content_type': 'course', 'content_path': 'courses',
             'summary': 'course.yaml found under courses/'},
            {'content_type': 'article', 'content_path': 'blog',
             'summary': 'markdown with date frontmatter found in blog'},
            {'content_type': 'event', 'content_path': 'events',
             'summary': 'YAML files with start_datetime found in events'},
        ]
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'confirm',
            'repo_name': 'AI-Shipping-Labs/content',
            'webhook_secret': '',
            'detections_json': json.dumps(detections),
            'selected': ['course:courses', 'article:blog', 'event:events'],
        })
        self.assertEqual(response.status_code, 302)
        sources = ContentSource.objects.filter(
            repo_name='AI-Shipping-Labs/content',
        )
        self.assertEqual(sources.count(), 3)
        self.assertEqual(
            set(sources.values_list('content_type', flat=True)),
            {'course', 'article', 'event'},
        )
        # All rows get the same auto-generated secret (single submission).
        secrets_used = set(sources.values_list('webhook_secret', flat=True))
        self.assertEqual(len(secrets_used), 1)
        self.assertGreaterEqual(len(secrets_used.pop()), 32)
        # Private flag is copied from the GitHub API for every row.
        self.assertTrue(all(s.is_private for s in sources))

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_confirm_only_creates_user_selected_subset(self, _mock_list):
        """Unticking a detected source skips it on submit."""
        detections = [
            {'content_type': 'course', 'content_path': 'courses', 'summary': ''},
            {'content_type': 'article', 'content_path': 'blog', 'summary': ''},
            {'content_type': 'event', 'content_path': 'events', 'summary': ''},
        ]
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'confirm',
            'repo_name': 'AI-Shipping-Labs/content',
            'webhook_secret': 'shh',
            'detections_json': json.dumps(detections),
            # User unticked event -- only course and article submitted.
            'selected': ['course:courses', 'article:blog'],
        })
        self.assertEqual(response.status_code, 302)
        types = set(ContentSource.objects.filter(
            repo_name='AI-Shipping-Labs/content',
        ).values_list('content_type', flat=True))
        self.assertEqual(types, {'course', 'article'})

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_confirm_with_zero_selected_re_renders_with_error(
        self, _mock_list,
    ):
        detections = [
            {'content_type': 'article', 'content_path': 'blog', 'summary': ''},
        ]
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'confirm',
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': '',
            'detections_json': json.dumps(detections),
            'selected': [],
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, 'at least one detected source', status_code=400,
        )
        self.assertFalse(ContentSource.objects.exists())

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_confirm_auto_generates_webhook_secret_when_blank(
        self, _mock_list,
    ):
        detections = [
            {'content_type': 'article', 'content_path': '', 'summary': ''},
        ]
        self.client.post('/studio/content-sources/new/', {
            'step': 'confirm',
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': '',
            'detections_json': json.dumps(detections),
            'selected': ['article:'],
        })
        source = ContentSource.objects.get(repo_name='AI-Shipping-Labs/blog')
        self.assertTrue(source.webhook_secret)
        self.assertGreaterEqual(len(source.webhook_secret), 32)

    # -- POST: validation ----------------------------------------------------

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_rejects_repo_not_in_installation(self, _mock_list):
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'detect',
            'repo_name': 'evil-org/private-stuff',
            'webhook_secret': '',
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ContentSource.objects.filter(
            repo_name='evil-org/private-stuff',
        ).exists())
        self.assertContains(
            response,
            'not accessible to the GitHub App installation',
            status_code=400,
        )

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_rejects_missing_repo(self, _mock_list):
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'detect',
            'repo_name': '',
            'webhook_secret': '',
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ContentSource.objects.exists())

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_rejects_repo_already_registered(self, _mock_list):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'detect',
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': '',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'already exists', status_code=400)
        # Still only one row.
        self.assertEqual(
            ContentSource.objects.filter(
                repo_name='AI-Shipping-Labs/blog',
            ).count(),
            1,
        )

    @patch('studio.views.content_sources.list_installation_repositories',
           side_effect=GitHubSyncError('boom'))
    def test_post_rejects_when_github_api_unreachable(self, _mock_list):
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'detect',
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': '',
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ContentSource.objects.exists())


class ContentSourceCreatedViewTest(TestCase):
    """The one-time "Webhook secret" panel rendered after a successful create.

    PM blocker on issue #213: the form copy promised to display the generated
    webhook secret, but the original implementation only flashed a generic
    success message and redirected -- the admin had no way to retrieve the
    secret from the Studio UI.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def tearDown(self):
        cache.clear()

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_auto_generated_secret_is_displayed_after_create(self, _mock_list):
        """Submitting the confirm step lands on a page that shows the secret.

        This is the regression test for the PM blocker: the auto-generated
        ``webhook_secret`` value MUST appear in the response that follows a
        successful create, so the admin can copy it to GitHub.
        """
        detections = [
            {'content_type': 'article', 'content_path': 'blog', 'summary': ''},
        ]
        # Submit with a blank secret -> the view auto-generates one.
        create_response = self.client.post('/studio/content-sources/new/', {
            'step': 'confirm',
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': '',
            'detections_json': json.dumps(detections),
            'selected': ['article:blog'],
        }, follow=True)

        self.assertEqual(create_response.status_code, 200)
        self.assertTemplateUsed(
            create_response,
            'studio/content_sources/created.html',
        )

        # The secret stored on the DB row must literally appear in the
        # rendered HTML so the admin can copy it.
        source = ContentSource.objects.get(repo_name='AI-Shipping-Labs/blog')
        self.assertContains(create_response, source.webhook_secret)
        # And the page advertises it as the webhook secret with copy support.
        self.assertContains(create_response, 'Webhook secret')
        self.assertContains(create_response, 'data-testid="webhook-secret"')
        self.assertContains(create_response, 'data-copy-target="webhook-secret-value"')

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_manual_secret_is_displayed_after_create(self, _mock_list):
        """Even if the admin typed in their own secret, we echo it back."""
        detections = [
            {'content_type': 'article', 'content_path': 'blog', 'summary': ''},
        ]
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'confirm',
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': 'my-custom-secret-abc123',
            'detections_json': json.dumps(detections),
            'selected': ['article:blog'],
        }, follow=True)
        self.assertContains(response, 'my-custom-secret-abc123')

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_monorepo_shows_secret_once_with_all_sources_listed(
        self, _mock_list,
    ):
        """One submission that creates multiple rows shares one secret panel."""
        detections = [
            {'content_type': 'course', 'content_path': 'courses', 'summary': ''},
            {'content_type': 'article', 'content_path': 'blog', 'summary': ''},
            {'content_type': 'event', 'content_path': 'events', 'summary': ''},
        ]
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'confirm',
            'repo_name': 'AI-Shipping-Labs/content',
            'webhook_secret': '',
            'detections_json': json.dumps(detections),
            'selected': ['course:courses', 'article:blog', 'event:events'],
        }, follow=True)

        sources = ContentSource.objects.filter(
            repo_name='AI-Shipping-Labs/content',
        )
        # Sanity: monorepo creates 3 rows that share the same secret.
        secrets_used = set(sources.values_list('webhook_secret', flat=True))
        self.assertEqual(len(secrets_used), 1)
        secret = secrets_used.pop()

        # The panel shows the shared secret exactly once and lists every
        # created source beneath it.
        self.assertEqual(response.content.decode().count(secret), 1)
        self.assertContains(response, 'course')
        self.assertContains(response, 'article')
        self.assertContains(response, 'event')
        self.assertContains(response, 'AI-Shipping-Labs/content')

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_secret_is_only_shown_once(self, _mock_list):
        """Reloading the success page after dismissing it does not leak the
        secret. The session stash is consumed on the first render."""
        detections = [
            {'content_type': 'article', 'content_path': 'blog', 'summary': ''},
        ]
        self.client.post('/studio/content-sources/new/', {
            'step': 'confirm',
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': 'one-time-secret-xyz',
            'detections_json': json.dumps(detections),
            'selected': ['article:blog'],
        }, follow=True)

        # Direct second visit -> redirect away from the page; the secret
        # value is no longer present anywhere in the resulting response.
        response = self.client.get(
            '/studio/content-sources/created/', follow=True,
        )
        self.assertNotContains(response, 'one-time-secret-xyz')
        # The user lands somewhere informational with a hint.
        self.assertContains(response, 'only displayed once')

    def test_direct_visit_without_session_redirects(self):
        """Hitting the created page cold (no session) bounces to the dashboard."""
        response = self.client.get('/studio/content-sources/created/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

    def test_created_page_requires_staff(self):
        client = Client()
        response = client.get('/studio/content-sources/created/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_created_page_non_staff_gets_403(self):
        User.objects.create_user(
            email='member@test.com', password='testpass', is_staff=False,
        )
        client = Client()
        client.login(email='member@test.com', password='testpass')
        response = client.get('/studio/content-sources/created/')
        self.assertEqual(response.status_code, 403)

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_panel_shows_github_webhook_url(self, _mock_list):
        """The panel exposes the URL the admin should paste into GitHub."""
        detections = [
            {'content_type': 'article', 'content_path': 'blog', 'summary': ''},
        ]
        response = self.client.post('/studio/content-sources/new/', {
            'step': 'confirm',
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': '',
            'detections_json': json.dumps(detections),
            'selected': ['article:blog'],
        }, follow=True)
        self.assertContains(response, '/api/webhooks/github')
        self.assertContains(response, 'Payload URL')


class ContentSourceRefreshViewTest(TestCase):
    """The "Refresh repo list" button drops the cached entry."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def tearDown(self):
        cache.clear()

    def test_refresh_clears_cache_and_redirects_to_form(self):
        cache.set(INSTALLATION_REPOS_CACHE_KEY, [{'full_name': 'cached/repo'}])
        response = self.client.post('/studio/content-sources/refresh/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/content-sources/new/')
        self.assertIsNone(cache.get(INSTALLATION_REPOS_CACHE_KEY))

    def test_refresh_requires_post(self):
        response = self.client.get('/studio/content-sources/refresh/')
        self.assertEqual(response.status_code, 405)

    def test_refresh_requires_staff(self):
        client = Client()
        response = client.post('/studio/content-sources/refresh/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_refresh_non_staff_gets_403(self):
        User.objects.create_user(
            email='member@test.com', password='testpass', is_staff=False,
        )
        client = Client()
        client.login(email='member@test.com', password='testpass')
        response = client.post('/studio/content-sources/refresh/')
        self.assertEqual(response.status_code, 403)


class SyncDashboardLinksToCreateTest(TestCase):
    """The sync dashboard exposes the new "Add content source" entry point."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_dashboard_has_link_to_add_form(self):
        response = self.client.get('/studio/sync/')
        self.assertContains(response, '/studio/content-sources/new/')
        self.assertContains(response, 'Add content source')


class AdminContentSourceFormUntouchedTest(TestCase):
    """The Django admin form for ContentSource still works as before.

    Issue #213 only changes the Studio flow. The admin form keeps the manual
    ``content_type`` / ``content_path`` fields as a power-user fallback.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='admin@test.com', password='testpass')

    def test_admin_changelist_loads(self):
        response = self.client.get('/admin/integrations/contentsource/')
        self.assertEqual(response.status_code, 200)

    def test_admin_add_form_loads_without_github_app(self):
        # No mocks, no GitHub creds: the admin form must still load because
        # power users can fall back to free-text entry.
        response = self.client.get('/admin/integrations/contentsource/add/')
        self.assertEqual(response.status_code, 200)

    def test_admin_add_form_keeps_content_type_and_path_fields(self):
        """The admin form still surfaces the manual fields (issue #213)."""
        response = self.client.get('/admin/integrations/contentsource/add/')
        self.assertContains(response, 'name="content_type"')
        self.assertContains(response, 'name="content_path"')
