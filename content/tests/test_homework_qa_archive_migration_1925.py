"""Data migration 0086: retired review / LIP step comments move to the unit.

Issue #1925 removed live Q&A from the homework stepper's ``review`` and
``learning-in-public`` steps. Migration ``0086`` re-points every comment on
those step threads at the owning unit's ``content_id`` (the Review & submit
archive). No comment may be lost, re-running must change nothing, and odd
production rows must never crash ``migrate``.
"""

import importlib
import uuid

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from comments.models import Comment, CommentVote
from content.models.homework import HomeworkStepThread
from content.services.homework_step_threads import step_thread_content_id

_MIGRATION = importlib.import_module(
    'content.migrations.0086_homework_qa_intro_thread_review_archive',
)

UNIT_ID = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
OTHER_UNIT_ID = uuid.UUID('dddddddd-dddd-dddd-dddd-dddddddddddd')


def _thread(unit_id, slug):
    return HomeworkStepThread.objects.create(
        unit_content_id=unit_id, step_slug=slug,
        content_id=step_thread_content_id(unit_id, slug),
    )


def _snapshot():
    return sorted(
        Comment.objects.values_list(
            'pk', 'content_id', 'parent_id', 'body', 'hidden_at',
            'edited_at', 'created_at', 'updated_at',
        )
    )


def _run():
    _MIGRATION.move_retired_step_comments_to_unit_thread(django_apps, None)


class MoveRetiredStepCommentsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.author = User.objects.create_user(email='mig-author@test.com')
        cls.voter = User.objects.create_user(email='mig-voter@test.com')
        cls.review = _thread(UNIT_ID, 'review')
        cls.lip = _thread(UNIT_ID, 'learning-in-public')
        cls.q1 = _thread(UNIT_ID, 'q1-first')
        cls.other_review = _thread(OTHER_UNIT_ID, 'review')

    def _comment(self, content_id, body, **extra):
        return Comment.objects.create(
            content_id=content_id, user=self.author, body=body, **extra,
        )

    def test_review_and_lip_comments_move_to_their_own_unit_thread(self):
        review_q = self._comment(self.review.content_id, 'Does review show my score?')
        review_reply = self._comment(
            self.review.content_id, 'It does', parent=review_q,
        )
        hidden = self._comment(
            self.review.content_id, 'Spoiler', hidden_at=timezone.now(),
        )
        lip_q = self._comment(self.lip.content_id, 'Which platforms count?')
        other = self._comment(self.other_review.content_id, 'Other unit review')
        CommentVote.objects.create(comment=review_q, user=self.voter)
        before = {row[0]: row for row in _snapshot()}

        _run()

        for comment in (review_q, review_reply, hidden, lip_q):
            comment.refresh_from_db()
            self.assertEqual(comment.content_id, UNIT_ID, comment.body)
        other.refresh_from_db()
        self.assertEqual(other.content_id, OTHER_UNIT_ID)
        # The reply still hangs off its parent; the vote still counts.
        self.assertEqual(review_reply.parent_id, review_q.pk)
        self.assertEqual(
            CommentVote.objects.filter(comment=review_q).count(), 1,
        )
        # Only content_id changed: bodies, moderation and timestamps intact.
        after = {row[0]: row for row in _snapshot()}
        self.assertEqual(set(after), set(before))
        for pk, row in after.items():
            self.assertEqual(row[2:], before[pk][2:], pk)
        self.assertEqual(hidden.hidden_at, before[hidden.pk][4])

    def test_question_intro_and_unit_comments_are_untouched(self):
        q1_comment = self._comment(self.q1.content_id, 'I got 42')
        intro_id = step_thread_content_id(UNIT_ID, 'intro')
        intro_comment = self._comment(intro_id, 'Deadline in UTC?')
        unit_comment = self._comment(UNIT_ID, 'Legacy whole-homework')
        stray = self._comment(uuid.uuid4(), 'Unknown thread')
        before = _snapshot()

        _run()

        self.assertEqual(_snapshot(), before)
        q1_comment.refresh_from_db()
        intro_comment.refresh_from_db()
        unit_comment.refresh_from_db()
        stray.refresh_from_db()
        self.assertEqual(q1_comment.content_id, self.q1.content_id)
        self.assertEqual(intro_comment.content_id, intro_id)
        self.assertEqual(unit_comment.content_id, UNIT_ID)

    def test_second_run_changes_nothing_and_deletes_nothing(self):
        parent = self._comment(self.review.content_id, 'First')
        self._comment(self.review.content_id, 'Reply', parent=parent)
        self._comment(self.lip.content_id, 'LIP')
        self._comment(UNIT_ID, 'Legacy')
        total = Comment.objects.count()

        _run()
        after_first = _snapshot()
        _run()

        self.assertEqual(_snapshot(), after_first)
        self.assertEqual(Comment.objects.count(), total)
        self.assertFalse(
            Comment.objects.filter(
                content_id__in=[self.review.content_id, self.lip.content_id],
            ).exists(),
        )

    def test_step_thread_rows_are_kept(self):
        self._comment(self.review.content_id, 'Kept owner')

        _run()

        self.assertTrue(
            HomeworkStepThread.objects.filter(pk=self.review.pk).exists(),
        )
        self.assertTrue(HomeworkStepThread.objects.filter(pk=self.lip.pk).exists())

    def test_row_pointing_at_itself_is_skipped_not_crashing(self):
        """A malformed row (thread UUID == unit UUID) must not fail migrate."""
        self_unit = uuid.uuid4()
        HomeworkStepThread.objects.create(
            unit_content_id=self_unit, step_slug='review', content_id=self_unit,
        )
        comment = self._comment(self_unit, 'Already on the unit')

        _run()

        comment.refresh_from_db()
        self.assertEqual(comment.content_id, self_unit)

    def test_rows_without_comments_or_unit_are_a_no_op(self):
        # Unit row is absent entirely (only the plain UUID mirror exists),
        # and the threads have no comments: nothing to move, nothing raises.
        _run()
        self.assertEqual(Comment.objects.count(), 0)


