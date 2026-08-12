"""Read-only Studio log of webhook deliveries (issue #1070).

Filterable by subscription and by success/failure so an operator can spot
failing handlers fast.
"""

from django.shortcuts import render

from studio.decorators import staff_required
from studio.utils import studio_pagination_context
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

    job_pager = studio_pagination_context(
        request,
        jobs,
        page_param="jobs_page",
    )
    attempt_pager = studio_pagination_context(
        request,
        deliveries,
        page_param="attempts_page",
    )

    return render(
        request,
        "studio/triggers/delivery_list.html",
        {
            "deliveries": attempt_pager["page"].object_list,
            "delivery_jobs": job_pager["page"].object_list,
            "subscriptions": TriggerSubscription.objects.all(),
            "selected_subscription": subscription_id or "",
            "selected_succeeded": succeeded or "",
            **{
                f"delivery_jobs_{key}": value
                for key, value in job_pager.items()
            },
            **{
                f"delivery_attempts_{key}": value
                for key, value in attempt_pager.items()
            },
        },
    )
