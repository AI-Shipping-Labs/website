"""Section-anchor backfill for stored note HTML (issue #1833)."""

import importlib
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from bookclub.models import Book, Chapter, Note

_BACKFILL = importlib.import_module('bookclub.migrations.0010_backfill_heading_ids')


class NoteHeadingIdBackfillMigrationTest(TestCase):
    def test_note_body_html_gains_heading_ids(self):
        book = Book.objects.create(title='Backfill book', slug='backfill-book')
        chapter = Chapter.objects.create(book=book, number=1, title='Ch 1')
        user = get_user_model().objects.create_user(
            email='notes@test.com', password='pw',
        )
        note = Note.objects.create(chapter=chapter, user=user, body='x')
        Note.objects.filter(pk=note.pk).update(
            body_html='<h2>Takeaways</h2><p>x</p><h2>Takeaways</h2>',
        )

        with redirect_stdout(StringIO()):
            _BACKFILL.backfill(django_apps, SimpleNamespace(connection=connection))

        note.refresh_from_db()
        self.assertEqual(
            note.body_html,
            '<h2 id="takeaways">Takeaways</h2><p>x</p>'
            '<h2 id="takeaways-1">Takeaways</h2>',
        )
