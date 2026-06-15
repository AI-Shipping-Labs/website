"""Template tag for rendering a sprint's start--end (duration) range (#978).

The same start/end/duration string is shown on six member-facing surfaces
(sprint detail, the ``/sprints`` and ``/activities`` cards, the cohort
board, the member's plan, and the dashboard). Centralising the same-year /
cross-year logic here keeps that wording consistent everywhere instead of
duplicating the conditional in each template.

Sprint dates are calendar ``DateField`` values, so this formats them with
plain ``date`` formatting and never converts to local time -- they must not
shift by timezone.
"""

from django import template

register = template.Library()


def _format_range(start, end, weeks, compact):
    """Build the ``Start - End (N weeks)`` string for one sprint.

    Same calendar year shows the year once at the end
    (``June 17 - July 29, 2026``); a cross-year range shows the full year on
    both sides (``December 16, 2025 - January 27, 2026``). ``compact`` drops
    the year entirely and uses abbreviated months for the tight dashboard
    list (``Jun 17 - Jul 29``).
    """
    if start is None:
        return ''

    week_word = 'week' if weeks == 1 else 'weeks'
    duration = f'({weeks} {week_word})' if weeks is not None else ''

    if end is None:
        # No duration -> fall back to just the start date.
        start_str = start.strftime('%b %-d') if compact else start.strftime('%B %-d, %Y')
        return start_str

    if compact:
        start_str = start.strftime('%b %-d')
        end_str = end.strftime('%b %-d')
    elif start.year == end.year:
        start_str = start.strftime('%B %-d')
        end_str = end.strftime('%B %-d, %Y')
    else:
        start_str = start.strftime('%B %-d, %Y')
        end_str = end.strftime('%B %-d, %Y')

    parts = f'{start_str} – {end_str}'
    if duration:
        parts = f'{parts} {duration}'
    return parts


@register.simple_tag
def sprint_date_range(sprint, compact=False):
    """Return the formatted start--end (duration) string for ``sprint``.

    Pass ``compact=True`` for the abbreviated, no-year form used in the
    dashboard active-sprint list.
    """
    if sprint is None:
        return ''
    return _format_range(
        sprint.start_date, sprint.end_date, sprint.duration_weeks, compact
    )
