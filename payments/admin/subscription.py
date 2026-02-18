from django.contrib import admin
from payments.models import StripePaymentLink


@admin.register(StripePaymentLink)
class StripePaymentLinkAdmin(admin.ModelAdmin):
    list_display = ['tier_name', 'billing_period', 'url']
