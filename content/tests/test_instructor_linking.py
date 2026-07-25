"""Instructor account auto-linking (issue #1345).

Covers the auto-link rule, the ``reconcile_instructors`` summary, the
``link_instructors`` management command (dry-run + idempotent), the
comment-bearing content count, and a #1341 regression: a comment on a
freshly-linked instructor's content produces a ``content_comment``
notification.
"""

import uuid
from datetime import date
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
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
from content.services.instructor_linking import (
    auto_link_instructor,
    comment_content_count,
    reconcile_instructors,
)
from notifications.models import Notification

User = get_user_model()


@tag('core')
class AutoLinkRuleTest(TestCase):
    """The core fill-only, verified-email-only auto-link rule."""

    @classmethod
    def setUpTestData(cls):
        cls.ada_user = User.objects.create_user(
            email='ada@test.com', password='pw', email_verified=True,
        )

    def test_links_unlinked_instructor_to_verified_match(self):
        ada = Instructor.objects.create(
            instructor_id='ada', name='Ada', email='ada@test.com',
        )
        self.assertEqual(auto_link_instructor(ada), 'linked')
        ada.refresh_from_db()
        self.assertEqual(ada.user, self.ada_user)

    def test_iexact_case_insensitive_match(self):
        ada = Instructor.objects.create(
            instructor_id='ada', name='Ada', email='ADA@Test.com',
        )
        self.assertEqual(auto_link_instructor(ada), 'linked')
        ada.refresh_from_db()
        self.assertEqual(ada.user, self.ada_user)

    def test_never_overwrites_operator_override(self):
        other = User.objects.create_user(
            email='ben-work@test.com', password='pw', email_verified=True,
        )
        ben = Instructor.objects.create(
            instructor_id='ben', name='Ben', email='ben-personal@test.com',
            user=other,
        )
        # A verified account matching ben-personal@test.com exists...
        User.objects.create_user(
            email='ben-personal@test.com', password='pw', email_verified=True,
        )
        self.assertEqual(auto_link_instructor(ben), 'already_linked')
        ben.refresh_from_db()
        self.assertEqual(ben.user, other)

    def test_empty_email_is_not_linked(self):
        eve = Instructor.objects.create(instructor_id='eve', name='Eve')
        self.assertEqual(auto_link_instructor(eve), 'no_email')
        eve.refresh_from_db()
        self.assertIsNone(eve.user_id)

    def test_unverified_account_is_not_linked(self):
        User.objects.create_user(
            email='cleo@test.com', password='pw', email_verified=False,
        )
        cleo = Instructor.objects.create(
            instructor_id='cleo', name='Cleo', email='cleo@test.com',
        )
        self.assertEqual(auto_link_instructor(cleo), 'no_match')
        cleo.refresh_from_db()
        self.assertIsNone(cleo.user_id)

    def test_no_matching_account_is_not_linked(self):
        dane = Instructor.objects.create(
            instructor_id='dane', name='Dane', email='dane@test.com',
        )
        self.assertEqual(auto_link_instructor(dane), 'no_match')
        dane.refresh_from_db()
        self.assertIsNone(dane.user_id)


@tag('core')
class ReconcileSummaryTest(TestCase):
    """``reconcile_instructors`` counts and idempotency."""

    def test_summary_counts_and_links(self):
        User.objects.create_user(
            email='ada@test.com', password='pw', email_verified=True,
        )
        Instructor.objects.create(
            instructor_id='ada', name='Ada', email='ada@test.com',
        )
        Instructor.objects.create(
            instructor_id='dane', name='Dane', email='dane@test.com',
        )

        summary = reconcile_instructors()
        self.assertEqual(summary['scanned'], 2)
        self.assertEqual(summary['newly_linked'], 1)
        self.assertEqual(summary['left_unlinked'], 1)
        self.assertEqual(summary['no_match'], 1)
        self.assertTrue(
            Instructor.objects.get(instructor_id='ada').user_id is not None
        )

    def test_dry_run_writes_nothing(self):
        User.objects.create_user(
            email='ada@test.com', password='pw', email_verified=True,
        )
        Instructor.objects.create(
            instructor_id='ada', name='Ada', email='ada@test.com',
        )
        summary = reconcile_instructors(dry_run=True)
        self.assertEqual(summary['newly_linked'], 1)
        self.assertIsNone(
            Instructor.objects.get(instructor_id='ada').user_id
        )

    def test_second_run_is_idempotent(self):
        User.objects.create_user(
            email='ada@test.com', password='pw', email_verified=True,
        )
        Instructor.objects.create(
            instructor_id='ada', name='Ada', email='ada@test.com',
        )
        reconcile_instructors()
        second = reconcile_instructors()
        self.assertEqual(second['newly_linked'], 0)
        self.assertEqual(second['already_linked'], 1)


