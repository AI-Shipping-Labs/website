"""Thread-owner lifecycle and orphan cleanup contracts for issue #1467."""

import inspect
import io
import re
import uuid
from datetime import date, timedelta
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, tag
from django.utils import timezone

from bookclub.models import Book, Chapter, Note
from comments.models import Comment, CommentVote
from comments.threads import (
    delete_thread,
    thread_owners,
)
from content.models import Course, Module, Unit, Workshop, WorkshopPage
from notifications.models import Notification
from notifications.services import notification_service
from notifications.services.notification_service import content_comment_urls
from plans.models import Plan, Sprint

User = get_user_model()


def _book_and_chapter(slug='thread-book'):
    book = Book.objects.create(
        title='Thread Book',
        slug=slug,
        author='Author',
        status='current',
        start_date=date(2026, 8, 1),
    )
    return book, Chapter.objects.create(book=book, number=1, title='Chapter')


def _sprint(slug='thread-sprint'):
    return Sprint.objects.create(
        name='Thread Sprint',
        slug=slug,
        start_date=date(2026, 8, 1),
        duration_weeks=4,
        status='active',
    )


@tag('core')
class ThreadOwnerRegistryTest(TestCase):
    def test_registry_has_exact_supported_models_and_cascade_contracts(self):
        registered = {
            owner.model._meta.label: (
                owner.content_id_field,
                owner.cascade_thread_delete,
                owner.user_field,
            )
            for owner in thread_owners()
        }
        self.assertEqual(
            registered,
            {
                'bookclub.Note': ('comment_content_id', True, 'user'),
                'plans.Plan': ('comment_content_id', True, 'member'),
                'content.Unit': ('content_id', False, None),
                'content.WorkshopPage': ('content_id', False, None),
            },
        )

    def test_every_comment_content_id_model_is_cascading_registered(self):
        bridge_models = {
            model._meta.label
            for model in apps.get_models()
            if any(
                field.name == 'comment_content_id'
                for field in model._meta.get_fields()
            )
        }
        cascading = {
            owner.model._meta.label
            for owner in thread_owners()
            if owner.cascade_thread_delete
        }
        self.assertEqual(bridge_models, cascading)

    def test_notification_resolver_model_set_matches_registry(self):
        source = inspect.getsource(notification_service._resolve_commented_content)
        resolved_names = set(re.findall(r'\b([A-Z][A-Za-z0-9_]*)\.objects\b', source))
        registered_names = {owner.model.__name__ for owner in thread_owners()}
        self.assertEqual(resolved_names, registered_names)


