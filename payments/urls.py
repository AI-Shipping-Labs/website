from django.urls import path
from django.views.generic import RedirectView

from payments.views.pricing import create_checkout_binding
from payments.views.webhooks import stripe_webhook

urlpatterns = [
    path(
        "pricing",
        RedirectView.as_view(pattern_name="pricing", permanent=True, query_string=True),
        name="legacy_pricing",
    ),
    path(
        "payments/checkout/<slug:tier_slug>/<str:billing_period>",
        create_checkout_binding,
        name="checkout_binding_create",
    ),
    path("api/webhooks/payments", stripe_webhook, name="stripe_webhook"),
]
