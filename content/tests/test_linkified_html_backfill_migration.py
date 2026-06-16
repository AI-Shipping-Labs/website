"""Tests for the issue #1000 linkified markdown HTML backfill."""

import datetime
import importlib

from django.test import TestCase

from content.models import Instructor, Workshop, WorkshopPage

_MIGRATION = importlib.import_module(
    'content.migrations.0048_backfill_linkified_markdown_html',
)


class LinkifiedHtmlBackfillMigrationTest(TestCase):
    """The data migration re-renders stale stored HTML fields."""

    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(
            slug='linkify-backfill',
            title='Linkify Backfill',
            date=datetime.date(2026, 4, 21),
            description='Reference: https://example.com/workshop-notes',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        Workshop.objects.filter(pk=cls.workshop.pk).update(
            description_html='<p>Reference: https://example.com/workshop-notes</p>',
            source_commit='WORKSHOP_SHA',
        )

        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop,
            slug='setup',
            title='Setup',
            body='Setup: https://example.com/setup',
        )
        WorkshopPage.objects.filter(pk=cls.page.pk).update(
            body_html='<p>Setup: https://example.com/setup</p>',
            source_commit='PAGE_SHA',
        )

        cls.instructor = Instructor.objects.create(
            instructor_id='linkify-backfill-instructor',
            name='Linkify Backfill Instructor',
            bio='Profile: https://example.com/instructor',
        )
        Instructor.objects.filter(pk=cls.instructor.pk).update(
            bio_html='<p>Profile: https://example.com/instructor</p>',
            source_commit='INSTRUCTOR_SHA',
        )

    def test_backfills_workshop_description_html(self):
        _MIGRATION.backfill_linkified_markdown_html(None, None)

        workshop = Workshop.objects.get(pk=self.workshop.pk)

        self.assertIn('href="https://example.com/workshop-notes"', workshop.description_html)
        self.assertIn('target="_blank"', workshop.description_html)
        self.assertEqual(workshop.source_commit, 'WORKSHOP_SHA')

    def test_backfills_workshop_page_body_html(self):
        _MIGRATION.backfill_linkified_markdown_html(None, None)

        page = WorkshopPage.objects.get(pk=self.page.pk)

        self.assertIn('href="https://example.com/setup"', page.body_html)
        self.assertIn('target="_blank"', page.body_html)
        self.assertEqual(page.source_commit, 'PAGE_SHA')

    def test_backfills_instructor_bio_html(self):
        _MIGRATION.backfill_linkified_markdown_html(None, None)

        instructor = Instructor.objects.get(pk=self.instructor.pk)

        self.assertIn('href="https://example.com/instructor"', instructor.bio_html)
        self.assertIn('target="_blank"', instructor.bio_html)
        self.assertEqual(instructor.source_commit, 'INSTRUCTOR_SHA')


class LinkifiedHtmlBackfillEmptyFieldsTest(TestCase):
    """Empty markdown fields remain empty after the backfill."""

    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(
            slug='empty-linkify-backfill',
            title='Empty Linkify Backfill',
            date=datetime.date(2026, 4, 22),
            description='',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop,
            slug='empty',
            title='Empty',
            body='',
        )
        cls.instructor = Instructor.objects.create(
            instructor_id='empty-linkify-backfill-instructor',
            name='Empty Linkify Backfill Instructor',
            bio='',
        )

    def test_empty_fields_stay_empty(self):
        _MIGRATION.backfill_linkified_markdown_html(None, None)

        self.assertEqual(
            Workshop.objects.get(pk=self.workshop.pk).description_html,
            '',
        )
        self.assertEqual(WorkshopPage.objects.get(pk=self.page.pk).body_html, '')
        self.assertEqual(Instructor.objects.get(pk=self.instructor.pk).bio_html, '')
