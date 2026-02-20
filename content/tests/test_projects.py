"""Tests for Project Showcase - issue #75.

Covers:
- Project model fields (source_code_url, demo_url, cover_image_url, status,
  published_at, submitter)
- Difficulty and tag filtering on /projects
- Community submission via POST /api/projects/submit
- Admin approve/reject actions
- Access control (gating) on project detail
- Project detail shows source code and demo links
"""

import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from content.access import LEVEL_OPEN, LEVEL_BASIC
from content.models import Project

User = get_user_model()


# --- Model field tests ---


class ProjectNewFieldsTest(TestCase):
    """Test that Project has all fields required by issue #75."""

    def test_source_code_url_field(self):
        project = Project.objects.create(
            title='Test', slug='test-src', date=date(2025, 1, 1),
            source_code_url='https://github.com/test/repo',
        )
        self.assertEqual(project.source_code_url, 'https://github.com/test/repo')

    def test_source_code_url_default_empty(self):
        project = Project.objects.create(
            title='Test', slug='test-src-default', date=date(2025, 1, 1),
        )
        self.assertEqual(project.source_code_url, '')

    def test_demo_url_field(self):
        project = Project.objects.create(
            title='Test', slug='test-demo', date=date(2025, 1, 1),
            demo_url='https://demo.example.com',
        )
        self.assertEqual(project.demo_url, 'https://demo.example.com')

    def test_demo_url_default_empty(self):
        project = Project.objects.create(
            title='Test', slug='test-demo-default', date=date(2025, 1, 1),
        )
        self.assertEqual(project.demo_url, '')

    def test_cover_image_url_field(self):
        project = Project.objects.create(
            title='Test', slug='test-cover', date=date(2025, 1, 1),
            cover_image_url='https://example.com/image.png',
        )
        self.assertEqual(project.cover_image_url, 'https://example.com/image.png')

    def test_cover_image_url_default_empty(self):
        project = Project.objects.create(
            title='Test', slug='test-cover-default', date=date(2025, 1, 1),
        )
        self.assertEqual(project.cover_image_url, '')

    def test_status_field_default_published(self):
        project = Project.objects.create(
            title='Test', slug='test-status', date=date(2025, 1, 1),
            published=True,
        )
        self.assertEqual(project.status, 'published')

    def test_status_pending_review(self):
        project = Project.objects.create(
            title='Test', slug='test-pending', date=date(2025, 1, 1),
            status='pending_review', published=False,
        )
        self.assertEqual(project.status, 'pending_review')

    def test_published_at_set_when_published(self):
        project = Project.objects.create(
            title='Test', slug='test-pub-at', date=date(2025, 1, 1),
            published=True,
        )
        self.assertIsNotNone(project.published_at)

    def test_published_at_null_when_not_published(self):
        project = Project.objects.create(
            title='Test', slug='test-no-pub-at', date=date(2025, 1, 1),
            published=False, status='pending_review',
        )
        self.assertIsNone(project.published_at)

    def test_submitter_field(self):
        user = User.objects.create_user(email='submitter@test.com')
        project = Project.objects.create(
            title='Test', slug='test-submitter', date=date(2025, 1, 1),
            submitter=user,
        )
        self.assertEqual(project.submitter, user)

    def test_submitter_null_by_default(self):
        project = Project.objects.create(
            title='Test', slug='test-no-submitter', date=date(2025, 1, 1),
        )
        self.assertIsNone(project.submitter)

    def test_difficulty_choices(self):
        valid = ['beginner', 'intermediate', 'advanced']
        for diff in valid:
            project = Project.objects.create(
                title=f'Test {diff}', slug=f'test-{diff}', date=date(2025, 1, 1),
                difficulty=diff,
            )
            self.assertEqual(project.difficulty, diff)


# --- Status sync tests ---


