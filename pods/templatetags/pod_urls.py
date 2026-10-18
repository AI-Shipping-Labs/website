"""``{% pod_action_url %}``: per-pod member URLs for either activity (issue #1921).

Pod templates serve cohort pods (``/courses/<slug>/home/pods/...``) and
sprint pods (``/sprints/<slug>/pods/...``). Instead of reversing a course
route, they name the course route and let the pod's activity build the
right URL:

    {% load pod_urls %}
    {% pod_action_url 'pod_join' pod %}
    {% pod_action_url 'pod_meeting_cancel' pod meeting_id=row.meeting.pk %}
"""

from django import template

from pods.services.activity import activity_for_pod

register = template.Library()


@register.simple_tag
def pod_action_url(name, pod, **kwargs):
    return activity_for_pod(pod).action_url(pod, name, **kwargs)