@tag('core')
class CascadingThreadDeleteTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(email='owner@test.com')
        cls.other = User.objects.create_user(email='other@test.com')
        cls.voter = User.objects.create_user(email='voter@test.com')

    def _thread(self, content_id):
        top = Comment.objects.create(
            content_id=content_id,
            user=self.other,
            body='Top-level comment',
        )
        reply = Comment.objects.create(
            content_id=content_id,
            user=self.owner,
            parent=top,
            body='Owner reply',
        )
        vote = CommentVote.objects.create(comment=top, user=self.voter)
        return top, reply, vote

    def test_delete_thread_returns_exact_counts_and_uses_only_uuid_metadata(self):
        _, chapter = _book_and_chapter()
        note = Note.objects.create(chapter=chapter, user=self.owner, body='Note')
        self._thread(note.comment_content_id)
        note_url = content_comment_urls(note)[0]
        notice = Notification.objects.create(
            user=self.other,
            title='Thread notice',
            url=note_url,
            notification_type='content_comment',
            thread_content_id=note.comment_content_id,
        )
        legacy_same_url = Notification.objects.create(
            user=self.other,
            title='Legacy notice',
            url=note_url,
            notification_type='content_comment',
        )
        unrelated_uuid = Notification.objects.create(
            user=self.other,
            title='Other thread',
            url=note_url,
            notification_type='content_comment',
            thread_content_id=uuid.uuid4(),
        )
        different_type = Notification.objects.create(
            user=self.other,
            title='Keep',
            url=note_url,
            notification_type='announcement',
            thread_content_id=note.comment_content_id,
        )

        result = delete_thread(note, note.comment_content_id)

        self.assertEqual(
            result,
            {'comments': 2, 'comment_votes': 1, 'notifications': 1},
        )
        self.assertFalse(Notification.objects.filter(pk=notice.pk).exists())
        self.assertEqual(
            set(Notification.objects.values_list('pk', flat=True)),
            {legacy_same_url.pk, unrelated_uuid.pk, different_type.pk},
        )

    def test_note_queryset_delete_removes_comments_votes_and_only_its_notice(self):
        _, chapter = _book_and_chapter()
        note = Note.objects.create(chapter=chapter, user=self.owner, body='Note')
        top, reply, vote = self._thread(note.comment_content_id)
        note_url = content_comment_urls(note)[0]
        stale = Notification.objects.create(
            user=self.other,
            title='New comment on your note',
            url=note_url,
            notification_type='content_comment',
            thread_content_id=note.comment_content_id,
        )
        legacy = Notification.objects.create(
            user=self.other,
            title='Legacy same URL',
            url=note_url,
            notification_type='content_comment',
        )
        unrelated = Notification.objects.create(
            user=self.other,
            title='Keep',
            url=note_url,
            notification_type='content_comment',
            thread_content_id=uuid.uuid4(),
        )

        Note.objects.filter(pk=note.pk).delete()

        self.assertFalse(Comment.objects.filter(pk__in=[top.pk, reply.pk]).exists())
        self.assertFalse(CommentVote.objects.filter(pk=vote.pk).exists())
        self.assertFalse(Notification.objects.filter(pk=stale.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=legacy.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=unrelated.pk).exists())

    def test_plan_delete_removes_its_thread(self):
        plan = Plan.objects.create(
            member=self.owner,
            sprint=_sprint(),
            goal='Ship it',
        )
        top, reply, vote = self._thread(plan.comment_content_id)
        notifications = [
            Notification.objects.create(
                user=self.other,
                title='Plan comment',
                url=url,
                notification_type='content_comment',
                thread_content_id=plan.comment_content_id,
            )
            for url in content_comment_urls(plan)
        ]

        plan.delete()

        self.assertFalse(Comment.objects.filter(pk__in=[top.pk, reply.pk]).exists())
        self.assertFalse(CommentVote.objects.filter(pk=vote.pk).exists())
        self.assertFalse(
            Notification.objects.filter(
                pk__in=[notification.pk for notification in notifications],
            ).exists()
        )

    def test_parent_chapter_cascade_removes_every_note_thread(self):
        _, chapter = _book_and_chapter()
        second_owner = User.objects.create_user(email='second@test.com')
        notes = [
            Note.objects.create(chapter=chapter, user=self.owner, body='One'),
            Note.objects.create(chapter=chapter, user=second_owner, body='Two'),
        ]
        ids = [note.comment_content_id for note in notes]
        for content_id in ids:
            self._thread(content_id)

        chapter.delete()

        self.assertFalse(Comment.objects.filter(content_id__in=ids).exists())

    def test_user_cascade_removes_note_and_plan_threads(self):
        _, chapter = _book_and_chapter()
        note = Note.objects.create(chapter=chapter, user=self.owner, body='Note')
        plan = Plan.objects.create(member=self.owner, sprint=_sprint(), goal='Goal')
        ids = [note.comment_content_id, plan.comment_content_id]
        for content_id in ids:
            self._thread(content_id)

        self.owner.delete()

        self.assertFalse(Comment.objects.filter(content_id__in=ids).exists())

    def test_unit_and_workshop_page_deletes_preserve_synced_qa(self):
        course = Course.objects.create(title='Course', slug='course')
        module = Module.objects.create(
            course=course,
            title='Module',
            slug='module',
            sort_order=1,
        )
        unit = Unit.objects.create(
            module=module,
            title='Unit',
            slug='unit',
            sort_order=1,
            content_id=uuid.uuid4(),
        )
        workshop = Workshop.objects.create(
            title='Workshop',
            slug='workshop',
            date=date(2026, 8, 1),
        )
        page = WorkshopPage.objects.create(
            workshop=workshop,
            title='Page',
            slug='page',
            sort_order=1,
            content_id=uuid.uuid4(),
        )
        unit_comment = Comment.objects.create(
            content_id=unit.content_id,
            user=self.other,
            body='Course Q&A',
        )
        page_comment = Comment.objects.create(
            content_id=page.content_id,
            user=self.other,
            body='Workshop Q&A',
        )
        unit_notice = Notification.objects.create(
            user=self.other,
            title='Unit Q&A',
            notification_type='content_comment',
            thread_content_id=unit.content_id,
        )
        page_notice = Notification.objects.create(
            user=self.other,
            title='Workshop Q&A',
            notification_type='content_comment',
            thread_content_id=page.content_id,
        )

        unit.delete()
        page.delete()

        self.assertTrue(Comment.objects.filter(pk=unit_comment.pk).exists())
        self.assertTrue(Comment.objects.filter(pk=page_comment.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=unit_notice.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=page_notice.pk).exists())


