from django.urls import path

from payments.views.pricing import pricing
from payments.views.webhooks import stripe_webhook

urlpatterns = [
    path("pricing", pricing, name="pricing"),
    path("api/webhooks/payments", stripe_webhook, name="stripe_webhook"),
]
