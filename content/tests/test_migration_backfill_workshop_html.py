"""Tests for the ``Workshop`` / ``WorkshopPage`` rendered-HTML backfill (issue #791).

The backfill migration ``0045_backfill_workshop_rendered_html`` re-renders
``description_html`` and ``body_html`` for every existing row by calling the
live model's ``save()`` so the post-fix ``render_markdown`` pipeline takes
effect on rows whose source markdown has not changed.

These tests:

- Seed rows whose stored ``_html`` is the stale pre-fix shape
  (``&lt;br/&gt;`` inside ``<div class="mermaid">``).
- Invoke the migration's ``forwards`` callable directly so the test does not
  depend on the Django migration state machine (the migration uses live
  model classes, not historical ones, so direct invocation is the
  equivalent of running ``migrate``).
- Assert that the new ``_html`` collapses ``<br/>`` to ``\\n`` with zero
  ``&lt;br`` artifacts, that unrelated columns are untouched, that empty
  bodies stay empty, and that the reverse callable is a no-op.
"""

import datetime
import importlib

from django.test import TestCase

from content.models import Workshop, WorkshopPage

# Migration module names begin with a digit, so ``import`` won't accept the
# bare statement form. ``importlib.import_module`` loads it explicitly.
_MIGRATION = importlib.import_module(
    'content.migrations.0045_backfill_workshop_rendered_html',
)


STALE_DESCRIPTION_HTML = (
    '<div class="mermaid">flowchart LR\n'
    '    A[&quot;x&lt;br/&gt;y&quot;]</div>'
)

WORKSHOP_DESCRIPTION_WITH_BR = (
    '```mermaid\n'
    'flowchart LR\n'
    '    A["x<br/>y"]\n'
    '```\n'
)


class BackfillWorkshopRenderedHtmlForwardTest(TestCase):
    """The forward migration re-renders stale ``description_html``."""

    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(
            slug='br-render',
            title='Br Render',
            date=datetime.date(2026, 4, 21),
            description=WORKSHOP_DESCRIPTION_WITH_BR,
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
            source_commit='ORIGINAL_SHA',
        )
        # Bypass ``save()`` so we can pin the stale HTML the migration is
        # meant to fix. ``QuerySet.update`` writes the column directly
        # without running the model's ``save()`` (which would re-render).
        Workshop.objects.filter(pk=cls.workshop.pk).update(
            description_html=STALE_DESCRIPTION_HTML,
        )

        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop,
            slug='arch',
            title='Arch',
            sort_order=1,
            body=WORKSHOP_DESCRIPTION_WITH_BR,
        )
        WorkshopPage.objects.filter(pk=cls.page.pk).update(
            body_html=STALE_DESCRIPTION_HTML,
        )

    def test_workshop_description_html_drops_br_artifacts(self):
        _MIGRATION.backfill_workshop_rendered_html(None, None)

        ws = Workshop.objects.get(pk=self.workshop.pk)
        # Pre-fix shape: literal ``&lt;br/&gt;`` inside the escaped div.
        self.assertNotIn('&lt;br', ws.description_html)
        # Post-fix shape: the source ``<br/>`` was collapsed to ``\n``
        # before ``html.escape``, so the rendered escaped HTML contains a
        # real newline between ``x`` and ``y``.
        self.assertIn('A[&quot;x\ny&quot;]', ws.description_html)
        # Sanity: the mermaid wrapper is intact.
        self.assertIn('<div class="mermaid">', ws.description_html)

    def test_workshop_page_body_html_drops_br_artifacts(self):
        _MIGRATION.backfill_workshop_rendered_html(None, None)

        page = WorkshopPage.objects.get(pk=self.page.pk)
        self.assertNotIn('&lt;br', page.body_html)
        self.assertIn('A[&quot;x\ny&quot;]', page.body_html)
        self.assertIn('<div class="mermaid">', page.body_html)


class BackfillWorkshopRenderedHtmlEmptyBodyTest(TestCase):
    """Rows with empty ``description`` / ``body`` stay empty (no crash)."""

    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(
            slug='empty-render',
            title='Empty Render',
            date=datetime.date(2026, 4, 22),
            description='',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop,
            slug='blank',
            title='Blank',
            sort_order=1,
            body='',
        )

    def test_empty_description_keeps_html_empty(self):
        _MIGRATION.backfill_workshop_rendered_html(None, None)

        ws = Workshop.objects.get(pk=self.workshop.pk)
        self.assertEqual(ws.description_html, '')

    def test_empty_body_keeps_html_empty(self):
        _MIGRATION.backfill_workshop_rendered_html(None, None)

        page = WorkshopPage.objects.get(pk=self.page.pk)
        self.assertEqual(page.body_html, '')


class BackfillWorkshopRenderedHtmlUpdateFieldsTest(TestCase):
    """The backfill uses ``update_fields`` so unrelated columns are preserved.

    ``Workshop.save()`` normalizes tags and re-renders ``description_html``
    in memory; passing ``update_fields=['description_html']`` to
    ``super().save()`` makes the DB write only update that single column.
    A pre-set ``source_commit`` must therefore survive the backfill.
    """

    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(
            slug='preserve-cols',
            title='Preserve Cols',
            date=datetime.date(2026, 4, 23),
            description=WORKSHOP_DESCRIPTION_WITH_BR,
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        # Write source_commit + stale html directly via update() so the
        # row is in the exact prod-style shape before the backfill runs.
        Workshop.objects.filter(pk=cls.workshop.pk).update(
            description_html=STALE_DESCRIPTION_HTML,
            source_commit='OLD_SHA',
        )

        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop,
            slug='p1',
            title='Page 1',
            sort_order=1,
            body=WORKSHOP_DESCRIPTION_WITH_BR,
        )
        WorkshopPage.objects.filter(pk=cls.page.pk).update(
            body_html=STALE_DESCRIPTION_HTML,
            source_commit='PAGE_OLD_SHA',
        )

    def test_workshop_source_commit_is_not_touched(self):
        _MIGRATION.backfill_workshop_rendered_html(None, None)

        ws = Workshop.objects.get(pk=self.workshop.pk)
        self.assertEqual(ws.source_commit, 'OLD_SHA')
        # And the html WAS re-rendered.
        self.assertNotIn('&lt;br', ws.description_html)

    def test_workshop_page_source_commit_is_not_touched(self):
        _MIGRATION.backfill_workshop_rendered_html(None, None)

        page = WorkshopPage.objects.get(pk=self.page.pk)
        self.assertEqual(page.source_commit, 'PAGE_OLD_SHA')
        self.assertNotIn('&lt;br', page.body_html)


class BackfillWorkshopRenderedHtmlReverseTest(TestCase):
    """``RunPython.noop`` reverse completes without error or row changes.

    ``Migration.operations[0].reverse_code`` is the ``noop`` callable; the
    test exercises the same callable the migration framework will run on
    ``migrate <app> <prev>``.
    """

    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(
            slug='reverse-noop',
            title='Reverse Noop',
            date=datetime.date(2026, 4, 24),
            description=WORKSHOP_DESCRIPTION_WITH_BR,
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )

    def test_reverse_is_noop_and_completes_without_error(self):
        before = Workshop.objects.get(pk=self.workshop.pk).description_html
        reverse_code = _MIGRATION.Migration.operations[0].reverse_code
        # Django sets ``RunPython.noop`` as the reverse for this operation;
        # invoking it must complete without error and not mutate any row.
        reverse_code(None, None)
        after = Workshop.objects.get(pk=self.workshop.pk).description_html
        self.assertEqual(before, after)