class ProjectStatusSyncTest(TestCase):
    """Test that status and published stay in sync on save."""

    def test_published_true_sets_status_published(self):
        project = Project.objects.create(
            title='Pub', slug='pub-sync', date=date(2025, 1, 1),
            published=True,
        )
        self.assertEqual(project.status, 'published')

    def test_published_false_keeps_pending_review(self):
        project = Project.objects.create(
            title='Pending', slug='pending-sync', date=date(2025, 1, 1),
            published=False, status='pending_review',
        )
        self.assertEqual(project.status, 'pending_review')

    def test_approve_method(self):
        project = Project.objects.create(
            title='Test', slug='approve-test', date=date(2025, 1, 1),
            published=False, status='pending_review',
        )
        self.assertFalse(project.published)
        project.approve()
        project.refresh_from_db()
        self.assertTrue(project.published)
        self.assertEqual(project.status, 'published')
        self.assertIsNotNone(project.published_at)

    def test_reject_method(self):
        project = Project.objects.create(
            title='Test', slug='reject-test', date=date(2025, 1, 1),
            published=True,
        )
        self.assertTrue(project.published)
        project.reject()
        project.refresh_from_db()
        self.assertFalse(project.published)
        self.assertEqual(project.status, 'pending_review')


# --- Filtering tests ---


class ProjectsListFilteringTest(TestCase):
    """Test difficulty and tag filtering on /projects."""

    def setUp(self):
        self.client = Client()
        self.beginner_project = Project.objects.create(
            title='Beginner Project',
            slug='beginner-project',
            description='Easy project',
            date=date(2025, 8, 10),
            difficulty='beginner',
            tags=['python', 'tutorial'],
            published=True,
        )
        self.advanced_project = Project.objects.create(
            title='Advanced Project',
            slug='advanced-project',
            description='Hard project',
            date=date(2025, 8, 9),
            difficulty='advanced',
            tags=['ai', 'agents'],
            published=True,
        )
        self.intermediate_project = Project.objects.create(
            title='Intermediate Project',
            slug='intermediate-project',
            description='Medium project',
            date=date(2025, 8, 8),
            difficulty='intermediate',
            tags=['python', 'ai'],
            published=True,
        )

    def test_no_filter_shows_all_projects(self):
        response = self.client.get('/projects')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Beginner Project')
        self.assertContains(response, 'Advanced Project')
        self.assertContains(response, 'Intermediate Project')

    def test_filter_by_difficulty_beginner(self):
        response = self.client.get('/projects?difficulty=beginner')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Beginner Project')
        self.assertNotContains(response, 'Advanced Project')
        self.assertNotContains(response, 'Intermediate Project')

    def test_filter_by_difficulty_advanced(self):
        response = self.client.get('/projects?difficulty=advanced')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Advanced Project')
        self.assertNotContains(response, 'Beginner Project')
        self.assertNotContains(response, 'Intermediate Project')

    def test_filter_by_tag_python(self):
        response = self.client.get('/projects?tag=python')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Beginner Project')
        self.assertContains(response, 'Intermediate Project')
        self.assertNotContains(response, 'Advanced Project')

    def test_filter_by_tag_ai(self):
        response = self.client.get('/projects?tag=ai')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Advanced Project')
        self.assertContains(response, 'Intermediate Project')
        self.assertNotContains(response, 'Beginner Project')

    def test_filter_by_both_difficulty_and_tag(self):
        response = self.client.get('/projects?difficulty=intermediate&tag=python')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Intermediate Project')
        self.assertNotContains(response, 'Beginner Project')
        self.assertNotContains(response, 'Advanced Project')

    def test_filter_by_nonexistent_tag(self):
        response = self.client.get('/projects?tag=nonexistent')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Beginner Project')
        self.assertNotContains(response, 'Advanced Project')

    def test_filter_by_nonexistent_difficulty(self):
        response = self.client.get('/projects?difficulty=expert')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Beginner Project')
        self.assertNotContains(response, 'Advanced Project')
        self.assertNotContains(response, 'Intermediate Project')

    def test_context_has_all_tags(self):
        response = self.client.get('/projects')
        all_tags = response.context['all_tags']
        self.assertIn('python', all_tags)
        self.assertIn('ai', all_tags)
        self.assertIn('tutorial', all_tags)
        self.assertIn('agents', all_tags)

    def test_context_has_all_difficulties(self):
        response = self.client.get('/projects')
        all_difficulties = response.context['all_difficulties']
        self.assertIn('beginner', all_difficulties)
        self.assertIn('intermediate', all_difficulties)
        self.assertIn('advanced', all_difficulties)

    def test_context_has_current_filters(self):
        response = self.client.get('/projects?difficulty=beginner&tag=python')
        self.assertEqual(response.context['current_difficulty'], 'beginner')
        self.assertEqual(response.context['current_tag'], 'python')

    def test_unpublished_not_in_listing(self):
        Project.objects.create(
            title='Unpublished Project',
            slug='unpublished-project',
            date=date(2025, 8, 7),
            published=False,
            status='pending_review',
        )
        response = self.client.get('/projects')
        self.assertNotContains(response, 'Unpublished Project')

    def test_pending_review_not_in_listing(self):
        Project.objects.create(
            title='Pending Project',
            slug='pending-project',
            date=date(2025, 8, 7),
            published=False,
            status='pending_review',
        )
        response = self.client.get('/projects')
        self.assertNotContains(response, 'Pending Project')


