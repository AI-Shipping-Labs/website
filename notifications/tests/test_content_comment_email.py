"""Tests for the issue #1895 author email on shared comments.

Linked course instructors and workshop authors get one transactional
``content_comment`` email per comment, mirroring the owner bell fan-out.
Member-owned threads (Book Club notes, sprint plans) and parent-comment
authors keep the in-app bell only. The durable delivery stores scalar
copy plus the comment relation; the worker mints the discussion URL.
"""

import uuid
from datetime import date
from unittest import mock

from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

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
from email_app.hooks import _resolve_content_comment_context
from email_app.services.context_guard import ensure_no_rendered_urls
from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    TRANSACTIONAL_EMAIL_TYPES,
    classify_email_type,
)
from email_app.services.email_rendering import render_template_parts
from email_app.services.preview_contexts import PREVIEW_CONTEXTS
from integrations.config import site_base_url
from notifications.models import Notification
from notifications.services.notification_service import NotificationService

User = get_user_model()


def deliveries():
    return EmailDelivery.objects.filter(purpose='content_comment')


@tag('core')
class ContentCommentEmailTest(TestCase):
    """Recipient set, dedup, self-email guard, durable context shape."""

    @classmethod
    def setUpTestData(cls):
        cls.commenter = User.objects.create_user(
            email='member@test.com', password='pw', first_name='Carol',
        )
        cls.author = User.objects.create_user(
            email='instructor@test.com', password='pw', first_name='Ada',
        )
        cls.author2 = User.objects.create_user(
            email='alice@test.com', password='pw',
        )
        cls.author3 = User.objects.create_user(
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

        # Co-taught workshop: Alice and Bob, with Bob reachable through
        # two instructor rows (the dedup-by-user case).
        cls.cotaught = Workshop.objects.create(
            title='Agents Workshop', slug='agents-workshop',
            date=date(2025, 2, 1), status='published',
        )
        cls.cotaught_page = WorkshopPage.objects.create(
            workshop=cls.cotaught, slug='patterns', title='Patterns',
            sort_order=1, body='Body', content_id=uuid.uuid4(),
        )
        cls.alice_link = Instructor.objects.create(
            instructor_id='alice', name='Alice', status='published',
            user=cls.author2,
        )
        cls.bob_link = Instructor.objects.create(
            instructor_id='bob', name='Bob', status='published',
            user=cls.author3,
        )
        cls.bob_dup_link = Instructor.objects.create(
            instructor_id='bob-2', name='Bob II', status='published',
            user=cls.author3,
        )
        cls.cotaught.instructors.add(
            cls.alice_link, cls.bob_link, cls.bob_dup_link,
        )

    def _comment(self, content_id, user=None, parent=None, body='A question'):
        return create_comment(
            content_id=content_id,
            user=user or self.commenter,
            body=body,
            parent=parent,
        )

    def test_comment_on_unit_queues_one_email_to_linked_instructor(self):
        comment = self._comment(
            self.unit.content_id, body='How should I count the tokens?',
        )

        rows = list(deliveries())
        self.assertEqual(len(rows), 1)
        delivery = rows[0]
        self.assertEqual(delivery.recipient_email, self.author.email)
        self.assertEqual(delivery.state, EmailDelivery.State.PENDING)
        self.assertEqual(
            delivery.idempotency_key,
            f'content_comment:{comment.pk}:{self.author.pk}',
        )
        self.assertEqual(delivery.related_object_type, 'comments.comment')
        self.assertEqual(str(delivery.related_object_id), str(comment.pk))
        self.assertEqual(delivery.context_data['verb'], 'comment')
        self.assertEqual(delivery.context_data['commenter_name'], 'Carol')
        self.assertEqual(
            delivery.context_data['comment_excerpt'],
            'How should I count the tokens?',
        )
        self.assertEqual(delivery.context_data['content_title'], 'Intro to ML')
        self.assertEqual(delivery.context_data['parent_title'], 'ML Zoomcamp')
        # Durable context stores scalar copy only — no discussion link.
        self.assertNotIn('discussion_url', delivery.context_data)
        ensure_no_rendered_urls('content_comment', delivery.context_data)

    def test_author_self_comment_queues_no_email_and_no_bell(self):
        self._comment(self.unit.content_id, user=self.author)
        self.assertEqual(deliveries().count(), 0)
        self.assertFalse(
            Notification.objects.filter(
                notification_type='content_comment',
            ).exists(),
        )

    def test_workshop_reply_queues_reply_email_to_author(self):
        top = self._comment(self.page.content_id, body='First question')
        Notification.objects.all().delete()
        deliveries().delete()

        self._comment(
            self.page.content_id,
            parent=top,
            body='Try the sample repo first.',
        )

        rows = list(deliveries())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].recipient_email, self.author.email)
        self.assertEqual(rows[0].context_data['verb'], 'reply')
        self.assertEqual(
            rows[0].context_data['comment_excerpt'],
            'Try the sample repo first.',
        )
        self.assertEqual(rows[0].context_data['content_title'], 'Setup')
        self.assertEqual(rows[0].context_data['parent_title'], 'RAG Workshop')

    def test_student_parent_author_gets_bell_not_email(self):
        top = self._comment(
            self.unit.content_id, user=self.asker, body='First question',
        )
        Notification.objects.all().delete()
        deliveries().delete()

        self._comment(self.unit.content_id, parent=top, body='A reply')

        rows = list(deliveries())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].recipient_email, self.author.email)
        self.assertFalse(
            deliveries().filter(recipient_email=self.asker.email).exists(),
        )
        bell = Notification.objects.get(user=self.asker)
        self.assertEqual(bell.title, 'New reply to your comment')

    def test_unlinked_instructor_queues_no_email(self):
        ghost_course = Course.objects.create(
            title='Ghost Course', slug='ghost-course-email',
            status='published',
        )
        ghost_module = Module.objects.create(
            course=ghost_course, title='Module 1', slug='ghost-module-1',
            sort_order=1,
        )
        ghost_unit = Unit.objects.create(
            module=ghost_module, title='Ghost Lesson', slug='ghost-lesson',
            sort_order=1, content_id=uuid.uuid4(),
        )
        ghost = Instructor.objects.create(
            instructor_id='ghost', name='Ghost', status='published',
        )
        ghost_course.instructors.add(ghost)

        self._comment(ghost_unit.content_id)

        self.assertEqual(deliveries().count(), 0)
        self.assertFalse(
            Notification.objects.filter(
                notification_type='content_comment',
            ).exists(),
        )

    def test_co_taught_content_emails_each_author_once(self):
        self._comment(
            self.cotaught_page.content_id,
            body='A shared question for both instructors.',
        )

        rows = deliveries()
        self.assertEqual(rows.count(), 2)
        self.assertEqual(
            set(rows.values_list('recipient_email', flat=True)),
            {self.author2.email, self.author3.email},
        )
        self.assertFalse(
            rows.filter(recipient_email=self.commenter.email).exists(),
        )

    def test_commenting_co_author_is_skipped(self):
        self._comment(self.cotaught_page.content_id, user=self.author2)

        rows = deliveries()
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows[0].recipient_email, self.author3.email)

    def test_repeat_delivery_for_same_comment_does_not_duplicate(self):
        comment = self._comment(self.unit.content_id)

        NotificationService.notify_content_comment(comment)

        self.assertEqual(deliveries().count(), 1)
        self.assertEqual(
            deliveries().get().idempotency_key,
            f'content_comment:{comment.pk}:{self.author.pk}',
        )

    def test_email_failure_does_not_block_comment_or_bell(self):
        with mock.patch(
            'notifications.services.notification_service.'
            'send_package_mail',
            side_effect=RuntimeError('ses down'),
        ):
            comment = self._comment(self.unit.content_id)

        self.assertTrue(Comment.objects.filter(pk=comment.pk).exists())
        self.assertTrue(
            Notification.objects.filter(user=self.author).exists(),
        )
        self.assertEqual(deliveries().count(), 0)

    def test_unsubscribed_linked_author_still_queued(self):
        self.author.unsubscribed = True
        self.author.save(update_fields=['unsubscribed'])

        self._comment(self.unit.content_id)

        row = deliveries().get()
        self.assertEqual(row.recipient_email, self.author.email)
        self.assertEqual(row.state, EmailDelivery.State.PENDING)

    def test_worker_mints_absolute_discussion_url_for_unit(self):
        self._comment(self.unit.content_id)
        delivery = deliveries().get()

        context = {}
        _resolve_content_comment_context(delivery, context)

        self.assertEqual(
            context['discussion_url'],
            f"{site_base_url().rstrip('/')}"
            '/courses/ml-zoomcamp/module-1/intro#qa-section',
        )

    def test_worker_mints_absolute_discussion_url_for_workshop(self):
        self._comment(self.page.content_id)
        delivery = deliveries().get()

        context = {}
        _resolve_content_comment_context(delivery, context)

        self.assertTrue(context['discussion_url'].startswith('https://'))
        self.assertTrue(context['discussion_url'].endswith('/setup#qa-section'))

    def test_worker_resolver_sets_greeting(self):
        self._comment(self.unit.content_id)
        delivery = deliveries().get()

        context = {}
        _resolve_content_comment_context(delivery, context)

        self.assertEqual(context['user_name'], 'Ada')

    def test_rendered_subject_matches_bell_and_body_has_cta(self):
        self._comment(
            self.unit.content_id, body='How should I count the tokens?',
        )
        delivery = deliveries().get()
        context = dict(delivery.context_data)
        _resolve_content_comment_context(delivery, context)

        subject, body_markdown, body_html, _footer = render_template_parts(
            'content_comment', self.author, context,
        )

        self.assertEqual(subject, 'New comment on Intro to ML')
        self.assertIn('Open the discussion', body_markdown)
        self.assertIn(context['discussion_url'], body_html)
        self.assertIn('Carol', body_markdown)
        self.assertIn('How should I count the tokens?', body_markdown)
        self.assertIn('ML Zoomcamp', body_markdown)
        self.assertNotIn(' -- ', body_markdown)
        self.assertNotIn('unsubscribe', body_markdown.lower())

    def test_rendered_reply_subject_matches_bell_title(self):
        top = self._comment(self.page.content_id, body='First question')
        deliveries().delete()
        self._comment(self.page.content_id, parent=top, body='A reply')
        delivery = deliveries().get()
        context = dict(delivery.context_data)
        _resolve_content_comment_context(delivery, context)

        subject, *_rest = render_template_parts(
            'content_comment', self.author, context,
        )

        self.assertEqual(subject, 'New reply on Setup')