@tag('core')
class LinkInstructorsCommandTest(TestCase):
    """``manage.py link_instructors`` output and behavior."""

    def test_command_links_and_reports_summary(self):
        User.objects.create_user(
            email='ada@test.com', password='pw', email_verified=True,
        )
        Instructor.objects.create(
            instructor_id='ada', name='Ada', email='ada@test.com',
        )
        out = StringIO()
        call_command('link_instructors', stdout=out)
        output = out.getvalue()
        self.assertIn('scanned:        1', output)
        self.assertIn('newly linked: 1', output)
        self.assertIsNotNone(
            Instructor.objects.get(instructor_id='ada').user_id
        )

    def test_command_dry_run_writes_nothing(self):
        User.objects.create_user(
            email='ada@test.com', password='pw', email_verified=True,
        )
        Instructor.objects.create(
            instructor_id='ada', name='Ada', email='ada@test.com',
        )
        out = StringIO()
        call_command('link_instructors', '--dry-run', stdout=out)
        self.assertIn('would link: 1', out.getvalue())
        self.assertIsNone(
            Instructor.objects.get(instructor_id='ada').user_id
        )


@tag('core')
class CommentContentCountTest(TestCase):
    """Comment-bearing content count across course units + workshop pages."""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = Instructor.objects.create(
            instructor_id='dane', name='Dane', email='dane@test.com',
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )

        cls.course = Course.objects.create(
            title='C', slug='c', status='published',
        )
        module = Module.objects.create(
            course=cls.course, title='M', slug='m', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=module, title='U', slug='u', sort_order=1,
            content_id=uuid.uuid4(),
        )
        cls.course.instructors.add(cls.instructor)

        cls.workshop = Workshop.objects.create(
            title='W', slug='w', date=date(2025, 1, 1), status='published',
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='p', title='P', sort_order=1,
            body='b', content_id=uuid.uuid4(),
        )
        cls.workshop.instructors.add(cls.instructor)

    def test_counts_only_content_with_comments(self):
        self.assertEqual(comment_content_count(self.instructor), 0)
        create_comment(
            content_id=self.unit.content_id, user=self.member, body='q',
        )
        self.assertEqual(comment_content_count(self.instructor), 1)
        create_comment(
            content_id=self.page.content_id, user=self.member, body='q',
        )
        self.assertEqual(comment_content_count(self.instructor), 2)

    def test_multiple_comments_on_one_item_count_once(self):
        create_comment(
            content_id=self.unit.content_id, user=self.member, body='q1',
        )
        create_comment(
            content_id=self.unit.content_id, user=self.member, body='q2',
        )
        self.assertEqual(comment_content_count(self.instructor), 1)


@tag('core')
class NotificationAfterLinkRegressionTest(TestCase):
    """#1341 regression: a comment reaches the freshly-linked author."""

    def test_command_link_enables_content_comment_notification(self):
        ada_user = User.objects.create_user(
            email='ada@test.com', password='pw', email_verified=True,
        )
        commenter = User.objects.create_user(
            email='main@test.com', password='pw',
        )
        workshop = Workshop.objects.create(
            title='RAG', slug='rag', date=date(2025, 1, 1),
            status='published',
        )
        page = WorkshopPage.objects.create(
            workshop=workshop, slug='setup', title='Setup', sort_order=1,
            body='b', content_id=uuid.uuid4(),
        )
        instructor = Instructor.objects.create(
            instructor_id='ada', name='Ada', email='ada@test.com',
            status='published',
        )
        workshop.instructors.add(instructor)

        # Before linking: no notification target.
        call_command('link_instructors')
        instructor.refresh_from_db()
        self.assertEqual(instructor.user, ada_user)

        create_comment(
            content_id=page.content_id, user=commenter, body='A question',
        )
        note = Notification.objects.get(notification_type='content_comment')
        self.assertEqual(note.user, ada_user)
        self.assertTrue(note.url.endswith('#qa-section'))
