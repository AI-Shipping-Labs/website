from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.test import SimpleTestCase

from content.models import (
    Article,
    Course,
    CuratedLink,
    Download,
    Instructor,
    InterviewCategory,
    Module,
    Project,
    Unit,
    Workshop,
    WorkshopPage,
)
from content.models.mixins import (
    SourceMetadataMixin,
    SyncedContentIdentityMixin,
    TimestampedModelMixin,
)
from events.models import Event


class SyncedMetadataMixinContractTest(SimpleTestCase):
    """The shared synced metadata mixins preserve public field contracts."""

    identity_models = [
        Article,
        Course,
        Unit,
        Download,
        Project,
        Workshop,
        WorkshopPage,
        Event,
    ]
    source_models = [
        Article,
        Course,
        Module,
        Unit,
        CuratedLink,
        Download,
        Project,
        Workshop,
        WorkshopPage,
        Instructor,
        InterviewCategory,
        Event,
    ]
    timestamped_models = [
        Article,
        Course,
        CuratedLink,
        Download,
        Project,
        Workshop,
        WorkshopPage,
        Instructor,
        InterviewCategory,
        Event,
    ]

    def test_source_metadata_fields_keep_names_and_columns(self):
        for model in self.source_models:
            with self.subTest(model=model.__name__):
                self.assertTrue(issubclass(model, SourceMetadataMixin))
                for field_name, max_length in [
                    ('source_repo', 300),
                    ('source_path', 500),
                    ('source_commit', 40),
                ]:
                    field = model._meta.get_field(field_name)
                    self.assertIsInstance(field, models.CharField)
                    self.assertEqual(field.column, field_name)
                    self.assertEqual(field.max_length, max_length)
                    self.assertTrue(field.blank)
                    self.assertTrue(field.null)
                    self.assertIsNone(field.default)

    def test_content_identity_only_on_models_keyed_by_uuid(self):
        for model in self.identity_models:
            with self.subTest(model=model.__name__):
                self.assertTrue(issubclass(model, SyncedContentIdentityMixin))
                field = model._meta.get_field('content_id')
                self.assertIsInstance(field, models.UUIDField)
                self.assertEqual(field.column, 'content_id')
                self.assertTrue(field.unique)
                self.assertTrue(field.blank)
                self.assertTrue(field.null)

        for model in [Module, CuratedLink, Instructor, InterviewCategory]:
            with self.subTest(model=model.__name__):
                self.assertFalse(issubclass(model, SyncedContentIdentityMixin))
                with self.assertRaises(FieldDoesNotExist):
                    model._meta.get_field('content_id')

    def test_timestamp_fields_keep_names_and_columns(self):
        for model in self.timestamped_models:
            with self.subTest(model=model.__name__):
                self.assertTrue(issubclass(model, TimestampedModelMixin))
                created = model._meta.get_field('created_at')
                updated = model._meta.get_field('updated_at')
                self.assertEqual(created.column, 'created_at')
                self.assertEqual(updated.column, 'updated_at')
                self.assertTrue(created.auto_now_add)
                self.assertTrue(updated.auto_now)

    def test_module_has_source_metadata_without_timestamps_or_uuid_identity(self):
        self.assertTrue(issubclass(Module, SourceMetadataMixin))
        self.assertFalse(issubclass(Module, SyncedContentIdentityMixin))
        self.assertFalse(issubclass(Module, TimestampedModelMixin))
        with self.assertRaises(FieldDoesNotExist):
            Module._meta.get_field('content_id')
        with self.assertRaises(FieldDoesNotExist):
            Module._meta.get_field('created_at')