@tag('core')
class ContentCommentUrlsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(email='url-owner@test.com')
        cls.member = User.objects.create_user(email='url-member@test.com')
        cls.staff = User.objects.create_user(email='url-staff@test.com', is_staff=True)
        _, chapter = _book_and_chapter('url-book')
        cls.note = Note.objects.create(chapter=chapter, user=cls.owner, body='Note')
        cls.plan = Plan.objects.create(
            member=cls.owner,
            sprint=_sprint('url-sprint'),
            goal='Goal',
        )

    def test_public_url_set_contains_every_recipient_specific_url(self):
        for content in (self.note, self.plan):
            urls = set(content_comment_urls(content))
            for recipient in (self.owner, self.member, self.staff):
                self.assertIn(
                    notification_service._content_comment_url(content, recipient),
                    urls,
                )
        self.assertEqual(len(content_comment_urls(self.note)), 1)
        self.assertEqual(len(content_comment_urls(self.plan)), 3)

    def test_synced_content_urls_are_the_generic_qa_deep_link(self):
        course = Course.objects.create(title='URL Course', slug='url-course')
        module = Module.objects.create(
            course=course,
            title='URL Module',
            slug='url-module',
            sort_order=1,
        )
        unit = Unit.objects.create(
            module=module,
            title='URL Unit',
            slug='url-unit',
            sort_order=1,
            content_id=uuid.uuid4(),
        )
        workshop = Workshop.objects.create(
            title='URL Workshop',
            slug='url-workshop',
            date=date(2026, 8, 1),
        )
        page = WorkshopPage.objects.create(
            workshop=workshop,
            title='URL Page',
            slug='url-page',
            sort_order=1,
            content_id=uuid.uuid4(),
        )
        for content in (unit, page):
            expected = (content.get_absolute_url() + '#qa-section',)
            self.assertEqual(content_comment_urls(content), expected)
            for recipient in (self.owner, self.member, self.staff):
                self.assertEqual(
                    notification_service._content_comment_url(content, recipient),
                    expected[0],
                )

    def test_unsupported_content_has_no_urls(self):
        self.assertEqual(content_comment_urls(object()), ())


