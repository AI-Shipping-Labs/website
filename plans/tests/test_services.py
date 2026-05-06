"""Unit tests for ``plans.services`` (issue #444).

The service helper ``create_plan_for_enrollment`` is the single
source of truth for the empty-plan + sprint-enrollment artefacts
behind both the existing ``studio_plan_create`` view and the new
``studio_sprint_add_member`` view. Tests assert the artefact shape
(week count, theme blank, draft status, private visibility) and the
three idempotency cases listed in the docstring.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import Checkpoint, Plan, Sprint, SprintEnrollment
from plans.services import create_plan_for_enrollment

User = get_user_model()


class CreatePlanForEnrollmentTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint_6 = Sprint.objects.create(
            name='6w', slug='six-w',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6,
        )
        cls.sprint_4 = Sprint.objects.create(
            name='4w', slug='four-w',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=4,
        )

    def test_creates_plan_with_one_empty_week_per_duration_week(self):
        plan, enrollment, created = create_plan_for_enrollment(
            sprint=self.sprint_6, user=self.member, enrolled_by=self.staff,
        )
        self.assertTrue(created)
        weeks = list(plan.weeks.all().order_by('week_number'))
        self.assertEqual(len(weeks), 6)
        self.assertEqual([w.week_number for w in weeks], [1, 2, 3, 4, 5, 6])
        self.assertEqual([w.position for w in weeks], [0, 1, 2, 3, 4, 5])
        # Every week has a blank theme. No stub / TBD copy.
        for w in weeks:
            self.assertEqual(w.theme, '')
        # Zero checkpoints across all weeks.
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=plan).count(),
            0,
        )

    def test_plan_defaults_status_draft_and_visibility_private(self):
        plan, _, _ = create_plan_for_enrollment(
            sprint=self.sprint_6, user=self.member, enrolled_by=self.staff,
        )
        self.assertEqual(plan.status, 'draft')
        self.assertEqual(plan.visibility, 'private')

    def test_creates_sprint_enrollment_with_enrolled_by(self):
        _plan, enrollment, _ = create_plan_for_enrollment(
            sprint=self.sprint_6, user=self.member, enrolled_by=self.staff,
        )
        self.assertEqual(enrollment.sprint_id, self.sprint_6.pk)
        self.assertEqual(enrollment.user_id, self.member.pk)
        self.assertEqual(enrollment.enrolled_by_id, self.staff.pk)

    def test_4_week_sprint_yields_4_weeks(self):
        plan, _, _ = create_plan_for_enrollment(
            sprint=self.sprint_4, user=self.member, enrolled_by=self.staff,
        )
        self.assertEqual(plan.weeks.count(), 4)

    def test_idempotent_when_plan_and_enrollment_already_exist(self):
        # First call creates the rows.
        plan_1, enrollment_1, created_1 = create_plan_for_enrollment(
            sprint=self.sprint_6, user=self.member, enrolled_by=self.staff,
        )
        self.assertTrue(created_1)

        # Add a checkpoint to verify the helper does NOT wipe data on
        # second call.
        week_1 = plan_1.weeks.get(week_number=1)
        Checkpoint.objects.create(
            week=week_1, description='Read paper', position=0,
        )

        # Second call: same sprint, same user. Returns existing rows.
        plan_2, enrollment_2, created_2 = create_plan_for_enrollment(
            sprint=self.sprint_6, user=self.member, enrolled_by=self.staff,
        )
        self.assertEqual(plan_2.pk, plan_1.pk)
        self.assertEqual(enrollment_2.pk, enrollment_1.pk)
        self.assertFalse(created_2)
        # Existing data is preserved.
        self.assertEqual(
            Checkpoint.objects.filter(week=week_1).count(), 1,
        )
        # Still exactly one Plan and one SprintEnrollment for the pair.
        self.assertEqual(
            Plan.objects.filter(
                sprint=self.sprint_6, member=self.member,
            ).count(),
            1,
        )
        self.assertEqual(
            SprintEnrollment.objects.filter(
                sprint=self.sprint_6, user=self.member,
            ).count(),
            1,
        )

    def test_idempotent_when_only_enrollment_exists_creates_plan(self):
        # Pre-existing enrollment with no plan (the bulk-enroll-without-
        # plan case from #443).
        SprintEnrollment.objects.create(
            sprint=self.sprint_6, user=self.member, enrolled_by=self.staff,
        )
        self.assertEqual(
            Plan.objects.filter(
                sprint=self.sprint_6, member=self.member,
            ).count(),
            0,
        )

        plan, enrollment, created = create_plan_for_enrollment(
            sprint=self.sprint_6, user=self.member, enrolled_by=self.staff,
        )
        self.assertTrue(created)
        self.assertEqual(plan.weeks.count(), 6)
        # Did NOT create a duplicate enrollment row.
        self.assertEqual(
            SprintEnrollment.objects.filter(
                sprint=self.sprint_6, user=self.member,
            ).count(),
            1,
        )
        self.assertEqual(enrollment.user_id, self.member.pk)

    def test_idempotent_when_only_plan_exists_creates_enrollment(self):
        # Legacy / race case: plan exists but no enrollment row. The
        # signal in #443 normally back-creates the enrollment when a
        # plan is created; we delete it here to simulate pre-#443 data.
        plan_pre = Plan.objects.create(
            member=self.member, sprint=self.sprint_6, status='draft',
        )
        SprintEnrollment.objects.filter(
            sprint=self.sprint_6, user=self.member,
        ).delete()
        self.assertEqual(
            SprintEnrollment.objects.filter(
                sprint=self.sprint_6, user=self.member,
            ).count(),
            0,
        )

        plan, enrollment, created = create_plan_for_enrollment(
            sprint=self.sprint_6, user=self.member, enrolled_by=self.staff,
        )
        self.assertEqual(plan.pk, plan_pre.pk)
        self.assertFalse(created)
        # Enrollment was created with ``enrolled_by`` set.
        self.assertEqual(
            SprintEnrollment.objects.filter(
                sprint=self.sprint_6, user=self.member,
            ).count(),
            1,
        )
        self.assertEqual(enrollment.enrolled_by_id, self.staff.pk)

    def test_does_not_seed_resources_deliverables_or_next_steps(self):
        plan, _, _ = create_plan_for_enrollment(
            sprint=self.sprint_6, user=self.member, enrolled_by=self.staff,
        )
        self.assertEqual(plan.resources.count(), 0)
        self.assertEqual(plan.deliverables.count(), 0)
        self.assertEqual(plan.next_steps.count(), 0)
        # No checkpoints.
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=plan).count(),
            0,
        )

    def test_no_stub_themes_just_empty_strings(self):
        """Empty-state UX from #434 must NOT regress.

        Real bug it would catch: if someone replaces ``theme=''`` with
        ``theme=f'Week {n} -- TBD'`` to make the UI look populated, the
        empty-week-hint copy stops rendering and the operator must
        manually delete each placeholder.
        """
        plan, _, _ = create_plan_for_enrollment(
            sprint=self.sprint_6, user=self.member, enrolled_by=self.staff,
        )
        for week in plan.weeks.all():
            # Strict: not just ``not week.theme`` -- the literal
            # empty string. ``None`` would be a different bug.
            self.assertEqual(week.theme, '')
            self.assertNotIn('TBD', week.theme)
            self.assertNotIn('Week', week.theme)
