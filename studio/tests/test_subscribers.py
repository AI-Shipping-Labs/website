"""Tests for the studio Users list / CSV export and subscriber redirect shims.

The page lives at ``/studio/users/`` (issue #271). The old
``/studio/subscribers/`` URLs are 301 redirect shims kept around for
bookmarks; they are exercised at the end of the file.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from email_app.models import NewsletterSubscriber
from payments.models import Tier

User = get_user_model()


class StudioUserListTest(TestCase):
    """Render and filter the /studio/users/ page."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        # A regular user who is also a newsletter subscriber.
        cls.regular_subscriber = User.objects.create_user(
            email='alice@test.com', password='testpass',
        )
        NewsletterSubscriber.objects.create(
            email='alice@test.com', is_active=True,
        )
        # A regular user who is NOT a subscriber.
        cls.regular_only = User.objects.create_user(
            email='bob@test.com', password='testpass',
        )
        # A subscriber with no platform user account -- should NOT appear in
        # any chip because the page lists Users, not subscriber rows.
        NewsletterSubscriber.objects.create(
            email='ghost@test.com', is_active=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    # --- rendering / template ---------------------------------------

    def test_list_returns_200(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/users/')
        self.assertTemplateUsed(response, 'studio/users/list.html')

    # --- default chip is Subscribers --------------------------------

    def test_default_filter_is_subscribers(self):
        """Hitting the page with no query string lands on the Subscribers chip."""
        response = self.client.get('/studio/users/')
        self.assertEqual(response.context['active_filter'], 'subscribers')

    def test_default_view_lists_only_subscriber_users(self):
        """With the default chip, only Users who are active subscribers appear."""
        response = self.client.get('/studio/users/')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertIn('alice@test.com', emails)
        self.assertNotIn('bob@test.com', emails)
        self.assertNotIn('staff@test.com', emails)
        # Subscriber-without-account never appears -- this page lists Users.
        self.assertNotIn('ghost@test.com', emails)

    # --- chip switching ---------------------------------------------

    def test_filter_all_lists_every_user(self):
        response = self.client.get('/studio/users/?filter=all')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertIn('alice@test.com', emails)
        self.assertIn('bob@test.com', emails)
        self.assertIn('staff@test.com', emails)

    def test_filter_non_subscribers(self):
        response = self.client.get('/studio/users/?filter=non_subscribers')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertNotIn('alice@test.com', emails)
        self.assertIn('bob@test.com', emails)
        self.assertIn('staff@test.com', emails)

    def test_filter_staff(self):
        response = self.client.get('/studio/users/?filter=staff')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(emails, ['staff@test.com'])

    def test_unknown_filter_falls_back_to_subscribers(self):
        response = self.client.get('/studio/users/?filter=garbage')
        self.assertEqual(response.context['active_filter'], 'subscribers')

    # --- search box -------------------------------------------------

    def test_search_filters_within_chip(self):
        """Search narrows results within the active chip."""
        response = self.client.get('/studio/users/?filter=all&q=alice')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(emails, ['alice@test.com'])

    def test_search_value_preserved_in_form(self):
        """The search box keeps its value after submitting (used by the chips)."""
        response = self.client.get('/studio/users/?filter=all&q=alice')
        self.assertContains(response, 'value="alice"')

    def test_chip_links_carry_search_value(self):
        """Switching chips preserves the active search query."""
        response = self.client.get('/studio/users/?filter=all&q=alice')
        # Each chip link includes q=alice so clicking does not lose it.
        # The template uses an HTML-entity ampersand for spec compliance.
        self.assertContains(response, '?filter=all&amp;q=alice')
        self.assertContains(response, '?filter=subscribers&amp;q=alice')
        self.assertContains(response, '?filter=non_subscribers&amp;q=alice')
        self.assertContains(response, '?filter=staff&amp;q=alice')

    # --- row decoration ---------------------------------------------

    def test_subscribed_column_yes_for_subscriber(self):
        response = self.client.get('/studio/users/?filter=all')
        rows = {r['email']: r for r in response.context['user_rows']}
        self.assertTrue(rows['alice@test.com']['is_subscribed'])
        self.assertFalse(rows['bob@test.com']['is_subscribed'])

    def test_inactive_subscriber_renders_as_not_subscribed(self):
        """A NewsletterSubscriber row with is_active=False does not count."""
        User.objects.create_user(email='unsubbed@test.com', password='x')
        NewsletterSubscriber.objects.create(
            email='unsubbed@test.com', is_active=False,
        )
        response = self.client.get('/studio/users/?filter=all')
        rows = {r['email']: r for r in response.context['user_rows']}
        self.assertFalse(rows['unsubbed@test.com']['is_subscribed'])

    def test_tier_column_shows_user_tier_name(self):
        """The Tier column reflects User.tier (defaulting to 'Free')."""
        response = self.client.get('/studio/users/?filter=all')
        rows = {r['email']: r for r in response.context['user_rows']}
        # Default tier on creation is 'free', whose display name is 'Free'.
        self.assertEqual(rows['alice@test.com']['tier_name'], 'Free')

    def test_tier_column_shows_paid_tier_name(self):
        paid = Tier.objects.exclude(slug='free').order_by('level').first()
        self.assertIsNotNone(paid, 'tier seed migration should provide a paid tier')
        paid_user = User.objects.create_user(email='paid@test.com', password='x')
        paid_user.tier = paid
        paid_user.save()
        response = self.client.get('/studio/users/?filter=all')
        rows = {r['email']: r for r in response.context['user_rows']}
        self.assertEqual(rows['paid@test.com']['tier_name'], paid.name)

    def test_status_column_marks_staff(self):
        response = self.client.get('/studio/users/?filter=all')
        rows = {r['email']: r for r in response.context['user_rows']}
        self.assertEqual(rows['staff@test.com']['status'], 'Staff')

    def test_status_column_marks_inactive(self):
        User.objects.create_user(
            email='deactivated@test.com', password='x', is_active=False,
        )
        response = self.client.get('/studio/users/?filter=all')
        rows = {r['email']: r for r in response.context['user_rows']}
        self.assertEqual(rows['deactivated@test.com']['status'], 'Inactive')

    def test_status_column_marks_regular_active(self):
        response = self.client.get('/studio/users/?filter=all')
        rows = {r['email']: r for r in response.context['user_rows']}
        self.assertEqual(rows['bob@test.com']['status'], 'Active')

    # --- Login as button --------------------------------------------

    def test_login_as_button_present_for_every_user(self):
        """The Login as form posts to /studio/impersonate/<pk>/ for every row."""
        response = self.client.get('/studio/users/?filter=all')
        self.assertContains(
            response, f'/studio/impersonate/{self.regular_subscriber.pk}/'
        )
        self.assertContains(
            response, f'/studio/impersonate/{self.regular_only.pk}/'
        )

    # --- counts -----------------------------------------------------

    def test_counts_in_context(self):
        response = self.client.get('/studio/users/')
        # 3 users seeded on the class + ghost subscriber has NO user
        self.assertEqual(response.context['total_users'], 3)
        # alice is the only user with an active subscription
        self.assertEqual(response.context['subscriber_count'], 1)
        self.assertEqual(response.context['non_subscriber_count'], 2)
        self.assertEqual(response.context['staff_count'], 1)


class StudioUserExportTest(TestCase):
    """CSV export at /studio/users/export."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='testpass',
        )
        NewsletterSubscriber.objects.create(
            email='alice@test.com', is_active=True,
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='testpass',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_export_returns_csv(self):
        response = self.client.get('/studio/users/export')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('users.csv', response['Content-Disposition'])

    def test_export_header_lists_new_columns(self):
        response = self.client.get('/studio/users/export')
        first_line = response.content.decode().splitlines()[0]
        self.assertEqual(first_line, 'Email,Joined,Subscribed,Tier,Status')

    def test_export_default_filter_is_subscribers(self):
        """No filter query param -> only subscriber rows (matches the page)."""
        response = self.client.get('/studio/users/export')
        content = response.content.decode()
        self.assertIn('alice@test.com', content)
        self.assertNotIn('bob@test.com', content)
        self.assertNotIn('staff@test.com', content)

    def test_export_filter_all(self):
        response = self.client.get('/studio/users/export?filter=all')
        content = response.content.decode()
        self.assertIn('alice@test.com', content)
        self.assertIn('bob@test.com', content)
        self.assertIn('staff@test.com', content)

    def test_export_filter_staff(self):
        response = self.client.get('/studio/users/export?filter=staff')
        content = response.content.decode()
        self.assertIn('staff@test.com', content)
        self.assertNotIn('alice@test.com', content)
        self.assertNotIn('bob@test.com', content)

    def test_export_filter_non_subscribers(self):
        response = self.client.get('/studio/users/export?filter=non_subscribers')
        content = response.content.decode()
        self.assertNotIn('alice@test.com', content)
        self.assertIn('bob@test.com', content)

    def test_export_honours_search(self):
        response = self.client.get('/studio/users/export?filter=all&q=alice')
        content = response.content.decode()
        self.assertIn('alice@test.com', content)
        self.assertNotIn('bob@test.com', content)
        self.assertNotIn('staff@test.com', content)

    def test_export_subscribed_column_values(self):
        response = self.client.get('/studio/users/export?filter=all')
        # Find each user's row and verify the Subscribed column.
        # Row layout: email,joined,Yes/No,tier,status
        lines = response.content.decode().splitlines()
        alice_line = next(line for line in lines if line.startswith('alice@test.com'))
        bob_line = next(line for line in lines if line.startswith('bob@test.com'))
        # Subscribed is the 3rd CSV cell.
        self.assertEqual(alice_line.split(',')[2], 'Yes')
        self.assertEqual(bob_line.split(',')[2], 'No')

    def test_export_non_staff_forbidden(self):
        User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.logout()
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.get('/studio/users/export')
        self.assertEqual(response.status_code, 403)


class SubscriberRedirectShimTest(TestCase):
    """The old /studio/subscribers/ URLs 301-redirect to the new ones."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_subscriber_list_redirects_permanently_to_users(self):
        response = self.client.get('/studio/subscribers/')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/studio/users/')

    def test_subscriber_export_redirects_permanently_to_users_export(self):
        response = self.client.get('/studio/subscribers/export')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/studio/users/export')

    def test_redirect_followed_lands_on_user_list(self):
        response = self.client.get('/studio/subscribers/', follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/users/list.html')
