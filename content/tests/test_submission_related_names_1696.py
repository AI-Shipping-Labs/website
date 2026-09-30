"""AISL frees the User reverse accessors ``community_base.coursework`` needs.

#1696 F2: the package's ``Submission.student`` and
``ProjectSubmission.student`` declare ``homework_submissions`` and
``project_submissions`` on User. AISL's own submissions use ``aisl_``-prefixed
names, and the rename migration is state-only.
"""

import io

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations import SeparateDatabaseAndState
from django.test import SimpleTestCase, TestCase

User = get_user_model()


class SubmissionRelatedNamesTest(TestCase):
    def test_content_submissions_do_not_claim_the_coursework_accessor_names(self):
        accessors = {
            rel.get_accessor_name(): rel.related_model._meta.label
            for rel in User._meta.related_objects
        }
        self.assertEqual(accessors['aisl_homework_submissions'], 'content.Submission')
        self.assertEqual(accessors['aisl_project_submissions'], 'content.ProjectSubmission')
        for name in ('homework_submissions', 'project_submissions'):
            self.assertFalse(
                accessors.get(name, '').startswith('content.'),
                f'{name} on User is still owned by {accessors.get(name)}',
            )

    def test_content_models_match_their_migrations(self):
        # ``makemigrations --check`` exits non-zero when the state drifts.
        call_command(
            'makemigrations', 'content', '--check', '--dry-run',
            stdout=io.StringIO(), stderr=io.StringIO(),
        )


class RenameMigrationIsStateOnlyTest(SimpleTestCase):
    PREVIOUS = ('content', '0079_backfill_heading_ids')
    RENAME = ('content', '0080_rename_user_submission_related_names')

    def test_rename_runs_no_database_operation_and_changes_only_related_name(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        [operation] = loader.disk_migrations[self.RENAME].operations
        self.assertIsInstance(operation, SeparateDatabaseAndState)
        self.assertEqual(operation.database_operations, [])

        before = loader.project_state(self.PREVIOUS)
        after = loader.project_state(self.RENAME)
        for model, field, new_name in (
            ('projectsubmission', 'user', 'aisl_project_submissions'),
            ('submission', 'student', 'aisl_homework_submissions'),
        ):
            old_kwargs = before.models['content', model].fields[field].deconstruct()[3]
            new_kwargs = after.models['content', model].fields[field].deconstruct()[3]
            self.assertEqual(new_kwargs.pop('related_name'), new_name)
            old_kwargs.pop('related_name')
            self.assertEqual(new_kwargs, old_kwargs)
