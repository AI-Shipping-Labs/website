"""Studio study-group creation form."""

from django.contrib import messages
from django.shortcuts import redirect, render

from pods.models import MEETING_MINUTES_CHOICES, POD_SOURCE_STUDIO
from pods.services import membership as svc
from studio.decorators import staff_required
from studio.views.pod_shared import (
    ACTIVITY_COHORT_PREFIX,
    ACTIVITY_SPRINT_PREFIX,
    activity_value,
    cohort_label,
    dated_cohorts,
    form_defaults,
    resolve_activity,
    sprints,
    store_results,
)


def _create(request, form):
    activity = resolve_activity(form["activity"])
    if activity is None:
        return None, "Choose a cohort or a sprint."
    try:
        pod, results, created = svc.create_staff_pod(
            activity=activity,
            data=form,
            owner_email=form["owner_email"],
            member_emails=form["member_emails"],
            actor=request.user,
            source=POD_SOURCE_STUDIO,
        )
    except svc.PodError as exc:
        return None, exc.message
    store_results(request, pod, results)
    if created:
        messages.success(request, f'Pod "{pod.name}" created.')
    else:
        messages.info(
            request,
            f'Pod "{pod.name}" already exists in this {activity.noun}; new emails were added to it.',
        )
    return pod, ""


def _activity_options():
    cohort_options = [
        (f"{ACTIVITY_COHORT_PREFIX}{cohort.pk}", cohort_label(cohort)) for cohort in dated_cohorts(active_only=True)
    ]
    sprint_options = [
        (f"{ACTIVITY_SPRINT_PREFIX}{sprint.pk}", sprint.name) for sprint in sprints().exclude(status="cancelled")
    ]
    return cohort_options, sprint_options


@staff_required
def studio_pod_create(request):
    form = form_defaults()
    form["activity"] = activity_value(request.GET)
    error = ""
    if request.method == "POST":
        form = {key: request.POST.get(key, "") for key in form}
        form["activity"] = activity_value(request.POST)
        pod, error = _create(request, form)
        if pod is not None:
            return redirect("studio_pod_detail", pod_id=pod.pk)
    cohort_options, sprint_options = _activity_options()
    status = 200
    if error:
        status = 400
    return render(
        request,
        "studio/pods/new.html",
        {
            "form": form,
            "error": error,
            "cohort_options": cohort_options,
            "sprint_options": sprint_options,
            "meeting_minute_choices": MEETING_MINUTES_CHOICES,
        },
        status=status,
    )
