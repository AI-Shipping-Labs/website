"""Central access policy for the sprint cohort board."""

from dataclasses import dataclass

from plans.models import SprintEnrollment


@dataclass(frozen=True)
class CohortBoardAccess:
    """Resolved board mode for one authenticated viewer."""

    allowed: bool
    staff_operator: bool


def resolve_cohort_board_access(*, sprint, viewer):
    """Allow enrolled members and authenticated staff operators.

    Enrollment remains the sole member-membership authority. Staff access is
    a separate, explicitly read-only operator mode rather than synthetic
    enrollment.
    """
    if not getattr(viewer, 'is_authenticated', False):
        return CohortBoardAccess(allowed=False, staff_operator=False)
    if getattr(viewer, 'is_staff', False):
        return CohortBoardAccess(allowed=True, staff_operator=True)
    enrolled = SprintEnrollment.objects.filter(
        sprint=sprint,
        user=viewer,
    ).exists()
    return CohortBoardAccess(allowed=enrolled, staff_operator=False)
