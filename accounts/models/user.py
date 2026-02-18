from django.db import models
from django.contrib.auth.models import AbstractUser


class Tier(models.Model):
    """Membership tier."""
    TIER_CHOICES = [
        ('basic', 'Basic'),
        ('main', 'Main'),
        ('premium', 'Premium'),
    ]

    name = models.CharField(max_length=50, choices=TIER_CHOICES, unique=True)
    tagline = models.CharField(max_length=200, blank=True, default='')
    description = models.TextField(blank=True, default='')
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price_annual = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    hook = models.TextField(blank=True, default='')
    positioning = models.TextField(blank=True, default='')
    features = models.JSONField(default=list, blank=True)
    highlighted = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['sort_order']

    def __str__(self):
        return self.get_name_display()
