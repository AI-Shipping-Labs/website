from django.contrib import admin

from payments.models import CheckoutAccountBinding, CheckoutFulfillment


@admin.register(CheckoutAccountBinding)
class CheckoutAccountBindingAdmin(admin.ModelAdmin):
    list_display = [
        "id", "user", "tier", "billing_period", "source", "purpose",
        "expires_at", "created_at",
    ]
    list_filter = ["billing_period", "tier", "source", "purpose"]
    search_fields = ["user__email"]
    readonly_fields = [
        "token_hash", "user", "tier", "billing_period", "email_snapshot",
        "source", "purpose", "created_at", "expires_at", "revoked_at",
    ]


@admin.register(CheckoutFulfillment)
class CheckoutFulfillmentAdmin(admin.ModelAdmin):
    list_display = ["stripe_session_id", "status", "user", "tier", "created_at"]
    list_filter = ["status", "reason", "tier"]
    search_fields = [
        "stripe_session_id", "stripe_customer_id", "stripe_subscription_id",
        "user__email",
    ]
    readonly_fields = [
        "stripe_session_id", "binding", "user", "tier", "stripe_customer_id",
        "stripe_subscription_id", "status", "reason", "details", "created_at",
        "updated_at",
    ]
