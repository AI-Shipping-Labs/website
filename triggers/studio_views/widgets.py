"""Studio screens for event widgets (issue #1070)."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from studio.decorators import staff_required
from triggers.forms import EventWidgetForm
from triggers.models import EventWidget


@staff_required
def widget_list(request):
    widgets = EventWidget.objects.all()
    return render(
        request,
        "studio/triggers/widget_list.html",
        {"widgets": widgets},
    )


@staff_required
def widget_create(request):
    if request.method == "POST":
        form = EventWidgetForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Widget created.")
            return redirect("studio_trigger_widget_list")
    else:
        form = EventWidgetForm()
    return render(
        request,
        "studio/triggers/widget_form.html",
        {"form": form, "is_create": True},
    )


@staff_required
def widget_edit(request, widget_id):
    widget = get_object_or_404(EventWidget, pk=widget_id)
    if request.method == "POST":
        form = EventWidgetForm(request.POST, instance=widget)
        if form.is_valid():
            form.save()
            messages.success(request, "Widget updated.")
            return redirect("studio_trigger_widget_list")
    else:
        form = EventWidgetForm(instance=widget)
    return render(
        request,
        "studio/triggers/widget_form.html",
        {"form": form, "is_create": False, "widget": widget},
    )


@staff_required
@require_POST
def widget_toggle(request, widget_id):
    """Activate/deactivate a widget (no DELETE — deactivate instead)."""
    widget = get_object_or_404(EventWidget, pk=widget_id)
    widget.is_active = not widget.is_active
    widget.save(update_fields=["is_active", "updated_at"])
    state = "activated" if widget.is_active else "deactivated"
    messages.success(request, f"Widget {state}.")
    return redirect("studio_trigger_widget_list")
