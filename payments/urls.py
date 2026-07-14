from django.urls import path

from payments.views.pricing import create_checkout_binding, pricing
from payments.views.webhooks import stripe_webhook

urlpatterns = [
    path("pricing", pricing, name="pricing"),
    path(
        "payments/checkout/<slug:tier_slug>/<str:billing_period>",
        create_checkout_binding,
        name="checkout_binding_create",
    ),
    path("api/webhooks/payments", stripe_webhook, name="stripe_webhook"),
]
