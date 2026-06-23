"""Studio routes for the triggers subsystem (issue #1070).

Mounted at ``/studio/triggers/`` from ``website/urls.py``.
"""

from django.urls import path

from triggers.studio_views import (
    delivery_list,
    emission_list,
    subscription_create,
    subscription_edit,
    subscription_list,
    subscription_toggle,
    widget_create,
    widget_edit,
    widget_list,
    widget_toggle,
)

urlpatterns = [
    path(
        "subscriptions/",
        subscription_list,
        name="studio_trigger_subscription_list",
    ),
    path(
        "subscriptions/new/",
        subscription_create,
        name="studio_trigger_subscription_create",
    ),
    path(
        "subscriptions/<int:subscription_id>/edit/",
        subscription_edit,
        name="studio_trigger_subscription_edit",
    ),
    path(
        "subscriptions/<int:subscription_id>/toggle/",
        subscription_toggle,
        name="studio_trigger_subscription_toggle",
    ),
    path("widgets/", widget_list, name="studio_trigger_widget_list"),
    path("widgets/new/", widget_create, name="studio_trigger_widget_create"),
    path(
        "widgets/<int:widget_id>/edit/",
        widget_edit,
        name="studio_trigger_widget_edit",
    ),
    path(
        "widgets/<int:widget_id>/toggle/",
        widget_toggle,
        name="studio_trigger_widget_toggle",
    ),
    path("emissions/", emission_list, name="studio_trigger_emission_list"),
    path("deliveries/", delivery_list, name="studio_trigger_delivery_list"),
]
