"""Tests for content-author comment notifications (issue #1341).

When a member posts a comment or reply on a course unit lesson or workshop
tutorial page, each distinct linked content author (``Instructor.user``)
receives one in-app ``content_comment`` notification, excluding the commenter.
"""

import uuid
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

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

    def test_reply_on_workshop_page_uses_reply_title(self):
        top = self._comment(self.page.content_id, body='First question')
        Notification.objects.all().delete()
        self._comment(self.page.content_id, parent=top, body='A reply')
        note = Notification.objects.get(notification_type='content_comment')
        self.assertEqual(note.user, self.author)
        self.assertEqual(note.title, 'New reply on Setup')
        self.assertTrue(note.url.endswith('/tutorial/setup#qa-section'))

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
class ContentCommentPlanExclusionTest(TestCase):
    """A plan-thread content_id resolves to no content -> no notification."""

    def test_plan_thread_content_id_creates_no_content_comment(self):
        from plans.models import Plan, Sprint

        member = User.objects.create_user(email='m@test.com', password='pw')
        sprint = Sprint.objects.create(
            name='May', slug='may', start_date=date(2026, 5, 1),
        )
        plan = Plan.objects.create(member=member, sprint=sprint)

        # Plan threads reuse the comments write path with the plan's
        # comment_content_id. That UUID matches neither Unit nor WorkshopPage.
        create_comment(
            content_id=plan.comment_content_id,
            user=member,
            body='Plan note',
        )
        self.assertEqual(
            Notification.objects.filter(
                notification_type='content_comment',
            ).count(),
            0,
        )


@tag('core')
class ContentCommentBestEffortTest(TestCase):
    """A failure inside the notification path never fails the comment write."""

    @classmethod
    def setUpTestData(cls):
        cls.commenter = User.objects.create_user(
            email='member@test.com', password='pw',
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

    def test_notification_failure_still_creates_comment(self):
        from unittest.mock import patch

        from comments.models import Comment

        with patch.object(
            NotificationService,
            'notify_content_comment',
            side_effect=RuntimeError('boom'),
        ):
            comment = create_comment(
                content_id=self.unit.content_id,
                user=self.commenter,
                body='Still saved',
            )

        # The comment row survives even though notify raised.
        self.assertTrue(Comment.objects.filter(pk=comment.pk).exists())
