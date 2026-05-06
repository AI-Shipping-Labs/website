"""Model-level tests for the plans app.

Per `_docs/testing-guidelines.md` Rule 3, this module deliberately avoids
testing Django ORM round-trips (CharField save/read) and default
``on_delete=CASCADE`` behaviour. We DO test:

- Unique constraints (`(member, sprint)`, `(plan, week_number)`).
- The non-default ``on_delete=PROTECT`` on ``Plan.sprint``.
- Choice enforcement on ``Plan.status`` and ``InterviewNote.visibility``.
- Variable sprint duration (4 and 8 weeks) saving + validating cleanly.
"""

import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from plans.models import (
    InterviewNote,
    Plan,
    Sprint,
    Week,
)

User = get_user_model()


class PlanModelConstraintsTest(TestCase):
    """Constraints we add ourselves -- worth testing per Rule 3."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )

    def test_plan_unique_per_member_per_sprint(self):
        Plan.objects.create(member=self.member, sprint=self.sprint)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Plan.objects.create(member=self.member, sprint=self.sprint)

    def test_sprint_protects_plans_on_delete(self):
        """``on_delete=PROTECT`` is non-default behaviour.

        We use PROTECT so deleting a sprint with attached plans raises
        rather than silently destroys plans -- staff must reassign first.
        """
        Plan.objects.create(member=self.member, sprint=self.sprint)
        with self.assertRaises(ProtectedError):
            self.sprint.delete()

    def test_plan_status_choices_enforced(self):
        plan = Plan(member=self.member, sprint=self.sprint, status='template')
        with self.assertRaises(ValidationError):
            plan.full_clean()

        plan.status = 'shared'
        # Should validate cleanly. ``full_clean`` would raise otherwise.
        plan.full_clean()


class WeekModelConstraintsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        member = User.objects.create_user(email='m@test.com', password='pw')
        sprint = Sprint.objects.create(
            name='Sprint', slug='sprint',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(member=member, sprint=sprint)

    def test_week_unique_number_per_plan(self):
        Week.objects.create(plan=self.plan, week_number=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Week.objects.create(plan=self.plan, week_number=1)


class InterviewNoteModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(email='m@test.com', password='pw')

    def test_interview_note_visibility_choices_enforced(self):
        note = InterviewNote(
            member=self.member, body='b', visibility='public',
        )
        with self.assertRaises(ValidationError):
            note.full_clean()

        # Both real choices validate cleanly.
        note.visibility = 'internal'
        note.full_clean()
        note.visibility = 'external'
        note.full_clean()


class SprintDurationTest(TestCase):
    """The system must support variable-length sprints, not just 6 weeks."""

    def test_sprint_duration_weeks_accepts_4_and_8(self):
        """4-week and 8-week sprints save and validate cleanly.

        AC: ``no code outside the default hardcodes 6``. We exercise both
        ends of the realistic range to confirm the model accepts them.
        """
        sprint_4 = Sprint.objects.create(
            name='Short', slug='short',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=4,
        )
        sprint_4.full_clean()
        self.assertEqual(
            Sprint.objects.get(pk=sprint_4.pk).duration_weeks, 4,
        )

        sprint_8 = Sprint.objects.create(
            name='Long', slug='long',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=8,
        )
        sprint_8.full_clean()
        self.assertEqual(
            Sprint.objects.get(pk=sprint_8.pk).duration_weeks, 8,
        )


class SprintMinTierLevelTest(TestCase):
    def test_sprint_default_min_tier_level_is_main(self):
        sprint = Sprint.objects.create(
            name='Main Sprint',
            slug='main-sprint',
            start_date=datetime.date(2026, 5, 1),
        )

        self.assertEqual(sprint.min_tier_level, 20)

    def test_existing_explicit_premium_sprint_keeps_min_tier_level(self):
        sprint = Sprint.objects.create(
            name='Premium Sprint',
            slug='premium-sprint',
            start_date=datetime.date(2026, 5, 1),
            min_tier_level=30,
        )

        self.assertEqual(Sprint.objects.get(pk=sprint.pk).min_tier_level, 30)
