"""Who may use an event's join link.

One rule for the ``event_join_redirect`` gate and the course session unit:
the event's registrants plus members of dated cohorts linked to the event's
series. That is the pre-event reminder audience, so everyone reminded about
a session can use the join link the reminder leads to.
"""

from events.models import EventRegistration
from events.services.event_audience import is_series_cohort_member


def user_can_join_event(user, event):
    """Return whether ``user`` may join ``event`` once its join window opens."""
    if not getattr(user, "is_authenticated", False):
        return False
    if EventRegistration.objects.filter(event=event, user=user).exists():
        return True
    return is_series_cohort_member(event, user.pk)
