from django.urls import path

from payments.views.pricing import pricing

urlpatterns = [
    path("pricing", pricing, name="pricing"),
]