class MigrationRunsThroughMigrateTest(TransactionTestCase):
    """Before/after through the real migration executor."""

    PRE = ('content', '0085_normalize_homework_draft_hours')
    POST = ('content', '0086_homework_qa_intro_thread_review_archive')
    # The comments app is not an ancestor of content 0085, so the project
    # state needs it as an explicit target to expose its models.
    COMMENTS = ('comments', '0003_comment_edited_at_comment_hidden_at')

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    @staticmethod
    def _migrate_to(target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([target])
        targets = [target, MigrationRunsThroughMigrateTest.COMMENTS]
        return MigrationExecutor(connection).loader.project_state(targets).apps

    def test_comments_move_on_migrate_and_survive_a_rerun(self):
        apps = self._migrate_to(self.PRE)
        Comment_ = apps.get_model('comments', 'Comment')
        Thread = apps.get_model('content', 'HomeworkStepThread')
        # accounts is fully migrated either way; the live model fills every
        # column default the historical one would leave NULL.
        user = get_user_model().objects.create_user(email='mig-exec@test.com')
        review_id = step_thread_content_id(UNIT_ID, 'review')
        q1_id = step_thread_content_id(UNIT_ID, 'q1-first')
        Thread.objects.create(
            unit_content_id=UNIT_ID, step_slug='review', content_id=review_id,
        )
        Thread.objects.create(
            unit_content_id=UNIT_ID, step_slug='q1-first', content_id=q1_id,
        )
        parent = Comment_.objects.create(
            content_id=review_id, user_id=user.pk, body='Before migrate',
        )
        Comment_.objects.create(
            content_id=review_id, user_id=user.pk, body='Reply', parent=parent,
        )
        Comment_.objects.create(content_id=q1_id, user_id=user.pk, body='Q1')

        apps = self._migrate_to(self.POST)
        Comment_ = apps.get_model('comments', 'Comment')
        self.assertEqual(Comment_.objects.count(), 3)
        self.assertEqual(
            Comment_.objects.filter(content_id=UNIT_ID).count(), 2,
        )
        self.assertEqual(Comment_.objects.filter(content_id=q1_id).count(), 1)

        # Backwards is a no-op; migrating forward again moves nothing more.
        self._migrate_to(self.PRE)
        apps = self._migrate_to(self.POST)
        Comment_ = apps.get_model('comments', 'Comment')
        self.assertEqual(Comment_.objects.count(), 3)
        self.assertEqual(
            Comment_.objects.filter(content_id=UNIT_ID).count(), 2,
        )
