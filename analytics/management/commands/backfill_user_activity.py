"""Backfill historical ``UserActivity`` rows from existing data (issue #853).

``UserActivity`` is an additive timeline; this command does NOT mutate the
source-of-truth rows. It derives historical activity rows from existing
data (enrollments, registrations, join clicks, email clicks, signups) using
their existing timestamps so the timeline has chronologically-correct
history from day one.

Idempotent: a row is only created when no existing ``UserActivity`` already
matches ``(user, event_type, object_type, object_id, occurred_at)``, so
re-running creates no duplicates. Prints a per-event-type summary count.

Strategy A (confirmed in the issue): forward-writing instrumentation lives
in the request/webhook chokepoints; this command seeds history. ``payment``
and ``lesson_open`` have no reliable pre-feature source and are forward-only.

``resource_view`` (issue #773) is likewise FORWARD-ONLY: there is no
historical browsing source (we never logged content views before that
feature), so this command intentionally never creates ``resource_view``
rows. They accrue only from live content views going forward.
"""

from django.core.management.base import BaseCommand

from analytics.activity import (
    studio_course_url,
    studio_event_url,
)
from analytics.models import UserActivity


def _make_row(*, user_id, event_type, occurred_at, object_type='',
              object_id='', label='', target_url=''):
    """Build an unsaved ``UserActivity`` candidate."""
    return UserActivity(
        user_id=user_id,
        event_type=event_type,
        occurred_at=occurred_at,
        object_type=object_type,
        object_id=str(object_id or '')[:64],
        label=(label or '')[:255],
        target_url=(target_url or '')[:500],
    )


class Command(BaseCommand):
    help = 'Backfill historical UserActivity rows from existing data (idempotent).'

    def handle(self, *args, **options):
        candidates = []
        candidates.extend(self._signup_rows())
        candidates.extend(self._course_enroll_rows())
        candidates.extend(self._event_register_rows())
        candidates.extend(self._event_join_rows())
        candidates.extend(self._email_click_rows())

        created_by_type = {}
        for row in candidates:
            if row.occurred_at is None or row.user_id is None:
                continue
            exists = UserActivity.objects.filter(
                user_id=row.user_id,
                event_type=row.event_type,
                object_type=row.object_type,
                object_id=row.object_id,
                occurred_at=row.occurred_at,
            ).exists()
            if exists:
                continue
            row.save()
            created_by_type[row.event_type] = (
                created_by_type.get(row.event_type, 0) + 1
            )

        self.stdout.write(self.style.SUCCESS('Backfill complete.'))
        total = 0
        for event_type, _label in UserActivity.EVENT_TYPE_CHOICES:
            count = created_by_type.get(event_type, 0)
            total += count
            self.stdout.write(f'  {event_type}: {count}')
        self.stdout.write(self.style.SUCCESS(f'Total created: {total}'))

    def _signup_rows(self):
        from django.contrib.auth import get_user_model

        from analytics.models import UserAttribution

        rows = []
        attributed_user_ids = set()
        for attr in UserAttribution.objects.select_related('user').iterator():
            occurred_at = (
                getattr(attr.user, 'date_joined', None) or attr.created_at
            )
            attributed_user_ids.add(attr.user_id)
            rows.append(_make_row(
                user_id=attr.user_id,
                event_type=UserActivity.EVENT_SIGNUP,
                occurred_at=occurred_at,
                label='Signed up',
            ))

        # Users without an attribution row still get a signup from
        # date_joined.
        User = get_user_model()
        no_attr = (
            User.objects
            .exclude(pk__in=attributed_user_ids)
            .values_list('pk', 'date_joined')
        )
        for pk, date_joined in no_attr.iterator():
            rows.append(_make_row(
                user_id=pk,
                event_type=UserActivity.EVENT_SIGNUP,
                occurred_at=date_joined,
                label='Signed up',
            ))
        return rows

    def _course_enroll_rows(self):
        from content.models import CohortEnrollment, Enrollment

        rows = []
        for enrollment in (
            Enrollment.objects.select_related('course').iterator()
        ):
            course = enrollment.course
            rows.append(_make_row(
                user_id=enrollment.user_id,
                event_type=UserActivity.EVENT_COURSE_ENROLL,
                occurred_at=enrollment.enrolled_at,
                object_type='course',
                object_id=course.slug,
                label=f'Enrolled in course: {course.title}',
                target_url=studio_course_url(course.pk),
            ))

        for cohort_enroll in (
            CohortEnrollment.objects
            .select_related('cohort', 'cohort__course')
            .iterator()
        ):
            course = cohort_enroll.cohort.course
            rows.append(_make_row(
                user_id=cohort_enroll.user_id,
                event_type=UserActivity.EVENT_COURSE_ENROLL,
                occurred_at=cohort_enroll.enrolled_at,
                object_type='course',
                object_id=course.slug,
                label=f'Enrolled in course: {course.title}',
                target_url=studio_course_url(course.pk),
            ))
        return rows

    def _event_register_rows(self):
        from events.models import EventRegistration

        rows = []
        for reg in EventRegistration.objects.select_related('event').iterator():
            event = reg.event
            rows.append(_make_row(
                user_id=reg.user_id,
                event_type=UserActivity.EVENT_EVENT_REGISTER,
                occurred_at=reg.registered_at,
                object_type='event',
                object_id=event.slug,
                label=f'Registered for event: {event.title}',
                target_url=studio_event_url(event.pk),
            ))
        return rows

    def _event_join_rows(self):
        from events.models import EventJoinClick

        rows = []
        for click in EventJoinClick.objects.select_related('event').iterator():
            event = click.event
            rows.append(_make_row(
                user_id=click.user_id,
                event_type=UserActivity.EVENT_EVENT_JOIN,
                occurred_at=click.clicked_at,
                object_type='event',
                object_id=event.slug,
                label=f'Joined event: {event.title}',
                target_url=studio_event_url(event.pk),
            ))
        return rows

    def _email_click_rows(self):
        from email_app.models import EmailLog

        rows = []
        clicked_logs = (
            EmailLog.objects
            .filter(clicked_at__isnull=False)
            .select_related('campaign')
        )
        for log in clicked_logs.iterator():
            subject = getattr(log.campaign, 'subject', '') or ''
            label = (
                f'Clicked email link: {subject}' if subject
                else 'Clicked email link'
            )
            rows.append(_make_row(
                user_id=log.user_id,
                event_type=UserActivity.EVENT_EMAIL_CLICK,
                occurred_at=log.clicked_at,
                object_type='email',
                object_id=str(log.pk),
                label=label,
            ))
        return rows
