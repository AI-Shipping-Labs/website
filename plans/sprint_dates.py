"""Sprint date-window predicates shared by the dashboard and plan nav."""


def created_timestamp(obj):
    value = getattr(obj, 'created_at', None)
    if value is None:
        return 0
    return value.timestamp()


def is_current_sprint(sprint, today):
    return sprint.start_date <= today <= sprint.end_date


def is_upcoming_sprint(sprint, today):
    return today < sprint.start_date
