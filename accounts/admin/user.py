from django.contrib import admin
from accounts.models import Tier


@admin.register(Tier)
class TierAdmin(admin.ModelAdmin):
    list_display = ['name', 'price_monthly', 'price_annual', 'highlighted', 'sort_order']
    list_filter = ['highlighted']
