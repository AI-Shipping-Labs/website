"""Studio list for dated-cohort and sprint study groups."""

from django.db.models import Count, Min, Q
from django.shortcuts import render
from django.utils import timezone

from pods.models import MEETING_STATUS_HELD, MEETING_STATUS_SCHEDULED, POD_STATUS_CHOICES, Pod
from pods.services.stuck_proposals import live_proposal_counts, pods_with_stuck_proposal
from studio.decorators import staff_required
from studio.utils import studio_pagination_context
from studio.views.pod_shared import cohort_label, dated_cohorts, sprints


def _filtered_pods(filters, now):
    search, status_filter, cohort_filter, sprint_filter = filters
    pods = (
        Pod.objects.all()
        .select_related("cohort__course", "sprint", "owner")
        .annotate(
            member_total=Count("memberships", distinct=True),
            held_total=Count("meetings", filter=Q(meetings__status=MEETING_STATUS_HELD), distinct=True),
            next_meeting_at=Min(
                "meetings__starts_at",
                filter=Q(meetings__status=MEETING_STATUS_SCHEDULED, meetings__starts_at__gt=now),
            ),
        )
        .order_by("-created_at", "-pk")
    )
    if search:
        pods = pods.filter(name__icontains=search)
    if status_filter in {value for value, _label in POD_STATUS_CHOICES}:
        pods = pods.filter(status=status_filter)
    if cohort_filter.isdigit():
        pods = pods.filter(cohort_id=int(cohort_filter))
    if sprint_filter.isdigit():
        pods = pods.filter(sprint_id=int(sprint_filter))
    return pods


def _pod_rows(page, now):
    pod_ids = [pod.pk for pod in page.object_list]
    stuck_ids = pods_with_stuck_proposal(pod_ids, now=now)
    proposal_counts = live_proposal_counts(pod_ids, now=now)
    return [
        {
            "pod": pod,
            "stuck": pod.pk in stuck_ids,
            "proposals_waiting": proposal_counts.get(pod.pk, 0),
        }
        for pod in page.object_list
    ]


def _extra_filters(cohort_filter, sprint_filter):
    return [
        {
            "name": "cohort",
            "label": "Cohort",
            "all_label": "All cohorts",
            "value": cohort_filter,
            "options": [(str(cohort.pk), cohort_label(cohort)) for cohort in dated_cohorts()],
        },
        {
            "name": "sprint",
            "label": "Sprint",
            "all_label": "All sprints",
            "value": sprint_filter,
            "options": [(str(sprint.pk), sprint.name) for sprint in sprints()],
        },
    ]


@staff_required
def studio_pod_list(request):
    filters = tuple((request.GET.get(key) or "").strip() for key in ("q", "status", "cohort", "sprint"))
    search, status_filter, cohort_filter, sprint_filter = filters
    now = timezone.now()
    pager = studio_pagination_context(request, _filtered_pods(filters, now))
    context = {
        "rows": _pod_rows(pager["page"], now),
        "search": search,
        "status_filter": status_filter,
        "cohort_filter": cohort_filter,
        "sprint_filter": sprint_filter,
        "filters_active": bool(any(filters)),
        "extra_filters": _extra_filters(cohort_filter, sprint_filter),
        **pager,
    }
    return render(request, "studio/pods/list.html", context)
