"""Server-side template render checks for the carry-forward control (issue #458).

The actual click flow (snapshot, optimistic move, sequential
``POST /api/checkpoints/<id>/move``, revert on failure, empty-state
update) runs in the browser; per ``_docs/testing-guidelines.md`` Rule 4
those scenarios live in
``playwright_tests/test_studio_plan_editor_carry_forward.py``.

What we MUST verify server-side is the rendering contract the JS reads:

- Each week card except the LAST renders a ``move-incomplete-to-next-week``
  button carrying the source ``data-week-id`` attribute.
- The final week card has NO such button (there is no week N+1 to
  receive the moves).
- The button's visible label reflects the destination week number so a
  staff editor knows what the click does before they click.

Why a Django ``TestCase`` and not Playwright for this surface: the
button's existence-per-week is a server-rendered template concern and
asserting on it in the test client is much faster than spinning up a
browser, while the click behaviour itself is a JS concern that cannot
be reproduced without a real browser.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import Plan, Sprint, Week

User = get_user_model()


class PlanEditorCarryForwardButtonRenderTest(TestCase):
    """Carry-forward button only on weeks 1..N-1, never on the final week."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint, status='draft',
        )
        cls.week_1 = Week.objects.create(
            plan=cls.plan, week_number=1, position=0,
        )
        cls.week_2 = Week.objects.create(
            plan=cls.plan, week_number=2, position=1,
        )
        cls.week_3 = Week.objects.create(
            plan=cls.plan, week_number=3, position=2,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def _get_editor_html(self):
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    @staticmethod
    def _slice_week_card(html, week_number):
        """Return the substring of ``html`` between the opening of the week
        card with ``data-week-number=N`` and the next ``</article>``.

        Slicing is safer than asserting on the whole body because every
        week card carries the same testid set; without the slice a
        button rendered on week 1 would also satisfy a button-on-week-3
        ``assertContains``.
        """
        marker = f'data-week-number="{week_number}"'
        start = html.index(marker)
        # Walk back to the enclosing <article>.
        article_open = html.rfind('<article', 0, start)
        article_close = html.index('</article>', start)
        return html[article_open:article_close + len('</article>')]

    def test_button_renders_on_week_1_with_source_week_id(self):
        html = self._get_editor_html()
        snippet = self._slice_week_card(html, 1)
        # The button exists in the Week 1 card.
        self.assertIn('data-testid="move-incomplete-to-next-week"', snippet)
        # And carries the SOURCE week id, not the destination -- the JS
        # reads ``btn.dataset.weekId`` to find the source list.
        self.assertIn(f'data-week-id="{self.week_1.pk}"', snippet)

    def test_button_renders_on_intermediate_week_2(self):
        """Week 2 is neither first nor last; it must still get a button.

        A naive ``forloop.first`` / ``forloop.last`` mix-up would drop
        the button from intermediate weeks. Asserting on Week 2
        explicitly catches that regression.
        """
        html = self._get_editor_html()
        snippet = self._slice_week_card(html, 2)
        self.assertIn('data-testid="move-incomplete-to-next-week"', snippet)
        self.assertIn(f'data-week-id="{self.week_2.pk}"', snippet)

    def test_button_does_not_render_on_final_week(self):
        """No carry-forward control on the final week (no next week).

        AC: ``The final week does not offer a carry-forward button
        because there is no next week.`` Sliced to the Week 3 card to
        avoid matching the buttons on Weeks 1 and 2.
        """
        html = self._get_editor_html()
        snippet = self._slice_week_card(html, 3)
        self.assertNotIn('data-testid="move-incomplete-to-next-week"', snippet)

    def test_button_count_equals_weeks_minus_one(self):
        """Exactly one button per non-final week — no duplicates, no extras."""
        html = self._get_editor_html()
        count = html.count('data-testid="move-incomplete-to-next-week"')
        self.assertEqual(count, 2)

    def test_button_label_names_the_destination_week(self):
        """Button text reads ``Move incomplete to Week N+1``.

        Staff click this without seeing the destination chips, so the
        label has to be unambiguous about WHICH week receives the
        moved items. Asserting on the label per source week guards
        against an off-by-one in the template (e.g. ``forloop.counter``
        vs ``week.week_number|add:1``).
        """
        html = self._get_editor_html()
        snippet_w1 = self._slice_week_card(html, 1)
        self.assertIn('Move incomplete to Week 2', snippet_w1)
        snippet_w2 = self._slice_week_card(html, 2)
        self.assertIn('Move incomplete to Week 3', snippet_w2)


class PlanEditorCarryForwardSingleWeekPlanTest(TestCase):
    """A plan with a single week renders no carry-forward button at all."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint, status='draft',
        )
        Week.objects.create(plan=cls.plan, week_number=1, position=0)

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_no_button_when_only_one_week(self):
        """The lone week is also the final week -- no button.

        Distinct from the two-week case: this catches a regression
        where the template special-cases ``forloop.first`` instead of
        ``not forloop.last``.
        """
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            'data-testid="move-incomplete-to-next-week"',
        )
