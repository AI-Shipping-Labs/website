"""Read-only Studio log of webhook deliveries (issue #1070).

Filterable by subscription and by success/failure so an operator can spot
failing handlers fast.
"""

from django.shortcuts import render

from studio.decorators import staff_required
from triggers.models import TriggerSubscription, WebhookDelivery, WebhookDeliveryJob


@staff_required
def delivery_list(request):
    deliveries = WebhookDelivery.objects.select_related(
        "subscription", "emission",
    )

    subscription_id = request.GET.get("subscription")
    if subscription_id:
        deliveries = deliveries.filter(subscription_id=subscription_id)

    succeeded = request.GET.get("succeeded")
    jobs = WebhookDeliveryJob.objects.select_related("subscription", "emission")
    if subscription_id:
        jobs = jobs.filter(subscription_id=subscription_id)
    if succeeded == "true":
        deliveries = deliveries.filter(succeeded=True)
        jobs = jobs.filter(status=WebhookDeliveryJob.STATUS_SUCCEEDED)
    elif succeeded == "false":
        deliveries = deliveries.filter(succeeded=False)
        jobs = jobs.exclude(status=WebhookDeliveryJob.STATUS_SUCCEEDED)

    return render(
        request,
        "studio/triggers/delivery_list.html",
        {
            "deliveries": deliveries[:200],
            "delivery_jobs": jobs[:200],
            "subscriptions": TriggerSubscription.objects.all(),
            "selected_subscription": subscription_id or "",
            "selected_succeeded": succeeded or "",
        },
    )
