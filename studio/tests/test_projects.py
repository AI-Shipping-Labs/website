"""Tests for studio project moderation views."""

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from content.models import Project

User = get_user_model()


class StudioProjectListTest(TestCase):
    """Test project list view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_list_returns_200(self):
        response = self.client.get('/studio/projects/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/projects/')
        self.assertTemplateUsed(response, 'studio/projects/list.html')

    def test_list_shows_projects(self):
        Project.objects.create(
            title='Test Project', slug='test-proj',
            date=timezone.now().date(),
        )
        response = self.client.get('/studio/projects/')
        self.assertContains(response, 'Test Project')

    def test_list_filter_pending(self):
        Project.objects.create(
            title='Pending', slug='pending',
            date=timezone.now().date(),
            status='pending_review', published=False,
        )
        Project.objects.create(
            title='Published', slug='published',
            date=timezone.now().date(),
            status='published', published=True,
        )
        response = self.client.get('/studio/projects/?status=pending_review')
        self.assertContains(response, 'Pending')

    def test_list_shows_pending_count(self):
        Project.objects.create(
            title='P1', slug='p1', date=timezone.now().date(),
            status='pending_review', published=False,
        )
        response = self.client.get('/studio/projects/')
        self.assertEqual(response.context['pending_count'], 1)

    def test_list_search(self):
        Project.objects.create(
            title='AI Project', slug='ai',
            date=timezone.now().date(),
        )
        Project.objects.create(
            title='Web Project', slug='web',
            date=timezone.now().date(),
        )
        response = self.client.get('/studio/projects/?q=AI')
        self.assertContains(response, 'AI Project')
        self.assertNotContains(response, 'Web Project')


class StudioProjectReviewTest(TestCase):
    """Test project review (approve/reject) view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.project = Project.objects.create(
            title='Review Project', slug='review-proj',
            date=timezone.now().date(),
            status='pending_review', published=False,
        )

    def test_review_page_returns_200(self):
        response = self.client.get(f'/studio/projects/{self.project.pk}/review')
        self.assertEqual(response.status_code, 200)

    def test_review_page_shows_project_info(self):
        response = self.client.get(f'/studio/projects/{self.project.pk}/review')
        self.assertContains(response, 'Review Project')
        self.assertContains(response, 'Approve')
        self.assertContains(response, 'Reject')

    def test_approve_project(self):
        response = self.client.post(
            f'/studio/projects/{self.project.pk}/review',
            {'action': 'approve'},
        )
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'published')
        self.assertTrue(self.project.published)

    def test_reject_project(self):
        response = self.client.post(
            f'/studio/projects/{self.project.pk}/review',
            {'action': 'reject'},
        )
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'pending_review')
        self.assertFalse(self.project.published)

    def test_approve_redirects_to_list(self):
        response = self.client.post(
            f'/studio/projects/{self.project.pk}/review',
            {'action': 'approve'},
        )
        self.assertRedirects(
            response, '/studio/projects/',
            fetch_redirect_response=False,
        )

    def test_review_nonexistent_returns_404(self):
        response = self.client.get('/studio/projects/99999/review')
        self.assertEqual(response.status_code, 404)

    def test_published_project_no_approve_reject_buttons(self):
        """Published projects should not show approve/reject buttons."""
        self.project.approve()
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'published')
        response = self.client.get(f'/studio/projects/{self.project.pk}/review')
        # The approve/reject forms should not be shown for published projects
        self.assertNotContains(response, 'value="approve"')
        self.assertNotContains(response, 'value="reject"')
