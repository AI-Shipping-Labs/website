from django.test import TestCase
from payments.models import StripePaymentLink


class StripePaymentLinkModelTest(TestCase):
    def setUp(self):
        self.link = StripePaymentLink.objects.create(
            tier_name='basic',
            billing_period='monthly',
            url='https://buy.stripe.com/test123',
        )

    def test_str(self):
        self.assertEqual(str(self.link), 'basic - monthly')

    def test_unique_together(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            StripePaymentLink.objects.create(
                tier_name='basic',
                billing_period='monthly',
                url='https://buy.stripe.com/test456',
            )

    def test_different_period_allowed(self):
        link = StripePaymentLink.objects.create(
            tier_name='basic',
            billing_period='annual',
            url='https://buy.stripe.com/test789',
        )
        self.assertEqual(StripePaymentLink.objects.count(), 2)
