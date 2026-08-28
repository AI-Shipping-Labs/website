"""Tests for shared-comment in-app notifications (issues #1341/#1361/#1365).

When a member posts a comment or reply on a course unit lesson or workshop
tutorial page, each distinct linked content author (``Instructor.user``)
receives one in-app ``content_comment`` notification, excluding the commenter.
"""

import json
import uuid
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from bookclub.models import Book, Chapter, Note
from comments.models import Comment
from comments.services import create_comment
from content.models import (
    Course,
    Instructor,
    Module,
    Unit,
    Workshop,
    WorkshopPage,
)
from notifications.models import Notification
from notifications.services.notification_service import NotificationService
from payments.models import Tier
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


@tag('core')
class ContentCommentNotifyTest(TestCase):
    """Recipient resolution, dedup, self-notify guard, title/body/url."""

    @classmethod
    def setUpTestData(cls):
        cls.commenter = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.author = User.objects.create_user(
            email='instructor@test.com', password='pw', first_name='Ada',
        )
        cls.author2 = User.objects.create_user(
            email='bob@test.com', password='pw',
        )
        cls.asker = User.objects.create_user(
            email='asker@test.com', password='pw',
        )

        # Course unit surface, single linked instructor.
        cls.course = Course.objects.create(
            title='ML Zoomcamp', slug='ml-zoomcamp', status='published',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module 1', slug='module-1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Intro to ML', slug='intro',
            sort_order=1, content_id=uuid.uuid4(),
        )
        cls.instructor = Instructor.objects.create(
            instructor_id='ada', name='Ada', status='published',
            user=cls.author,
        )
        cls.course.instructors.add(cls.instructor)

        # Workshop tutorial page surface, single linked instructor.
        cls.workshop = Workshop.objects.create(
            title='RAG Workshop', slug='rag-workshop',
            date=date(2025, 1, 1), status='published',
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='setup', title='Setup',
            sort_order=1, body='Body', content_id=uuid.uuid4(),
        )
        cls.workshop.instructors.add(cls.instructor)

    def _comment(self, content_id, user=None, parent=None, body='A question'):
        return create_comment(
            content_id=content_id,
            user=user or self.commenter,
            body=body,
            parent=parent,
        )

    def test_top_level_comment_on_unit_notifies_linked_author(self):
        self._comment(self.unit.content_id)
        notes = Notification.objects.filter(notification_type='content_comment')
        self.assertEqual(notes.count(), 1)
        note = notes.get()
        self.assertEqual(note.user, self.author)
        self.assertEqual(note.title, 'New comment on Intro to ML')
        self.assertEqual(
            note.url, '/courses/ml-zoomcamp/module-1/intro#qa-section',
        )
        self.assertEqual(note.thread_content_id, self.unit.content_id)

    def test_reply_on_workshop_page_uses_reply_title(self):
        top = self._comment(self.page.content_id, body='First question')
        Notification.objects.all().delete()
        self._comment(self.page.content_id, parent=top, body='A reply')
        note = Notification.objects.get(notification_type='content_comment')
        self.assertEqual(note.user, self.author)
        self.assertEqual(note.title, 'New reply on Setup')
        self.assertTrue(note.url.endswith('/tutorial/setup#qa-section'))
        self.assertEqual(note.thread_content_id, self.page.content_id)

    def test_reply_notifies_parent_author_and_linked_content_author(self):
        top = self._comment(
            self.unit.content_id,
            user=self.asker,
            body='First question',
        )
        Notification.objects.all().delete()

        self._comment(self.unit.content_id, parent=top, body='A reply')

        notes = Notification.objects.filter(notification_type='content_comment')
        self.assertEqual(notes.count(), 2)
        self.assertEqual(
            set(notes.values_list('user_id', flat=True)),
            {self.author.pk, self.asker.pk},
        )
        self.assertEqual(
            set(notes.values_list('thread_content_id', flat=True)),
            {self.unit.content_id},
        )
        direct_reply = notes.get(user=self.asker)
        self.assertEqual(direct_reply.title, 'New reply to your comment')
        self.assertEqual(
            direct_reply.url,
            '/courses/ml-zoomcamp/module-1/intro#qa-section',
        )

    def test_parent_author_who_is_also_content_author_gets_one_notification(self):
        top = self._comment(
            self.unit.content_id,
            user=self.author,
            body='Instructor question',
        )
        Notification.objects.all().delete()

        self._comment(self.unit.content_id, parent=top, body='A reply')

        note = Notification.objects.get(
            user=self.author,
            notification_type='content_comment',
        )
        self.assertEqual(note.title, 'New reply on Intro to ML')

    def test_reply_does_not_notify_unrelated_historical_participants(self):
        self._comment(
            self.unit.content_id,
            user=self.author2,
            body='Unrelated earlier question',
        )
        parent = self._comment(
            self.unit.content_id,
            user=self.asker,
            body='Question being answered',
        )
        Notification.objects.all().delete()

        self._comment(self.unit.content_id, parent=parent, body='A reply')

        notes = Notification.objects.filter(notification_type='content_comment')
        self.assertEqual(
            set(notes.values_list('user_id', flat=True)),
            {self.author.pk, self.asker.pk},
        )
        self.assertFalse(notes.filter(user=self.author2).exists())

    def test_body_contains_commenter_name_and_excerpt(self):
        self._comment(self.unit.content_id, body='How do I install this?')
        note = Notification.objects.get(notification_type='content_comment')
        # display_name falls back to email local-part; author has first_name
        # but the COMMENTER has none, so it is the email handle.
        self.assertIn('member', note.body)
        self.assertIn('How do I install this?', note.body)

    def test_body_excerpt_is_truncated(self):
        long_body = 'x' * 500
        self._comment(self.unit.content_id, body=long_body)
        note = Notification.objects.get(notification_type='content_comment')
        # 200-char excerpt + ellipsis, plus the commenter prefix.
        self.assertIn('…', note.body)
        self.assertLess(len(note.body), 260)

    def test_author_commenting_on_own_content_is_not_notified(self):
        self._comment(self.unit.content_id, user=self.author)
        self.assertEqual(
            Notification.objects.filter(
                notification_type='content_comment',
            ).count(),
            0,
        )

    def test_no_linked_author_creates_no_notification(self):
        unlinked = Instructor.objects.create(
            instructor_id='nobody', name='Nobody', status='published',
        )
        course = Course.objects.create(
            title='Solo', slug='solo', status='published',
        )
        module = Module.objects.create(
            course=course, title='M', slug='m', sort_order=1,
        )
        unit = Unit.objects.create(
            module=module, title='U', slug='u', sort_order=1,
            content_id=uuid.uuid4(),
        )
        course.instructors.add(unlinked)

        self._comment(unit.content_id)
        self.assertEqual(
            Notification.objects.filter(
                notification_type='content_comment',
            ).count(),
            0,
        )

    def test_two_distinct_authors_each_notified_once(self):
        instructor2 = Instructor.objects.create(
            instructor_id='bob', name='Bob', status='published',
            user=self.author2,
        )
        self.workshop.instructors.add(instructor2)

        self._comment(self.page.content_id)
        notes = Notification.objects.filter(notification_type='content_comment')
        self.assertEqual(notes.count(), 2)
        self.assertEqual(
            {n.user_id for n in notes},
            {self.author.pk, self.author2.pk},
        )

    def test_one_user_linked_to_two_instructors_gets_one_notification(self):
        # A second instructor on the SAME content linked to the SAME user.
        instructor_dup = Instructor.objects.create(
            instructor_id='ada-alias', name='Ada Alias', status='published',
            user=self.author,
        )
        self.course.instructors.add(instructor_dup)

        self._comment(self.unit.content_id)
        notes = Notification.objects.filter(
            notification_type='content_comment', user=self.author,
        )
        self.assertEqual(notes.count(), 1)

    def test_co_author_commenter_skipped_but_co_author_notified(self):
        instructor2 = Instructor.objects.create(
            instructor_id='bob', name='Bob', status='published',
            user=self.author2,
        )
        self.workshop.instructors.add(instructor2)

        # author (Ada) comments; only author2 (Bob) should be notified.
        self._comment(self.page.content_id, user=self.author)
        notes = Notification.objects.filter(notification_type='content_comment')
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes.get().user, self.author2)

    def test_unknown_content_id_notifies_nobody(self):
        result = NotificationService.notify_content_comment(
            self._comment(uuid.uuid4()),
        )
        self.assertEqual(result, {"notified": 0})
        self.assertEqual(
            Notification.objects.filter(
                notification_type='content_comment',
            ).count(),
            0,
        )