# --- Projects list display tests ---


class ProjectsListDisplayTest(TestCase):
    """Test that project list cards show all required elements."""

    def setUp(self):
        self.client = Client()
        self.project = Project.objects.create(
            title='Display Project',
            slug='display-project',
            description='A project description',
            cover_image_url='https://example.com/project-cover.jpg',
            date=date(2025, 8, 10),
            author='Project Author',
            difficulty='beginner',
            tags=['python', 'ai'],
            published=True,
        )

    def test_shows_title(self):
        response = self.client.get('/projects')
        self.assertContains(response, 'Display Project')

    def test_shows_author(self):
        response = self.client.get('/projects')
        self.assertContains(response, 'Project Author')

    def test_shows_difficulty_badge(self):
        response = self.client.get('/projects')
        self.assertContains(response, 'beginner')

    def test_shows_tag_badges(self):
        response = self.client.get('/projects')
        self.assertContains(response, 'python')
        self.assertContains(response, 'ai')

    def test_shows_cover_image(self):
        response = self.client.get('/projects')
        self.assertContains(response, 'https://example.com/project-cover.jpg')

    def test_shows_description(self):
        response = self.client.get('/projects')
        self.assertContains(response, 'A project description')


# --- Project detail display tests ---


class ProjectDetailDisplayTest(TestCase):
    """Test that project detail page shows all required elements."""

    def setUp(self):
        self.client = Client()
        self.project = Project.objects.create(
            title='Detail Project',
            slug='detail-project',
            description='Detailed description',
            content_html='<p>Full project content</p>',
            cover_image_url='https://example.com/detail-cover.jpg',
            date=date(2025, 8, 10),
            author='Detail Author',
            difficulty='intermediate',
            tags=['ai', 'mcp'],
            source_code_url='https://github.com/test/project',
            demo_url='https://demo.example.com/project',
            published=True,
        )

    def test_shows_title(self):
        response = self.client.get('/projects/detail-project')
        self.assertContains(response, 'Detail Project')

    def test_shows_author(self):
        response = self.client.get('/projects/detail-project')
        self.assertContains(response, 'by Detail Author')

    def test_shows_difficulty(self):
        response = self.client.get('/projects/detail-project')
        self.assertContains(response, 'intermediate')

    def test_shows_description(self):
        response = self.client.get('/projects/detail-project')
        self.assertContains(response, 'Detailed description')

    def test_shows_content(self):
        response = self.client.get('/projects/detail-project')
        self.assertContains(response, 'Full project content')

    def test_shows_source_code_link(self):
        response = self.client.get('/projects/detail-project')
        self.assertContains(response, 'https://github.com/test/project')
        self.assertContains(response, 'Source Code')

    def test_shows_demo_link(self):
        response = self.client.get('/projects/detail-project')
        self.assertContains(response, 'https://demo.example.com/project')
        self.assertContains(response, 'Live Demo')

    def test_shows_cover_image(self):
        response = self.client.get('/projects/detail-project')
        self.assertContains(response, 'https://example.com/detail-cover.jpg')

    def test_shows_tags(self):
        response = self.client.get('/projects/detail-project')
        self.assertContains(response, 'ai')
        self.assertContains(response, 'mcp')

    def test_no_source_code_link_when_empty(self):
        project = Project.objects.create(
            title='No Links', slug='no-links', date=date(2025, 1, 1),
            published=True,
        )
        response = self.client.get('/projects/no-links')
        self.assertNotContains(response, 'Source Code')
        self.assertNotContains(response, 'Live Demo')


# --- Access control tests ---


