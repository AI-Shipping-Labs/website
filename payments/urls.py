from django.urls import path

from payments.views.checkout import cancel, create_checkout, downgrade, upgrade
from payments.views.pricing import pricing
from payments.views.webhooks import stripe_webhook

urlpatterns = [
    path("pricing", pricing, name="pricing"),
    path("api/checkout/create", create_checkout, name="checkout_create"),
    path("api/subscription/upgrade", upgrade, name="subscription_upgrade"),
    path("api/subscription/downgrade", downgrade, name="subscription_downgrade"),
    path("api/subscription/cancel", cancel, name="subscription_cancel"),
    path("api/webhooks/payments", stripe_webhook, name="stripe_webhook"),
]
