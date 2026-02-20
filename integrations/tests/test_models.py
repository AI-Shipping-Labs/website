from django.test import TestCase
from integrations.models import WebhookLog


class WebhookLogModelTest(TestCase):
    def setUp(self):
        self.log = WebhookLog.objects.create(
            service='stripe',
            event_type='payment.succeeded',
            payload={'amount': 500},
            processed=False,
        )

    def test_str(self):
        self.assertIn('stripe', str(self.log))
        self.assertIn('payment.succeeded', str(self.log))

    def test_default_values(self):
        log = WebhookLog.objects.create(service='slack')
        self.assertEqual(log.event_type, '')
        self.assertEqual(log.payload, {})
        self.assertFalse(log.processed)

    def test_ordering(self):
        WebhookLog.objects.create(service='zoom')
        logs = list(WebhookLog.objects.all())
        # Most recent first
        self.assertEqual(logs[0].service, 'zoom')