class ProjectDetailGatingTest(TestCase):
    """Test access control gating on project detail page."""

    def setUp(self):
        self.client = Client()
        self.open_project = Project.objects.create(
            title='Open Project', slug='open-project',
            description='Open description',
            content_html='<p>Full open project content</p>',
            date=date(2025, 8, 10), published=True,
            required_level=LEVEL_OPEN,
        )
        self.gated_project = Project.objects.create(
            title='Gated Project', slug='gated-project',
            description='Gated description',
            content_html='<p>Secret gated project content</p>',
            date=date(2025, 8, 9), published=True,
            required_level=LEVEL_BASIC,
        )

    def test_anonymous_sees_open_project_full_content(self):
        response = self.client.get('/projects/open-project')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Full open project content')

    def test_anonymous_sees_gated_teaser_and_cta(self):
        response = self.client.get('/projects/gated-project')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Secret gated project content')
        self.assertContains(response, 'Upgrade to Basic to view this project')
        self.assertContains(response, '/pricing')

    def test_gated_project_returns_200_not_404(self):
        response = self.client.get('/projects/gated-project')
        self.assertEqual(response.status_code, 200)


# --- Community submission tests ---


class ProjectSubmissionAPITest(TestCase):
    """Test POST /api/projects/submit endpoint."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email='member@test.com', password='testpass',
        )

    def test_anonymous_user_gets_401(self):
        response = self.client.post(
            '/api/projects/submit',
            data=json.dumps({
                'title': 'My Project',
                'description': 'A description',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
        data = json.loads(response.content)
        self.assertEqual(data['error'], 'Authentication required')

    def test_authenticated_user_can_submit(self):
        self.client.login(email='member@test.com', password='testpass')
        response = self.client.post(
            '/api/projects/submit',
            data=json.dumps({
                'title': 'My Project',
                'description': 'A great project',
                'difficulty': 'beginner',
                'tags': ['python', 'ai'],
                'source_code_url': 'https://github.com/test/repo',
                'demo_url': 'https://demo.example.com',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertEqual(data['status'], 'pending_review')
        self.assertIn('slug', data)
        self.assertEqual(data['message'], 'Project submitted for review')

    def test_submitted_project_is_pending_review(self):
        self.client.login(email='member@test.com', password='testpass')
        self.client.post(
            '/api/projects/submit',
            data=json.dumps({
                'title': 'Pending Project',
                'description': 'Description',
            }),
            content_type='application/json',
        )
        project = Project.objects.get(slug='pending-project')
        self.assertEqual(project.status, 'pending_review')
        self.assertFalse(project.published)
        self.assertEqual(project.submitter, self.user)

    def test_submitted_project_not_visible_in_listing(self):
        self.client.login(email='member@test.com', password='testpass')
        self.client.post(
            '/api/projects/submit',
            data=json.dumps({
                'title': 'Hidden Submission',
                'description': 'Should not be visible',
            }),
            content_type='application/json',
        )
        response = self.client.get('/projects')
        self.assertNotContains(response, 'Hidden Submission')

    def test_title_required(self):
        self.client.login(email='member@test.com', password='testpass')
        response = self.client.post(
            '/api/projects/submit',
            data=json.dumps({
                'description': 'No title',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertEqual(data['error'], 'Title is required')

    def test_description_required(self):
        self.client.login(email='member@test.com', password='testpass')
        response = self.client.post(
            '/api/projects/submit',
            data=json.dumps({
                'title': 'No Description',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertEqual(data['error'], 'Description is required')

    def test_invalid_difficulty_rejected(self):
        self.client.login(email='member@test.com', password='testpass')
        response = self.client.post(
            '/api/projects/submit',
            data=json.dumps({
                'title': 'Bad Difficulty',
                'description': 'Description',
                'difficulty': 'expert',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn('Invalid difficulty', data['error'])

    def test_invalid_json_returns_400(self):
        self.client.login(email='member@test.com', password='testpass')
        response = self.client.post(
            '/api/projects/submit',
            data='not json',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertEqual(data['error'], 'Invalid JSON')

    def test_tags_must_be_list(self):
        self.client.login(email='member@test.com', password='testpass')
        response = self.client.post(
            '/api/projects/submit',
            data=json.dumps({
                'title': 'Bad Tags',
                'description': 'Description',
                'tags': 'not-a-list',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertEqual(data['error'], 'Tags must be a list')

    def test_duplicate_slug_gets_incremented(self):
        # Create a project with slug "my-project"
        Project.objects.create(
            title='My Project', slug='my-project', date=date(2025, 1, 1),
            published=True,
        )
        self.client.login(email='member@test.com', password='testpass')
        response = self.client.post(
            '/api/projects/submit',
            data=json.dumps({
                'title': 'My Project',
                'description': 'Another project with same title',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertEqual(data['slug'], 'my-project-1')

    def test_get_method_not_allowed(self):
        self.client.login(email='member@test.com', password='testpass')
        response = self.client.get('/api/projects/submit')
        self.assertEqual(response.status_code, 405)

    def test_submitter_set_to_current_user(self):
        self.client.login(email='member@test.com', password='testpass')
        self.client.post(
            '/api/projects/submit',
            data=json.dumps({
                'title': 'Submitter Test',
                'description': 'Testing submitter',
            }),
            content_type='application/json',
        )
        project = Project.objects.get(slug='submitter-test')
        self.assertEqual(project.submitter, self.user)
        # Author should default to user email since get_full_name() returns ''
        self.assertEqual(project.author, 'member@test.com')


# --- Admin tests ---


class ProjectAdminTest(TestCase):
    """Test admin CRUD and approve/reject for projects."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    def test_admin_project_list(self):
        Project.objects.create(
            title='Admin Project', slug='admin-project',
            date=date(2025, 8, 10), published=True,
        )
        response = self.client.get('/admin/content/project/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Admin Project')

    def test_admin_project_add_page(self):
        response = self.client.get('/admin/content/project/add/')
        self.assertEqual(response.status_code, 200)

    def test_admin_create_project(self):
        response = self.client.post('/admin/content/project/add/', {
            'title': 'New Project',
            'slug': 'new-project',
            'description': 'A new project',
            'content_markdown': '# New Project',
            'cover_image_url': '',
            'source_code_url': '',
            'demo_url': '',
            'author': 'Admin',
            'difficulty': 'beginner',
            'tags': '[]',
            'required_level': 0,
            'status': 'published',
            'published': True,
            'date': '2025-08-10',
        })
        self.assertEqual(Project.objects.filter(slug='new-project').count(), 1)

    def test_admin_edit_project(self):
        project = Project.objects.create(
            title='Edit Me', slug='edit-me', date=date(2025, 8, 10),
            published=True,
        )
        response = self.client.get(f'/admin/content/project/{project.pk}/change/')
        self.assertEqual(response.status_code, 200)

    def test_admin_delete_project(self):
        project = Project.objects.create(
            title='Delete Me', slug='delete-me', date=date(2025, 8, 10),
            published=True,
        )
        response = self.client.post(
            f'/admin/content/project/{project.pk}/delete/',
            {'post': 'yes'},
        )
        self.assertEqual(Project.objects.filter(slug='delete-me').count(), 0)

    def test_admin_approve_action(self):
        project = Project.objects.create(
            title='Pending', slug='pending-approve',
            date=date(2025, 8, 10),
            published=False, status='pending_review',
        )
        response = self.client.post('/admin/content/project/', {
            'action': 'approve_projects',
            '_selected_action': [project.pk],
        })
        project.refresh_from_db()
        self.assertTrue(project.published)
        self.assertEqual(project.status, 'published')
        self.assertIsNotNone(project.published_at)

    def test_admin_reject_action(self):
        project = Project.objects.create(
            title='Published', slug='published-reject',
            date=date(2025, 8, 10),
            published=True,
        )
        response = self.client.post('/admin/content/project/', {
            'action': 'reject_projects',
            '_selected_action': [project.pk],
        })
        project.refresh_from_db()
        self.assertFalse(project.published)
        self.assertEqual(project.status, 'pending_review')

    def test_admin_sees_pending_projects(self):
        Project.objects.create(
            title='Pending Submission', slug='pending-submission',
            date=date(2025, 8, 10),
            published=False, status='pending_review',
        )
        response = self.client.get('/admin/content/project/?status__exact=pending_review')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pending Submission')

    def test_admin_search(self):
        Project.objects.create(
            title='Searchable Project', slug='searchable',
            description='find me', date=date(2025, 8, 10),
            published=True,
        )
        response = self.client.get('/admin/content/project/?q=Searchable')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Searchable Project')

    def test_admin_slug_auto_generated(self):
        """Verify prepopulated_fields config for slug from title."""
        from content.admin.project import ProjectAdmin
        self.assertEqual(ProjectAdmin.prepopulated_fields, {'slug': ('title',)})
