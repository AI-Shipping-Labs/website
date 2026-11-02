"""Scheduled campaign dispatch task.

Fires every minute via the ``campaign-scheduled-dispatch`` recurring
schedule and claims every draft campaign whose ``scheduled_at`` has
arrived. The claim path is shared with Studio's manual send, so dispatch is
exactly-once and clears the schedule fields.
"""

from email_app.services.campaign_scheduling import dispatch_due_campaigns


def dispatch_due_scheduled_campaigns(now=None):
    """Send every scheduled campaign whose time has come."""
    return dispatch_due_campaigns(now=now)
