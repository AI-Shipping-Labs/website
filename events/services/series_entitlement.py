"""Entitlement gate for a hidden ``EventSeries`` (issue #1660).

A hidden series' occurrences never appear on any discovery surface (see
``events.services.time_windows.public_events_queryset`` and friends), but
its detail/recap/series pages must stay reachable to staff and to members
entitled through a linked ``Cohort`` (paid course cohorts) or ``Sprint``
(sprint calls). Mirrors ``plans.cohort_access.resolve_cohort_board_access``:
staff bypass, enrollment is the sole membership authority.
"""


def is_entitled_for_series(user, series):
    """Return whether ``user`` may reach a hidden series' gated pages.

    ``series`` may be ``None`` (an occurrence with no linked series never
    reaches this gate; callers only call it when a series is present).
    Staff are always entitled. Otherwise entitlement is exactly:
    a ``CohortEnrollment`` for a ``Cohort`` whose ``event_series`` is this
    series, or a ``SprintEnrollment`` for a ``Sprint`` whose
    ``event_series`` is this series.

    Lazy imports of ``content.models.CohortEnrollment`` and
    ``plans.models.SprintEnrollment`` mirror the pattern already used in
    ``content.services.related_content`` — ``content`` and ``plans`` both
    import ``events`` models at module level, so importing them back at
    module level here would create an import cycle.
    """
    if series is None:
        return False
    if not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_staff', False):
        return True

    from content.models import CohortEnrollment
    from plans.models import SprintEnrollment

    if CohortEnrollment.objects.filter(
        user=user, cohort__event_series=series,
    ).exists():
        return True
    return SprintEnrollment.objects.filter(
        user=user, sprint__event_series=series,
    ).exists()
