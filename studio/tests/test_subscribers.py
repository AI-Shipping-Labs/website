"""Tests for studio subscriber management views."""

from django.contrib.auth import get_user_model
from django.test import TestCase, Client

from email_app.models import NewsletterSubscriber

User = get_user_model()


class StudioSubscriberListTest(TestCase):
    """Test subscriber list view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_list_returns_200(self):
        response = self.client.get('/studio/subscribers/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/subscribers/')
        self.assertTemplateUsed(response, 'studio/subscribers/list.html')

    def test_list_shows_subscribers(self):
        NewsletterSubscriber.objects.create(email='sub@test.com', is_active=True)
        response = self.client.get('/studio/subscribers/')
        self.assertContains(response, 'sub@test.com')

    def test_list_filter_active(self):
        NewsletterSubscriber.objects.create(email='alice-enabled@test.com', is_active=True)
        NewsletterSubscriber.objects.create(email='bob-disabled@test.com', is_active=False)
        response = self.client.get('/studio/subscribers/?status=active')
        self.assertContains(response, 'alice-enabled@test.com')
        self.assertNotContains(response, 'bob-disabled@test.com')

    def test_list_filter_inactive(self):
        NewsletterSubscriber.objects.create(email='alice-enabled@test.com', is_active=True)
        NewsletterSubscriber.objects.create(email='bob-disabled@test.com', is_active=False)
        response = self.client.get('/studio/subscribers/?status=inactive')
        self.assertContains(response, 'bob-disabled@test.com')
        self.assertNotContains(response, 'alice-enabled@test.com')

    def test_list_search(self):
        NewsletterSubscriber.objects.create(email='alice@test.com', is_active=True)
        NewsletterSubscriber.objects.create(email='bob@test.com', is_active=True)
        response = self.client.get('/studio/subscribers/?q=alice')
        self.assertContains(response, 'alice@test.com')
        self.assertNotContains(response, 'bob@test.com')

    def test_list_shows_stats(self):
        NewsletterSubscriber.objects.create(email='a@test.com', is_active=True)
        NewsletterSubscriber.objects.create(email='b@test.com', is_active=False)
        response = self.client.get('/studio/subscribers/')
        self.assertEqual(response.context['total_count'], 2)
        self.assertEqual(response.context['active_count'], 1)
        self.assertEqual(response.context['inactive_count'], 1)


class StudioSubscriberExportTest(TestCase):
    """Test subscriber CSV export."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_export_returns_csv(self):
        NewsletterSubscriber.objects.create(email='sub@test.com', is_active=True)
        response = self.client.get('/studio/subscribers/export')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('attachment', response['Content-Disposition'])

    def test_export_contains_header(self):
        response = self.client.get('/studio/subscribers/export')
        content = response.content.decode()
        self.assertIn('Email', content)
        self.assertIn('Subscribed At', content)
        self.assertIn('Active', content)

    def test_export_contains_data(self):
        NewsletterSubscriber.objects.create(email='export@test.com', is_active=True)
        response = self.client.get('/studio/subscribers/export')
        content = response.content.decode()
        self.assertIn('export@test.com', content)
        self.assertIn('Yes', content)

    def test_export_filter_active_only(self):
        NewsletterSubscriber.objects.create(email='alice-enabled@test.com', is_active=True)
        NewsletterSubscriber.objects.create(email='bob-disabled@test.com', is_active=False)
        response = self.client.get('/studio/subscribers/export?status=active')
        content = response.content.decode()
        self.assertIn('alice-enabled@test.com', content)
        self.assertNotIn('bob-disabled@test.com', content)

    def test_export_filter_inactive_only(self):
        NewsletterSubscriber.objects.create(email='alice-enabled@test.com', is_active=True)
        NewsletterSubscriber.objects.create(email='bob-disabled@test.com', is_active=False)
        response = self.client.get('/studio/subscribers/export?status=inactive')
        content = response.content.decode()
        self.assertNotIn('alice-enabled@test.com', content)
        self.assertIn('bob-disabled@test.com', content)

    def test_export_non_staff_forbidden(self):
        """Non-staff users cannot export subscribers."""
        regular_user = User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/subscribers/export')
        self.assertEqual(response.status_code, 403)
