"""Tests for the Member Q&A builder behind the CRM contact record.

Issue #1949: visible comments across course-unit / homework-step /
workshop threads surface with context labels and public thread links;
hidden comments are excluded; the section caps at 20 with a total.
"""

import datetime
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from comments.models import Comment
from content.models import Course, Module, Unit, Workshop, WorkshopPage
from crm.services.member_qa import QA_LIMIT, recent_member_qa
from plans.models import Plan, Sprint

User = get_user_model()


class RecentMemberQATest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='qa-member@test.com', password='pw',
        )
        cls.course = Course.objects.create(
            content_id=uuid.uuid4(), title='QA Hero', slug='qa-hero',
            status='published', required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Day 1', slug='qa-day-1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            content_id=uuid.uuid4(), module=cls.module,
            title='Frontmatter', slug='qa-frontmatter', sort_order=1,
        )
        cls.workshop = Workshop.objects.create(
            content_id=uuid.uuid4(), title='QA Agents', slug='qa-agents',
            date=datetime.date(2026, 10, 1), status='published',
            landing_required_level=0, pages_required_level=0,
            recording_required_level=0,
        )
        cls.page = WorkshopPage.objects.create(
            content_id=uuid.uuid4(), workshop=cls.workshop,
            title='Setup', slug='qa-setup', sort_order=1,
        )
        cls.sprint = Sprint.objects.create(
            name='QA sprint', slug='qa-sprint',
            start_date=datetime.date(2026, 10, 1),
            duration_weeks=6, status='active',
        )
        cls.plan = Plan.objects.create(
            member=cls.member,
            sprint=cls.sprint,
            comment_content_id=uuid.uuid4(),
        )

    def _comment(self, content_id, body, parent=None, hidden=False):
        comment = Comment.objects.create(
            content_id=content_id,
            user=self.member,
            parent=parent,
            body=body,
        )
        if hidden:
            comment.hidden_at = timezone_now()
            comment.save(update_fields=['hidden_at'])
        return comment

    def test_empty_member_has_no_rows(self):
        loner = User.objects.create_user(
            email='qa-loner@test.com', password='pw',
        )
        context = recent_member_qa(loner)
        self.assertEqual(context['rows'], [])
        self.assertEqual(context['total'], 0)

    def test_surfaces_course_and_workshop_comments_with_context(self):
        question = self._comment(
            self.unit.content_id, 'How do I ship the checkpoint?',
        )
        self._comment(
            self.unit.content_id, 'Found it in the docs.', parent=question,
        )
        self._comment(
            self.page.content_id, 'Workshop setup worked for me.',
        )
        self._comment(self.plan.comment_content_id, 'Plan note')
        self._comment(uuid.uuid4(), 'Orphan comment')

        context = recent_member_qa(self.member)
        bodies = [row['snippet'] for row in context['rows']]
        self.assertIn('How do I ship the checkpoint?', bodies)
        self.assertIn('Found it in the docs.', bodies)
        self.assertIn('Workshop setup worked for me.', bodies)
        # Sprint-plan threads and unresolvable UUIDs stay out of the
        # section (course-unit / homework-step / workshop only).
        self.assertNotIn('Plan note', bodies)
        self.assertNotIn('Orphan comment', bodies)

        by_body = {row['snippet']: row for row in context['rows']}
        question_row = by_body['How do I ship the checkpoint?']
        answer_row = by_body['Found it in the docs.']
        workshop_row = by_body['Workshop setup worked for me.']

        self.assertTrue(question_row['is_question'])
        self.assertFalse(answer_row['is_question'])
        self.assertEqual(question_row['kind_label'], 'Course unit')
        self.assertEqual(workshop_row['kind_label'], 'Workshop')
        self.assertEqual(question_row['thread_title'], 'Frontmatter')
        self.assertTrue(question_row['thread_url'])
        self.assertIn(str(self.unit.slug), question_row['thread_url'])
        self.assertTrue(workshop_row['thread_url'])

    def test_hidden_comments_are_excluded(self):
        self._comment(self.unit.content_id, 'Visible question')
        self._comment(self.unit.content_id, 'Hidden spam', hidden=True)

        context = recent_member_qa(self.member)
        bodies = [row['snippet'] for row in context['rows']]
        self.assertIn('Visible question', bodies)
        self.assertNotIn('Hidden spam', bodies)

    def test_capped_at_limit_with_total_count(self):
        for index in range(QA_LIMIT + 5):
            self._comment(self.unit.content_id, f'Question {index}')
        context = recent_member_qa(self.member)
        self.assertEqual(len(context['rows']), QA_LIMIT)
        self.assertEqual(context['total'], QA_LIMIT + 5)
        created = [row['created_at'] for row in context['rows']]
        self.assertEqual(created, sorted(created, reverse=True))


def timezone_now():
    from django.utils import timezone
    return timezone.now()
