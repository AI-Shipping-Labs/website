"""Tests for ``Sprint.get_studio_edit_url`` (issue #667).

The floating "Edit in Studio" button on the public sprint detail and
cohort board pages uses this method to point staff at the Sprint editor.
"""

import datetime

from django.test import TestCase, tag

from plans.models import Sprint


@tag('core')
class SprintStudioEditUrlTest(TestCase):
    """``Sprint.get_studio_edit_url`` returns the Studio editor URL."""

    def test_sprint_studio_edit_url(self):
        sprint = Sprint.objects.create(
            name='June 2026',
            slug='june-2026',
            start_date=datetime.date(2026, 6, 1),
        )
        self.assertEqual(
            sprint.get_studio_edit_url(),
            f'/studio/sprints/{sprint.pk}/edit',
        )