@tag('core')
class CleanupOrphanedCommentThreadsCommandTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='cleanup@test.com')

    def _comment_at(self, content_id, body, when):
        comment = Comment.objects.create(
            content_id=content_id,
            user=self.user,
            body=body,
        )
        Comment.objects.filter(pk=comment.pk).update(
            created_at=when,
            updated_at=when,
        )
        return comment

    def test_default_dry_run_lists_only_old_orphan_and_deletes_nothing(self):
        old_id = uuid.uuid4()
        recent_id = uuid.uuid4()
        now = timezone.now()
        old_comments = [
            self._comment_at(old_id, 'Old one', now - timedelta(days=30)),
            self._comment_at(old_id, 'Old two', now - timedelta(days=20)),
        ]
        recent = self._comment_at(recent_id, 'Recent', now - timedelta(hours=2))

        output = io.StringIO()
        call_command('cleanup_orphaned_comment_threads', stdout=output)
        rendered = output.getvalue()

        self.assertIn(str(old_id), rendered)
        self.assertIn('2 comments, newest ', rendered)
        self.assertNotIn(str(recent_id), rendered)
        self.assertIn(
            '1 orphaned thread, 2 comments, 0 metadata-bearing notifications',
            rendered,
        )
        self.assertIn(
            'Legacy content-comment notifications without thread metadata are '
            'preserved because they cannot be safely mapped.',
            rendered,
        )
        self.assertIn('Dry run: nothing was deleted', rendered)
        self.assertIn('--apply', rendered)
        self.assertTrue(Comment.objects.filter(pk__in=[c.pk for c in old_comments]).exists())
        self.assertTrue(Comment.objects.filter(pk=recent.pk).exists())

    def test_apply_uses_delete_thread_and_second_run_reports_zero(self):
        orphan_id = uuid.uuid4()
        comment = self._comment_at(
            orphan_id,
            'Old orphan',
            timezone.now() - timedelta(days=10),
        )
        output = io.StringIO()
        with patch(
            'comments.management.commands.cleanup_orphaned_comment_threads.delete_thread',
            wraps=delete_thread,
        ) as cleanup:
            call_command(
                'cleanup_orphaned_comment_threads',
                '--apply',
                stdout=output,
            )

        cleanup.assert_called_once_with(None, orphan_id)
        self.assertFalse(Comment.objects.filter(pk=comment.pk).exists())
        self.assertIn('Cleanup applied.', output.getvalue())

        second = io.StringIO()
        call_command('cleanup_orphaned_comment_threads', stdout=second)
        self.assertIn(
            '0 orphaned threads, 0 comments, 0 metadata-bearing notifications',
            second.getvalue(),
        )

    def test_apply_deletes_only_old_uuid_matched_metadata_and_is_idempotent(self):
        old_id = uuid.uuid4()
        recent_id = uuid.uuid4()
        old_comment = self._comment_at(
            old_id, 'Old orphan', timezone.now() - timedelta(days=10),
        )
        old_vote = CommentVote.objects.create(comment=old_comment, user=self.user)
        recent_comment = self._comment_at(
            recent_id, 'Recent orphan', timezone.now() - timedelta(hours=2),
        )
        _, live_chapter = _book_and_chapter('cleanup-live-book')
        live_note = Note.objects.create(
            chapter=live_chapter,
            user=self.user,
            body='Live note',
        )
        shared_url = '/historical/dead-link#qa-section'
        old_notification = Notification.objects.create(
            user=self.user,
            title='Old orphan',
            url=shared_url,
            notification_type='content_comment',
            thread_content_id=old_id,
        )
        recent_notification = Notification.objects.create(
            user=self.user,
            title='Recent orphan',
            url=shared_url,
            notification_type='content_comment',
            thread_content_id=recent_id,
        )
        live_notification = Notification.objects.create(
            user=self.user,
            title='Live thread',
            url=shared_url,
            notification_type='content_comment',
            thread_content_id=live_note.comment_content_id,
        )
        unrelated_notification = Notification.objects.create(
            user=self.user,
            title='Unrelated orphan UUID',
            url=shared_url,
            notification_type='content_comment',
            thread_content_id=uuid.uuid4(),
        )
        legacy_notification = Notification.objects.create(
            user=self.user,
            title='Unmappable legacy row',
            url=shared_url,
            notification_type='content_comment',
        )
        other_type = Notification.objects.create(
            user=self.user,
            title='Matching UUID, different type',
            url=shared_url,
            notification_type='announcement',
            thread_content_id=old_id,
        )

        output = io.StringIO()
        call_command(
            'cleanup_orphaned_comment_threads',
            '--apply',
            stdout=output,
        )

        self.assertFalse(Comment.objects.filter(pk=old_comment.pk).exists())
        self.assertFalse(CommentVote.objects.filter(pk=old_vote.pk).exists())
        self.assertTrue(Comment.objects.filter(pk=recent_comment.pk).exists())
        self.assertFalse(Notification.objects.filter(pk=old_notification.pk).exists())
        preserved = {
            recent_notification.pk,
            live_notification.pk,
            unrelated_notification.pk,
            legacy_notification.pk,
            other_type.pk,
        }
        self.assertEqual(
            set(Notification.objects.values_list('pk', flat=True)),
            preserved,
        )
        self.assertIn(
            '1 orphaned thread, 1 comment, 1 metadata-bearing notification',
            output.getvalue(),
        )

        second = io.StringIO()
        call_command('cleanup_orphaned_comment_threads', '--apply', stdout=second)
        self.assertIn(
            '0 orphaned threads, 0 comments, 0 metadata-bearing notifications',
            second.getvalue(),
        )
        self.assertEqual(
            set(Notification.objects.values_list('pk', flat=True)),
            preserved,
        )

    def test_min_age_override_and_floor(self):
        orphan_id = uuid.uuid4()
        self._comment_at(
            orphan_id,
            'Two days old',
            timezone.now() - timedelta(days=2),
        )
        default = io.StringIO()
        call_command('cleanup_orphaned_comment_threads', stdout=default)
        self.assertNotIn(str(orphan_id), default.getvalue())

        one_day = io.StringIO()
        call_command(
            'cleanup_orphaned_comment_threads',
            '--min-age-days=1',
            stdout=one_day,
        )
        self.assertIn(str(orphan_id), one_day.getvalue())

        with self.assertRaisesMessage(CommandError, 'minimum value of 1'):
            call_command('cleanup_orphaned_comment_threads', '--min-age-days=0')
