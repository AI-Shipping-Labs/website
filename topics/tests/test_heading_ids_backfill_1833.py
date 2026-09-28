"""Section-anchor backfill for stored topic HTML (issue #1833)."""

import importlib
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace

from django.apps import apps as django_apps
from django.db import connection
from django.test import TestCase

from topics.models import STATUS_DRAFT, TopicPage

_BACKFILL = importlib.import_module('topics.migrations.0003_backfill_heading_ids')


class TopicHeadingIdBackfillMigrationTest(TestCase):
    def test_missing_ids_added_and_existing_ids_kept(self):
        # Draft, so the cached Topics nav flag is not flipped for later tests.
        page = TopicPage.objects.create(
            slug='rag', title='RAG', body='x', status=STATUS_DRAFT,
        )
        TopicPage.objects.filter(pk=page.pk).update(
            body_html='<h2 id="setup">Setup</h2><h2>Setup</h2>',
        )

        with redirect_stdout(StringIO()):
            _BACKFILL.backfill(django_apps, SimpleNamespace(connection=connection))

        page.refresh_from_db()
        self.assertEqual(
            page.body_html,
            '<h2 id="setup">Setup</h2><h2 id="setup-1">Setup</h2>',
        )
