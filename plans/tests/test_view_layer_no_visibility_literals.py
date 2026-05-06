"""Regression test: visibility enforcement lives at the queryset, not in views.

If a view in ``plans/views/cohort.py`` ever inlines an
``is_staff`` check or a ``plan.visibility == 'cohort'`` comparison, it
re-introduces the bug class this issue is designed to prevent. The
single source of truth for those rules is :class:`PlanQuerySet` on
:class:`plans.models.Plan`. Adding any of the forbidden patterns to
the view module fails this test on purpose.
"""

import pathlib

from django.test import SimpleTestCase

VIEW_FILE = (
    pathlib.Path(__file__).resolve().parent.parent / 'views' / 'cohort.py'
)


def _read_view_source():
    return VIEW_FILE.read_text()


class ViewLayerHasNoVisibilityLiteralsTest(SimpleTestCase):
    def test_views_do_not_check_is_staff_directly(self):
        """The string ``is_staff`` MUST NOT appear in the view module.

        ``is_staff`` is a one-shot bypass that, if added in a view body,
        would let staff who are not enrolled in a sprint see the cohort
        board. Staff use Studio for full access; the cohort board is
        member-only. The queryset enforces this; the view file must
        not contradict it.
        """
        source = _read_view_source()
        self.assertNotIn(
            'is_staff', source,
            'plans/views/cohort.py contains the literal "is_staff" -- '
            'gating must live at the queryset, not in the view body.',
        )

    def test_views_do_not_check_visibility_literal_cohort(self):
        """No ``visibility == 'cohort'`` comparison in the view module.

        The cohort filter is :meth:`Plan.objects.visible_on_cohort_board`.
        A literal comparison in a view is a sign that someone re-derived
        the rule and bypassed the chokepoint -- forbid it here.
        """
        source = _read_view_source()
        forbidden_literals = [
            "visibility == 'cohort'",
            'visibility == "cohort"',
            "visibility=='cohort'",
            'visibility=="cohort"',
            ".filter(visibility='cohort')",
            '.filter(visibility="cohort")',
        ]
        for literal in forbidden_literals:
            self.assertNotIn(
                literal, source,
                f'plans/views/cohort.py contains the forbidden literal '
                f'{literal!r} -- visibility filtering belongs in '
                f'PlanQuerySet, not in the view body.',
            )

    def test_views_do_not_import_interviewnote(self):
        """``InterviewNote`` must not be imported in the cohort view module.

        Internal interview notes are the most security-sensitive table
        in this app; member-facing views in this issue do not surface
        them. Removing the import altogether makes the rule mechanical
        and reviewable.
        """
        source = _read_view_source()
        # Catch both ``from plans.models import InterviewNote`` and
        # ``from plans.models import (..., InterviewNote, ...)`` styles.
        self.assertNotIn(
            'InterviewNote', source,
            'plans/views/cohort.py imports or references InterviewNote -- '
            'this issue does not surface interview notes on member-facing '
            'pages, regardless of plan visibility.',
        )