@tag('core')
class HomeworkStepCommentEmailTest(TestCase):
    """Issue #1895 follows the #1897 owner fan-out for homework steps."""

    @classmethod
    def setUpTestData(cls):
        cls.commenter = User.objects.create_user(
            email='step-member@test.com', password='pw', first_name='Carol',
        )
        cls.author = User.objects.create_user(
            email='step-instructor@test.com', password='pw', first_name='Ada',
        )
        cls.course = Course.objects.create(
            title='Buildcamp', slug='buildcamp-notify-1897',
            status='published',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module 1', slug='module-1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Homework One', slug='hw1',
            sort_order=1, kind='homework', content_id=uuid.uuid4(),
        )
        cls.instructor = Instructor.objects.create(
            instructor_id='ada', name='Ada', status='published',
            user=cls.author,
        )
        cls.course.instructors.add(cls.instructor)
        cls._enable_stepper()

    @staticmethod
    def _enable_stepper():
        from datetime import timedelta

        from content.models.cohort import Cohort
        from content.models.homework import Homework, Question, QuestionType

        cohort = Cohort.objects.create(
            course=HomeworkStepCommentEmailTest.course,
            name='Cohort 4',
            start_date=timezone.now().date() - timedelta(days=1),
        )
        homework = Homework.objects.create(
            cohort=cohort, slug='hw1', title='Homework One',
            content_id=HomeworkStepCommentEmailTest.unit.content_id,
            stepper_enabled=True,
        )
        for source_question_id in ('q1-first', 'q2-reflect'):
            Question.objects.create(
                homework=homework, source_question_id=source_question_id,
                text='Q', question_type=QuestionType.FREE_FORM,
            )

    def _create_step_thread(self, step_slug):
        from content.models.homework import HomeworkStepThread
        from content.services.homework_step_threads import (
            step_thread_content_id,
        )

        return HomeworkStepThread.objects.create(
            unit_content_id=self.unit.content_id,
            step_slug=step_slug,
            content_id=step_thread_content_id(self.unit.content_id, step_slug),
        )

    def test_step_comment_queues_email_with_step_title_and_link(self):
        thread = self._create_step_thread('q2-reflect')

        create_comment(
            content_id=thread.content_id,
            user=self.commenter,
            body='Token question',
        )

        delivery = deliveries().get()
        self.assertEqual(delivery.recipient_email, self.author.email)
        self.assertEqual(
            delivery.context_data['content_title'],
            'Homework One — Question 2',
        )
        self.assertEqual(delivery.context_data['parent_title'], 'Buildcamp')
        context = {}
        _resolve_content_comment_context(delivery, context)
        self.assertTrue(
            context['discussion_url'].endswith('/q2-reflect#qa-section'),
        )


