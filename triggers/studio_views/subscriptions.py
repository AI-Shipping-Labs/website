"""Studio screens for trigger subscriptions (issue #1070)."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from studio.decorators import staff_required
from triggers.forms import TriggerSubscriptionForm
from triggers.models import TriggerSubscription


@staff_required
def subscription_list(request):
    subscriptions = TriggerSubscription.objects.all()
    return render(
        request,
        "studio/triggers/subscription_list.html",
        {"subscriptions": subscriptions},
    )


@staff_required
def subscription_create(request):
    if request.method == "POST":
        form = TriggerSubscriptionForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Subscription created.")
            return redirect("studio_trigger_subscription_list")
    else:
        form = TriggerSubscriptionForm()
    return render(
        request,
        "studio/triggers/subscription_form.html",
        {"form": form, "is_create": True},
    )


@staff_required
def subscription_edit(request, subscription_id):
    subscription = get_object_or_404(TriggerSubscription, pk=subscription_id)
    if request.method == "POST":
        form = TriggerSubscriptionForm(request.POST, instance=subscription)
        if form.is_valid():
            form.save()
            messages.success(request, "Subscription updated.")
            return redirect("studio_trigger_subscription_list")
    else:
        form = TriggerSubscriptionForm(instance=subscription)
    return render(
        request,
        "studio/triggers/subscription_form.html",
        {"form": form, "is_create": False, "subscription": subscription},
    )


@staff_required
@require_POST
def subscription_toggle(request, subscription_id):
    """Activate/deactivate a subscription (no DELETE — deactivate instead)."""
    subscription = get_object_or_404(TriggerSubscription, pk=subscription_id)
    subscription.is_active = not subscription.is_active
    subscription.save(update_fields=["is_active", "updated_at"])
    state = "activated" if subscription.is_active else "deactivated"
    messages.success(request, f"Subscription {state}.")
    return redirect("studio_trigger_subscription_list")
