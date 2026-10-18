"""Shared form and activity helpers for Studio study-group views."""

from content.models import Cohort
from plans.models import Sprint
from pods.services import config as pods_config
from pods.services.activity import cohort_activity, sprint_activity

RESULTS_SESSION_KEY = "studio_pod_add_results"
RESULT_BUCKET_LABELS = (
    ("added", "Added"),
    ("not_enrolled", "Not enrolled"),
    ("unknown_user", "Unknown user"),
    ("already_member", "Already a member"),
)
ACTIVITY_COHORT_PREFIX = "cohort:"
ACTIVITY_SPRINT_PREFIX = "sprint:"


def cohort_label(cohort):
    return f"{cohort.course.title} - {cohort.name}"


def sprints():
    return Sprint.objects.order_by("-start_date", "name", "pk")


def dated_cohorts(active_only=False):
    cohorts = (
        Cohort.objects.filter(mode="cohort")
        .select_related("course")
        .order_by(
            "course__title",
            "start_date",
            "pk",
        )
    )
    if active_only:
        cohorts = cohorts.filter(is_active=True)
    return cohorts


def store_results(request, pod, results):
    request.session[RESULTS_SESSION_KEY] = {"pod_id": pod.pk, "results": results.as_dict()}


def pop_results(request, pod):
    stored = request.session.pop(RESULTS_SESSION_KEY, None)
    if not stored or stored.get("pod_id") != pod.pk:
        return []
    rows = []
    for key, label in RESULT_BUCKET_LABELS:
        emails = stored["results"].get(key)
        if emails:
            rows.append({"key": key, "label": label, "emails": emails})
    return rows


def form_defaults():
    return {
        "activity": "",
        "name": "",
        "purpose": "",
        "scheduling_note": "",
        "internal_notes": "",
        "meeting_count": str(pods_config.default_meeting_count()),
        "meeting_minutes": str(pods_config.default_meeting_minutes()),
        "owner_email": "",
        "member_emails": "",
    }


def activity_value(data):
    value = (data.get("activity") or "").strip()
    if value:
        return value
    for key, prefix in (("sprint", ACTIVITY_SPRINT_PREFIX), ("cohort", ACTIVITY_COHORT_PREFIX)):
        raw = (data.get(key) or "").strip()
        if raw.isdigit():
            return f"{prefix}{raw}"
    return ""


def resolve_activity(value):
    kind, _separator, raw_id = value.partition(":")
    if not raw_id.isdigit():
        return None
    if kind == "cohort":
        cohort = Cohort.objects.select_related("course").filter(pk=int(raw_id)).first()
        if cohort is not None:
            return cohort_activity(cohort)
    if kind == "sprint":
        sprint = Sprint.objects.filter(pk=int(raw_id)).first()
        if sprint is not None:
            return sprint_activity(sprint)
    return None