@tag('core')
class ContentCommentEmailExcludedSurfacesTest(TestCase):
    """Book Club note and sprint-plan owners keep the bell, never email."""

    @classmethod
    def setUpTestData(cls):
        from bookclub.models import Book, Chapter, Note
        from payments.models import Tier
        from plans.models import Plan, Sprint
        from tests.fixtures import create_user_with_membership

        main_tier = Tier.objects.get(slug='main')
        cls.note_owner = create_user_with_membership(
            email='note-owner@test.com', password='pw', tier=main_tier,
        )
        cls.note_commenter = User.objects.create_user(
            email='note-member@test.com', password='pw',
        )
        cls.book = Book.objects.create(
            title='Inference Engineering',
            slug='inference-engineering-email',
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

        cls.plan_owner = User.objects.create_user(
            email='plan-owner@test.com', password='pw',
        )
        cls.plan_commenter = User.objects.create_user(
            email='plan-member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='August Sprint', slug='august-sprint-email',
            start_date=date(2026, 8, 1),
        )
        cls.plan = Plan.objects.create(
            member=cls.plan_owner,
            sprint=cls.sprint,
            visibility='cohort',
            title='Ship the assistant',
        )

    def test_plan_comment_queues_no_email(self):
        create_comment(
            content_id=self.plan.comment_content_id,
            user=self.plan_commenter,
            body='Plan feedback',
        )

        self.assertEqual(deliveries().count(), 0)
        self.assertTrue(
            Notification.objects.filter(user=self.plan_owner).exists(),
        )

    def test_book_note_comment_queues_no_email(self):
        create_comment(
            content_id=self.note.comment_content_id,
            user=self.note_commenter,
            body='Book reply',
        )

        self.assertEqual(deliveries().count(), 0)
        self.assertTrue(
            Notification.objects.filter(user=self.note_owner).exists(),
        )


class ContentCommentEmailRegistrationTest(TestCase):
    """The template is classified and registered on every operator surface."""

    def test_template_is_registered_everywhere(self):
        from studio.views.email_templates import (
            TEMPLATE_DISPLAY_ORDER,
            TEMPLATE_SENT_WHEN,
        )

        self.assertIn('content_comment', TRANSACTIONAL_EMAIL_TYPES)
        self.assertEqual(
            classify_email_type('content_comment'),
            EMAIL_KIND_TRANSACTIONAL,
        )
        self.assertIn('content_comment', TEMPLATE_DISPLAY_ORDER)
        self.assertIn('content_comment', TEMPLATE_SENT_WHEN)
        preview = PREVIEW_CONTEXTS['content_comment']
        for key in (
            'user_name',
            'verb',
            'commenter_name',
            'comment_excerpt',
            'content_title',
            'parent_title',
            'discussion_url',
        ):
            self.assertIn(key, preview)
