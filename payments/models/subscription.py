from django.db import models


class StripePaymentLink(models.Model):
    """Stripe payment link for a tier."""
    tier_name = models.CharField(max_length=50)
    billing_period = models.CharField(max_length=20, choices=[('monthly', 'Monthly'), ('annual', 'Annual')])
    url = models.URLField(max_length=500)

    class Meta:
        unique_together = ['tier_name', 'billing_period']
        ordering = ['tier_name', 'billing_period']

    def __str__(self):
        return f'{self.tier_name} - {self.billing_period}'
