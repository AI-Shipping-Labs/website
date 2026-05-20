"""Visual de-clutter for the member plan body (issue #733).

The shared partial ``templates/plans/_plan_body.html`` previously wrapped
every heterogeneous top-level section (Goal, Resources, Action items,
Focus, Accountability, Details) in identical ``rounded-lg border
border-border bg-card p-4 sm:p-6`` chrome, and the Weekly work block had
three layers of nested cards (outer Weeks shell → per-week card →
Week-notes panel → per-note card). The result was a stack of look-alike
cards instead of a single prose-driven document.

These tests pin the cleanup so future edits don't regress:

- The outer ``<section>`` for Goal / Resources / Action items / Focus /
  Accountability / Details carries no ``bg-card`` or ``border-border``
  chrome.
- Each Week wrapper carries at most one ``border-border`` ``<div>``/
  ``<article>`` (the per-week ``<article>`` itself), proving the triple
  nest is gone.
- Section headings inside ``_plan_body.html`` use the design-system
  section-h2 scale (``text-2xl font-semibold tracking-tight``) instead
  of the card-title scale (``text-lg font-semibold``).
- Existing visible content (goal text, week themes, checkpoint
  descriptions, resource titles, deliverables, next steps,
  accountability) still renders -- no accidental data loss from the
  refactor.
"""

import datetime
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from plans.models import (
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    Resource,
    Sprint,
    SprintEnrollment,
    Week,
    WeekNote,
)

User = get_user_model()


def _section_html(body, testid):
    """Slice the rendered HTML between ``data-testid="<testid>"`` and the
    next closing ``</section>`` tag.

    Returns an empty string when the marker is not present so tests can
    assert ``not in`` without indexing into ``-1``.
    """
    needle = f'data-testid="{testid}"'
    start = body.find(needle)
    if start == -1:
        return ''
    # Walk back to the opening ``<section`` so we capture the whole
    # block, including the wrapper classes we care about asserting on.
    section_open = body.rfind('<section', 0, start)
    if section_open == -1:
        section_open = start
    section_close = body.find('</section>', start)
    if section_close == -1:
        section_close = len(body)
    return body[section_open:section_close]


