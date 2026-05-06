"""Template tag for the header "Plan" nav link (issue #440).

The header shows a ``Plan`` link only for authenticated members who
have at least one :class:`plans.models.Plan` row. The link points to
the most recently created plan's ``my_plan_detail`` view. Members with
zero plans see no link (per AC: "Authenticated user with no plans does
NOT see the Plan link").
"""

from django import template

from plans.models import Plan

register = template.Library()


@register.simple_tag(takes_context=True)
def current_user_latest_plan(context):
    """Return the most recently created :class:`Plan` for the current user.

    Returns ``None`` for anonymous users or authenticated users who
    have no plan rows yet.
    """
    request = context.get('request')
    if request is None:
        return None
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return None
    return (
        Plan.objects.filter(member=user)
        .order_by('-created_at')
        .first()
    )
