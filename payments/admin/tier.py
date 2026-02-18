from django.contrib import admin

from payments.models import Tier


@admin.register(Tier)
class TierAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "level", "price_eur_month", "price_eur_year"]
    list_editable = ["price_eur_month", "price_eur_year"]
    ordering = ["level"]
    prepopulated_fields = {"slug": ("name",)}