class PlanBodyOwnerDeClutterTest(TestCase):
    """The owner workspace renders the same partial as the teammate
    read-only view. Using the owner view here gives us the maximum set
    of sections (Goal, Weeks + week notes, Resources, Deliverables,
    Next steps, Details, Focus, Accountability) in a single response.
    """

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=4,
        )
        cls.owner = User.objects.create_user(
            email='owner@test.com', password='pw',
            first_name='Olive', last_name='Owner',
        )
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.owner)
        cls.plan = Plan.objects.create(
            member=cls.owner,
            sprint=cls.sprint,
            visibility='private',
            goal='Ship the SME agent prototype',
            focus_main='Build the agent and demo it',
            accountability='Weekly checkin with mentor',
            summary_goal='Long-form **goal**',
        )
        cls.weeks = []
        for week_number in range(1, 5):
            week = Week.objects.create(
                plan=cls.plan,
                week_number=week_number,
                position=week_number - 1,
                theme=f'Theme {week_number}',
            )
            Checkpoint.objects.create(
                week=week,
                description=f'Checkpoint W{week_number}A',
                position=0,
            )
            Checkpoint.objects.create(
                week=week,
                description=f'Checkpoint W{week_number}B',
                position=1,
            )
            cls.weeks.append(week)
        # One week note on week 1 so the week-notes panel renders with
        # a populated list (not just the "No notes yet" empty state).
        WeekNote.objects.create(
            week=cls.weeks[0],
            author=cls.owner,
            body='How did week 1 go: prototype landed.',
        )
        Resource.objects.create(
            plan=cls.plan,
            title='RAG paper',
            url='https://example.com/rag',
            note='Key reference',
            position=0,
        )
        Deliverable.objects.create(
            plan=cls.plan,
            description='Demo recording',
            position=0,
        )
        NextStep.objects.create(
            plan=cls.plan,
            description='Book user review',
            position=0,
        )

    def setUp(self):
        self.client.force_login(self.owner)

    def _get_body(self):
        url = reverse(
            'my_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.plan.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response, response.content.decode('utf-8')

    # ------------------------------------------------------------------
    # Outer sections: no card shell on heterogeneous sections.
    # ------------------------------------------------------------------

    def test_goal_section_has_no_card_shell(self):
        _, body = self._get_body()
        section = _section_html(body, 'plan-goal')
        # The opening ``<section>`` tag (first 200 chars covers the
        # full opening tag including the ``data-*`` attributes).
        opening = section[:section.find('>') + 1]
        self.assertNotIn('bg-card', opening)
        self.assertNotIn('border-border', opening)
        self.assertNotIn('rounded-lg', opening)
        self.assertIn('mt-10', opening)

    def test_resources_section_has_no_card_shell(self):
        _, body = self._get_body()
        section = _section_html(body, 'plan-resources')
        opening = section[:section.find('>') + 1]
        self.assertNotIn('bg-card', opening)
        self.assertNotIn('border-border', opening)
        self.assertNotIn('rounded-lg', opening)
        self.assertIn('mt-10', opening)

    def test_action_items_section_has_no_card_shell(self):
        _, body = self._get_body()
        section = _section_html(body, 'plan-action-items')
        opening = section[:section.find('>') + 1]
        self.assertNotIn('bg-card', opening)
        self.assertNotIn('border-border', opening)
        self.assertNotIn('rounded-lg', opening)
        self.assertIn('mt-10', opening)

    def test_details_section_has_no_card_shell(self):
        _, body = self._get_body()
        # The owner-only Details block lives inside ``plan-summary``.
        section = _section_html(body, 'plan-summary')
        opening = section[:section.find('>') + 1]
        self.assertNotIn('bg-card', opening)
        self.assertNotIn('border-border', opening)
        self.assertNotIn('rounded-lg', opening)
        self.assertIn('mt-10', opening)

    def test_focus_section_has_no_card_shell(self):
        _, body = self._get_body()
        section = _section_html(body, 'plan-focus')
        opening = section[:section.find('>') + 1]
        self.assertNotIn('bg-card', opening)
        self.assertNotIn('border-border', opening)
        self.assertNotIn('rounded-lg', opening)
        self.assertIn('mt-10', opening)

    def test_accountability_section_has_no_card_shell(self):
        _, body = self._get_body()
        section = _section_html(body, 'plan-accountability')
        opening = section[:section.find('>') + 1]
        self.assertNotIn('bg-card', opening)
        self.assertNotIn('border-border', opening)
        self.assertNotIn('rounded-lg', opening)
        self.assertIn('mt-10', opening)

    def test_weeks_outer_shell_has_no_card_chrome(self):
        """The outer ``plan-weeks`` wrapper must not carry the
        ``bg-card`` / ``border-border`` chrome that wraps per-week
        cards. The per-week ``<article>`` is the only justified card.
        """
        _, body = self._get_body()
        section = _section_html(body, 'plan-weeks')
        opening = section[:section.find('>') + 1]
        self.assertNotIn('bg-card', opening)
        self.assertNotIn('border-border', opening)
        self.assertNotIn('rounded-lg', opening)

    # ------------------------------------------------------------------
    # Flatten the triple-nested chrome inside each Week.
    # ------------------------------------------------------------------

    def test_each_week_carries_at_most_one_border_border_wrapper(self):
        """Per-week wrappers used to nest three borders deep
        (per-week card → week-notes panel → per-note card). After
        #733 only the per-week ``<article>`` should carry
        ``border-border`` as a wrapper. The per-week-notes panel uses
        ``border-t`` (a top divider, not a wrapper border) and per-note
        rows have no border at all.

        We assert ``border border-border`` appears at most once on the
        wrapper elements (``<div>``, ``<article>``, ``<section>``,
        ``<ul>``, ``<li>``) inside the per-week chunk. Form fields
        (``<input>``, ``<textarea>``) and the round checkbox still use
        ``border-border`` for their own styling, but those are not
        wrapper chrome.
        """
        _, body = self._get_body()
        weeks_section = _section_html(body, 'plan-weeks')
        # Split by <article ... data-testid="plan-week" so each chunk
        # is a single week's HTML.
        chunks = re.split(
            r'(?=<article[^>]*data-testid="plan-week")', weeks_section,
        )
        # First chunk is the section preamble, drop it.
        week_chunks = chunks[1:]
        self.assertEqual(
            len(week_chunks), 4,
            f'expected 4 weeks, got {len(week_chunks)}',
        )
        wrapper_tag_re = re.compile(
            r'<(div|article|section|ul|li)\b[^>]*\bborder border-border\b',
        )
        for index, chunk in enumerate(week_chunks, start=1):
            # Trim each chunk to just this week's HTML by cutting at
            # the next ``</article>`` close tag.
            article_close = chunk.find('</article>')
            self.assertGreater(
                article_close, 0,
                f'week {index} missing </article> close',
            )
            week_html = chunk[:article_close + len('</article>')]
            wrapper_matches = wrapper_tag_re.findall(week_html)
            self.assertLessEqual(
                len(wrapper_matches), 1,
                (
                    f'week {index} has {len(wrapper_matches)} '
                    f'wrapper-style `border border-border` tags '
                    f'({wrapper_matches!r}); expected at most 1 '
                    f'(the per-week <article>).'
                ),
            )

    def test_week_notes_panel_uses_top_divider_not_card_chrome(self):
        """The per-week ``data-testid="plan-week-notes"`` wrapper
        previously used ``rounded-lg border border-border/70
        bg-secondary/40 p-3`` (a full card). After #733 it is a plain
        block separated by a top divider only.
        """
        _, body = self._get_body()
        weeks_section = _section_html(body, 'plan-weeks')
        notes_marker = 'data-testid="plan-week-notes"'
        start = weeks_section.find(notes_marker)
        self.assertGreater(start, 0)
        # Find the opening ``<div`` for this marker.
        div_open = weeks_section.rfind('<div', 0, start)
        self.assertGreater(div_open, 0)
        wrapper_tag = weeks_section[div_open:weeks_section.find('>', start) + 1]
        self.assertNotIn('bg-secondary', wrapper_tag)
        self.assertNotIn('rounded-lg', wrapper_tag)
        # The week-notes wrapper does NOT carry the wrapper-style
        # ``border border-border`` chrome anymore.
        self.assertNotIn('border border-border', wrapper_tag)
        # A subtle top divider replaces the card shell.
        self.assertIn('border-t', wrapper_tag)

    def test_per_note_row_has_no_card_chrome(self):
        """Per-note ``<li data-testid="plan-week-note">`` rows used to
        be mini-cards (``rounded-md border border-border bg-card
        p-3``). After #733 they are plain rows."""
        _, body = self._get_body()
        weeks_section = _section_html(body, 'plan-weeks')
        marker = 'data-testid="plan-week-note"'
        start = weeks_section.find(marker)
        self.assertGreater(
            start, 0,
            'expected at least one per-note row to render',
        )
        li_open = weeks_section.rfind('<li', 0, start)
        self.assertGreater(li_open, 0)
        wrapper_tag = weeks_section[li_open:weeks_section.find('>', start) + 1]
        self.assertNotIn('bg-card', wrapper_tag)
        self.assertNotIn('rounded-md', wrapper_tag)
        self.assertNotIn('border border-border', wrapper_tag)

    # ------------------------------------------------------------------
    # Section headings carry the section-h2 scale, not card-title.
    # ------------------------------------------------------------------

    def test_at_least_one_section_h2_uses_text_2xl_scale(self):
        """Once the outer cards are removed, the section ``<h2>`` is
        the only thing separating sections, so it must use the design
        system's section-h2 scale (``text-2xl``), not the card-title
        scale (``text-lg``)."""
        _, body = self._get_body()
        # Multiple h2s use text-2xl after #733 (Goal, Weekly work,
        # Resources, Deliverables, Next steps, Details, Focus,
        # Accountability). One is enough to prove the bump landed; we
        # check several below for completeness.
        self.assertIn('text-2xl font-semibold tracking-tight', body)

    def test_goal_heading_uses_section_scale(self):
        _, body = self._get_body()
        section = _section_html(body, 'plan-goal')
        self.assertIn(
            '<h2 class="text-2xl font-semibold tracking-tight',
            section,
        )

    def test_resources_heading_uses_section_scale(self):
        _, body = self._get_body()
        section = _section_html(body, 'plan-resources')
        self.assertIn(
            '<h2 class="text-2xl font-semibold tracking-tight',
            section,
        )

    def test_focus_heading_uses_section_scale(self):
        _, body = self._get_body()
        section = _section_html(body, 'plan-focus')
        self.assertIn(
            '<h2 class="text-2xl font-semibold tracking-tight',
            section,
        )

    def test_accountability_heading_uses_section_scale(self):
        _, body = self._get_body()
        section = _section_html(body, 'plan-accountability')
        self.assertIn(
            '<h2 class="text-2xl font-semibold tracking-tight',
            section,
        )

    # ------------------------------------------------------------------
    # Content regression: nothing visible was accidentally dropped.
    # ------------------------------------------------------------------

    def test_existing_visible_content_still_renders(self):
        response, body = self._get_body()
        # Goal copy.
        self.assertContains(response, 'Ship the SME agent prototype')
        # Week themes.
        for week_number in range(1, 5):
            self.assertContains(response, f'Theme {week_number}')
        # Checkpoint descriptions across the 4 weeks.
        for week_number in range(1, 5):
            self.assertContains(response, f'Checkpoint W{week_number}A')
            self.assertContains(response, f'Checkpoint W{week_number}B')
        # Week note body.
        self.assertContains(response, 'prototype landed.')
        # Resource title.
        self.assertContains(response, 'RAG paper')
        # Deliverable description.
        self.assertContains(response, 'Demo recording')
        # Next step description.
        self.assertContains(response, 'Book user review')
        # Focus + accountability copy.
        self.assertContains(response, 'Build the agent and demo it')
        self.assertContains(response, 'Weekly checkin with mentor')


class PlanBodyTeammateDeClutterTest(TestCase):
    """Re-run the highest-signal assertions through the teammate
    read-only view. The teammate path omits owner-only sections
    (``plan-summary``) so we only assert on the shared sections."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=4,
        )
        cls.owner = User.objects.create_user(
            email='owner@test.com', password='pw',
        )
        cls.teammate = User.objects.create_user(
            email='teammate@test.com', password='pw',
        )
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.owner)
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.teammate)
        cls.plan = Plan.objects.create(
            member=cls.owner,
            sprint=cls.sprint,
            visibility='cohort',
            goal='Ship the SME agent prototype',
            focus_main='Build the agent and demo it',
            accountability='Weekly checkin with mentor',
        )
        Plan.objects.create(
            member=cls.teammate, sprint=cls.sprint, visibility='private',
        )
        week = Week.objects.create(
            plan=cls.plan, week_number=1, position=0, theme='Theme 1',
        )
        Checkpoint.objects.create(
            week=week, description='Checkpoint W1A', position=0,
        )
        WeekNote.objects.create(
            week=week, author=cls.owner, body='Note from owner',
        )
        Resource.objects.create(
            plan=cls.plan, title='RAG paper',
            url='https://example.com/rag', position=0,
        )
        Deliverable.objects.create(
            plan=cls.plan, description='Demo recording', position=0,
        )
        NextStep.objects.create(
            plan=cls.plan, description='Book user review', position=0,
        )

    def setUp(self):
        self.client.force_login(self.teammate)

    def _get_body(self):
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.plan.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response, response.content.decode('utf-8')

    def test_teammate_goal_section_has_no_card_shell(self):
        _, body = self._get_body()
        section = _section_html(body, 'plan-goal')
        opening = section[:section.find('>') + 1]
        self.assertNotIn('bg-card', opening)
        self.assertNotIn('border-border', opening)

    def test_teammate_weeks_outer_shell_has_no_card_chrome(self):
        _, body = self._get_body()
        section = _section_html(body, 'plan-weeks')
        opening = section[:section.find('>') + 1]
        self.assertNotIn('bg-card', opening)
        self.assertNotIn('border-border', opening)

    def test_teammate_existing_visible_content_still_renders(self):
        response, _ = self._get_body()
        self.assertContains(response, 'Ship the SME agent prototype')
        self.assertContains(response, 'Theme 1')
        self.assertContains(response, 'Checkpoint W1A')
        self.assertContains(response, 'RAG paper')
        self.assertContains(response, 'Demo recording')
        self.assertContains(response, 'Book user review')
        self.assertContains(response, 'Build the agent and demo it')
        self.assertContains(response, 'Weekly checkin with mentor')
