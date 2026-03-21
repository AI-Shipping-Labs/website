"""Management command to form peer review batches for all eligible courses.

Scans all courses with peer_review_enabled=True and forms batches for:
- Cohort mode: cohorts past their end date with unassigned submissions
- Self-paced mode: enough waiting submissions to form a batch

Intended to be run as a cron job (e.g. every hour) or manually.
"""

from django.core.management.base import BaseCommand

from content.models import Course
from content.services.peer_review_service import PeerReviewService


class Command(BaseCommand):
    help = 'Form peer review batches for all courses with peer review enabled.'

    def handle(self, *args, **options):
        courses = Course.objects.filter(peer_review_enabled=True)

        if not courses.exists():
            self.stdout.write('No courses with peer review enabled.')
            return

        total_batched = 0
        total_reviews = 0

        for course in courses:
            result = PeerReviewService.form_batches_for_course(course)
            batched = result['batched']
            reviews = result['reviews_assigned']

            if batched > 0:
                self.stdout.write(
                    f'Course "{course.title}": formed batch of {batched} submissions, '
                    f'assigned {reviews} reviews'
                )
                total_batched += batched
                total_reviews += reviews

        if total_batched > 0:
            self.stdout.write(self.style.SUCCESS(
                f'Total: {total_batched} submissions batched, '
                f'{total_reviews} reviews assigned'
            ))
        else:
            self.stdout.write('No batches formed.')
