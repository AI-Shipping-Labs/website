from django.contrib import admin

from payments.models import Tier


@admin.register(Tier)
class TierAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "level", "price_eur_month", "price_eur_year"]
    # yaml-managed columns are read-only in admin. Edit `tiers.yaml` in the
    # AI-Shipping-Labs/content repo and re-sync; admin edits to these fields
    # would be silently overwritten on the next content sync.
    readonly_fields = [
        "name",
        "level",
        "price_eur_month",
        "price_eur_year",
        "description",
        "stripe_price_id_monthly",
        "stripe_price_id_yearly",
    ]
    list_editable = []
    ordering = ["level"]
    # `prepopulated_fields = {"slug": ("name",)}` was removed because
    # `name` is now in `readonly_fields` (yaml-managed), and Django's admin
    # raises `KeyError: "Key 'name' not found in 'TierForm'"` when a
    # prepopulated source field is also readonly. Slug stays editable for
    # the rare add path.

    def get_fieldsets(self, request, obj=None):
        # Surface a short banner so operators discover the yaml-managed
        # conflict resolution without having to read code or docs.
        return (
            (
                None,
                {
                    "description": (
                        "name, level, prices, description, and Stripe price "
                        "IDs are managed by tiers.yaml in the content repo "
                        "(AI-Shipping-Labs/content). Edit them there and "
                        "re-sync; changes made here will be overwritten on "
                        "the next sync. slug and features are still managed "
                        "in admin."
                    ),
                    "fields": (
                        "slug",
                        "name",
                        "level",
                        "price_eur_month",
                        "price_eur_year",
                        "description",
                        "stripe_price_id_monthly",
                        "stripe_price_id_yearly",
                        "features",
                    ),
                },
            ),
        )
