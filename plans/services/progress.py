"""Shared sprint-plan progress annotations."""

from django.db.models import Count, Q


def annotate_plan_progress(queryset):
    """Annotate a Plan queryset with checkpoint progress counts.

    ``progress_total`` counts distinct checkpoints across all weeks on
    the plan. ``progress_done`` counts that same distinct checkpoint set
    filtered to checkpoints with ``done_at`` set.
    """
    return queryset.annotate(
        progress_total=Count(
            'weeks__checkpoints',
            filter=~Q(weeks__checkpoints__description__regex=r'^\s*$'),
            distinct=True,
        ),
        progress_done=Count(
            'weeks__checkpoints',
            filter=(
                Q(weeks__checkpoints__done_at__isnull=False)
                & ~Q(weeks__checkpoints__description__regex=r'^\s*$')
            ),
            distinct=True,
        ),
    )