@tag('core')
class ContentCommentPlanNotifyTest(TestCase):
    """Plan-owner/direct-reply delivery, access, de-dup, and URLs."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(email='owner@test.com', password='pw')
        cls.teammate = User.objects.create_user(
            email='teammate@test.com', password='pw',
        )
        cls.replier = User.objects.create_user(
            email='replier@test.com', password='pw',
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.outsider = User.objects.create_user(
            email='outsider@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='August Sprint', slug='august-sprint',
            start_date=date(2026, 8, 1),
        )
        cls.plan = Plan.objects.create(
            member=cls.owner,
            sprint=cls.sprint,
            visibility='cohort',
            title='Ship the assistant',
        )
        SprintEnrollment.objects.get_or_create(
            sprint=cls.sprint, user=cls.teammate,
        )
        SprintEnrollment.objects.get_or_create(
            sprint=cls.sprint, user=cls.replier,
        )

    def _comment(self, user, *, parent=None, body='Plan feedback'):
        return create_comment(
            content_id=self.plan.comment_content_id,
            user=user,
            body=body,
            parent=parent,
        )

    def test_staff_comment_on_private_plan_notifies_owner_on_owner_route(self):
        self.plan.visibility = 'private'
        self.plan.save(update_fields=['visibility'])
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse(
                'comments_endpoint',
                kwargs={'content_id': self.plan.comment_content_id},
            ),
            data=json.dumps({'body': 'Staff feedback'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        note = Notification.objects.get(
            user=self.owner,
            notification_type='content_comment',
        )
        self.assertEqual(note.title, 'New comment on your plan')
        self.assertEqual(note.thread_content_id, self.plan.comment_content_id)
        self.assertEqual(
            note.url,
            reverse(
                'my_plan_detail',
                kwargs={
                    'sprint_slug': self.sprint.slug,
                    'plan_id': self.plan.pk,
                },
            ) + '#qa-section',
        )

    def test_denied_plan_write_creates_no_comment_or_notification(self):
        self.client.force_login(self.outsider)

        response = self.client.post(
            reverse(
                'comments_endpoint',
                kwargs={'content_id': self.plan.comment_content_id},
            ),
            data=json.dumps({'body': 'Unauthorized feedback'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            Comment.objects.filter(
                content_id=self.plan.comment_content_id,
            ).exists(),
        )
        self.assertFalse(
            Notification.objects.filter(
                notification_type='content_comment',
            ).exists(),
        )

    def test_owner_comment_on_own_plan_does_not_notify_owner(self):
        self._comment(self.owner)

        self.assertFalse(
            Notification.objects.filter(
                user=self.owner,
                notification_type='content_comment',
            ).exists(),
        )

    def test_cohort_reply_notifies_owner_and_parent_author_once_each(self):
        parent = self._comment(self.teammate, body='Can you clarify?')
        Notification.objects.all().delete()

        self._comment(self.replier, parent=parent, body='Here is more context')

        notes = Notification.objects.filter(notification_type='content_comment')
        self.assertEqual(notes.count(), 2)
        self.assertEqual(
            set(notes.values_list('user_id', flat=True)),
            {self.owner.pk, self.teammate.pk},
        )
        self.assertEqual(
            set(notes.values_list('thread_content_id', flat=True)),
            {self.plan.comment_content_id},
        )
        self.assertEqual(
            notes.get(user=self.teammate).url,
            reverse(
                'member_plan_detail',
                kwargs={
                    'sprint_slug': self.sprint.slug,
                    'plan_id': self.plan.pk,
                },
            ) + '#qa-section',
        )

    def test_staff_parent_author_gets_studio_route(self):
        parent = self._comment(self.staff, body='Staff question')
        Notification.objects.all().delete()

        self._comment(self.replier, parent=parent)

        staff_note = Notification.objects.get(
            user=self.staff,
            notification_type='content_comment',
        )
        self.assertEqual(
            staff_note.url,
            reverse(
                'studio_plan_detail', kwargs={'plan_id': self.plan.pk},
            ) + '#qa-section',
        )

    def test_reply_skips_parent_author_who_lost_plan_read_access(self):
        parent = self._comment(self.teammate, body='Before leaving the sprint')
        Notification.objects.all().delete()
        SprintEnrollment.objects.filter(
            sprint=self.sprint, user=self.teammate,
        ).delete()

        self._comment(self.replier, parent=parent)

        notes = Notification.objects.filter(notification_type='content_comment')
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes.get().user, self.owner)

    def test_plan_owner_parent_role_is_deduplicated(self):
        parent = self._comment(self.owner, body='Owner question')
        Notification.objects.all().delete()

        self._comment(self.replier, parent=parent)

        notes = Notification.objects.filter(
            user=self.owner,
            notification_type='content_comment',
        )
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes.get().title, 'New reply on your plan')

    def test_owner_reply_does_not_self_notify_but_notifies_parent_author(self):
        parent = self._comment(self.teammate, body='Teammate question')
        Notification.objects.all().delete()

        self._comment(self.owner, parent=parent)

        notes = Notification.objects.filter(notification_type='content_comment')
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes.get().user, self.teammate)

    def test_top_level_comment_does_not_notify_earlier_plan_commenters(self):
        self._comment(self.teammate, body='Earlier comment')
        Notification.objects.all().delete()

        self._comment(self.replier, body='New top-level comment')

        notes = Notification.objects.filter(notification_type='content_comment')
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes.get().user, self.owner)

    def test_new_plan_notification_uses_existing_list_count_and_read_apis(self):
        self._comment(self.staff)
        note = Notification.objects.get(
            user=self.owner,
            notification_type='content_comment',
        )
        self.client.force_login(self.owner)

        listed = self.client.get('/api/notifications?filter=unread')
        unread_count = self.client.get('/api/notifications/unread-count')
        marked = self.client.post(f'/api/notifications/{note.pk}/read')

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()['notifications'][0]['id'], note.pk)
        self.assertEqual(unread_count.json(), {'count': 1})
        self.assertEqual(marked.status_code, 200)
        note.refresh_from_db()
        self.assertTrue(note.read)


@tag('core')
class ContentCommentBookNoteNotifyTest(TestCase):
    """Exact note targeting and gated direct-reply recipient checks."""

    @classmethod
    def setUpTestData(cls):
        cls.main_tier = Tier.objects.get(slug='main')
        cls.free_tier = Tier.objects.get(slug='free')
        cls.note_owner = User.objects.create_user(
            email='note-owner@test.com', password='pw', tier=cls.main_tier,
        )
        cls.parent_author = User.objects.create_user(
            email='note-reader@test.com', password='pw', tier=cls.main_tier,
        )
        cls.replier = User.objects.create_user(
            email='note-replier@test.com', password='pw', tier=cls.main_tier,
        )
        cls.book = Book.objects.create(
            title='Inference Engineering',
            slug='inference-engineering',
            author='Philip Kiely',
            required_level=20,
            status='current',
            start_date=date(2026, 8, 10),
        )
        cls.chapter = Chapter.objects.create(
            book=cls.book, number=1, title='Serving',
        )
        cls.note = Note.objects.create(
            chapter=cls.chapter,
            user=cls.note_owner,
            body='KV-cache notes',
        )

    def _comment(self, user, *, parent=None, body='Book reply'):
        return create_comment(
            content_id=self.note.comment_content_id,
            user=user,
            body=body,
            parent=parent,
        )

    def test_book_reply_notifies_owner_and_parent_at_exact_note_section(self):
        parent = self._comment(self.parent_author, body='Reader comment')
        Notification.objects.all().delete()

        self._comment(self.replier, parent=parent)

        notes = Notification.objects.filter(notification_type='content_comment')
        expected_url = (
            self.note.get_absolute_url() + f'#qa-section-{self.note.pk}'
        )
        self.assertEqual(notes.count(), 2)
        self.assertEqual(
            set(notes.values_list('user_id', flat=True)),
            {self.note_owner.pk, self.parent_author.pk},
        )
        self.assertEqual(
            set(notes.values_list('url', flat=True)),
            {expected_url},
        )
        self.assertEqual(
            set(notes.values_list('thread_content_id', flat=True)),
            {self.note.comment_content_id},
        )
        self.assertEqual(
            notes.get(user=self.note_owner).title,
            'New reply on your note',
        )

    def test_book_reply_skips_parent_author_who_lost_tier_access(self):
        parent = self._comment(self.parent_author, body='Before downgrade')
        Notification.objects.all().delete()
        self.parent_author.tier = self.free_tier
        self.parent_author.save(update_fields=['tier'])

        self._comment(self.replier, parent=parent)

        notes = Notification.objects.filter(notification_type='content_comment')
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes.get().user, self.note_owner)

    def test_note_owner_parent_role_is_deduplicated_with_existing_title(self):
        parent = self._comment(self.note_owner, body='Owner comment')
        Notification.objects.all().delete()

        self._comment(self.replier, parent=parent)

        notes = Notification.objects.filter(
            user=self.note_owner,
            notification_type='content_comment',
        )
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes.get().title, 'New reply on your note')


@tag('core')
class ContentCommentBestEffortTest(TestCase):
    """A failure inside the notification path never fails the comment write."""

    @classmethod
    def setUpTestData(cls):
        cls.commenter = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.author = User.objects.create_user(
            email='author@test.com', password='pw',
        )
        cls.course = Course.objects.create(
            title='C', slug='c', status='published',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='M', slug='m', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='U', slug='u', sort_order=1,
            content_id=uuid.uuid4(),
        )
        cls.instructor = Instructor.objects.create(
            instructor_id='author',
            name='Author',
            status='published',
            user=cls.author,
        )
        cls.course.instructors.add(cls.instructor)

    def test_notification_url_failure_is_logged_and_still_creates_comment(self):
        from unittest.mock import patch

        from comments.models import Comment

        with self.assertLogs('comments.services', level='ERROR') as logs:
            with patch(
                'notifications.services.notification_service._content_comment_url',
                side_effect=RuntimeError('bad URL'),
            ):
                comment = create_comment(
                    content_id=self.unit.content_id,
                    user=self.commenter,
                    body='Still saved',
                )

        # The comment row survives even though notify raised.
        self.assertTrue(Comment.objects.filter(pk=comment.pk).exists())
        self.assertIn(str(comment.pk), '\n'.join(logs.output))
