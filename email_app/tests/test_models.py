from django.test import TestCase

from email_app.models import NewsletterSubscriber


class NewsletterSubscriberModelTest(TestCase):
    def setUp(self):
        self.subscriber = NewsletterSubscriber.objects.create(
            email='test@example.com',
        )

    def test_ordering(self):
        NewsletterSubscriber.objects.create(email='second@example.com')
        subs = list(NewsletterSubscriber.objects.all())
        self.assertEqual(subs[0].email, 'second@example.com')
