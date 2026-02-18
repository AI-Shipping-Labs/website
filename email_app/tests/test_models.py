from django.test import TestCase
from email_app.models import NewsletterSubscriber


class NewsletterSubscriberModelTest(TestCase):
    def setUp(self):
        self.subscriber = NewsletterSubscriber.objects.create(
            email='test@example.com',
        )

    def test_str(self):
        self.assertEqual(str(self.subscriber), 'test@example.com')

    def test_unique_email(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            NewsletterSubscriber.objects.create(email='test@example.com')

    def test_default_active(self):
        self.assertTrue(self.subscriber.is_active)

    def test_ordering(self):
        NewsletterSubscriber.objects.create(email='second@example.com')
        subs = list(NewsletterSubscriber.objects.all())
        self.assertEqual(subs[0].email, 'second@example.com')
