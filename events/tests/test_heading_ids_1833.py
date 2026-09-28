"""Section anchors on event surfaces (issue #1833)."""

import importlib
from contextlib import redirect_stdout
from datetime import time, timedelta
from io import StringIO
from types import SimpleNamespace

from django.apps import apps as django_apps
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventSeries, Host

_BACKFILL = importlib.import_module('events.migrations.0053_backfill_heading_ids')
_STORED = '<p>Intro</p><h2>How to join</h2><p>x</p><h2>How to join</h2>'
_EXPECTED = (
    '<p>Intro</p><h2 id="how-to-join">How to join</h2><p>x</p>'
    '<h2 id="how-to-join-1">How to join</h2>'
)


class EventDescriptionHeadingIdTest(TestCase):
    def test_event_detail_page_renders_description_heading_id(self):
        event = Event.objects.create(
            title='Anchor event', slug='anchor-event',
            start_datetime=timezone.now() + timedelta(days=3),
            status='upcoming',
            description='Intro paragraph.\n\n## How to join\n\nUse the Zoom link.',
        )

        response = self.client.get(event.get_absolute_url())

        self.assertContains(
            response, '<h2 id="how-to-join">How to join</h2>', html=False,
        )


class EventHeadingIdBackfillMigrationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        event = Event.objects.create(
            title='Backfill event', slug='backfill-event',
            start_datetime=timezone.now(), description='x',
        )
        series = EventSeries.objects.create(
            name='Backfill series', slug='backfill-series', start_time=time(18),
        )
        host = Host.objects.create(name='Backfill host', slug='backfill-host')
        cls.targets = [
            (event, 'description_html'),
            (event, 'recap_html'),
            (event, 'recap_notes_html'),
            (series, 'description_html'),
            (host, 'bio_html'),
        ]
        for obj, field in cls.targets:
            type(obj).objects.filter(pk=obj.pk).update(**{field: _STORED})
        cls.plain = Event.objects.create(
            title='Plain event', slug='plain-event',
            start_datetime=timezone.now(), description='x',
        )
        Event.objects.filter(pk=cls.plain.pk).update(
            description_html='<p>No headings.</p>',
        )

    def test_every_covered_field_gains_heading_ids(self):
        with redirect_stdout(StringIO()):
            _BACKFILL.backfill(
                django_apps, SimpleNamespace(connection=connection),
            )

        for obj, field in self.targets:
            with self.subTest(model=type(obj).__name__, field=field):
                stored = type(obj).objects.values_list(field, flat=True).get(
                    pk=obj.pk,
                )
                self.assertEqual(stored, _EXPECTED)
        self.plain.refresh_from_db()
        self.assertEqual(self.plain.description_html, '<p>No headings.</p>')
