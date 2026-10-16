"""Schedule declaration for rolling self-paced review groups."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django_q.models import Schedule


class SelfPacedPeerReviewScheduleTest(TestCase):
    def test_schedule_runs_every_fifteen_minutes(self):
        call_command('setup_schedules', stdout=StringIO())

        schedule = Schedule.objects.get(name='self-paced-peer-review-batches')
        self.assertEqual(
            schedule.func,
            'content.tasks.peer_reviews.form_self_paced_review_batches',
        )
        self.assertEqual(schedule.cron, '*/15 * * * *')
