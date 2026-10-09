"""Shared fixtures for pods tests (issue #1918)."""

import datetime

from django.utils import timezone

from accounts.models import User
from content.models import Cohort, CohortEnrollment, Course
from pods.models import AvailabilityProfile, AvailabilityWindow

COURSE_SLUG = 'pods-course'


def make_course(slug=COURSE_SLUG, title='Pods course', required_level=0):
    return Course.objects.create(
        title=title, slug=slug, status='published', required_level=required_level,
    )


def make_cohort(course, key='4', name='Cohort 4', mode='cohort'):
    today = timezone.localdate()
    if mode == 'cohort':
        return Cohort.objects.create(
            course=course, name=name, external_key=key, mode=mode,
            start_date=today - datetime.timedelta(days=7),
            end_date=today + datetime.timedelta(days=60),
        )
    return Cohort.objects.create(course=course, name=name, external_key=key, mode=mode)


def make_user(email, *, first_name='', last_name='', timezone_name='', staff=False):
    user = User.objects.create_user(
        email=email, password='testpass', email_verified=True,
        first_name=first_name, last_name=last_name,
    )
    fields = []
    if timezone_name:
        user.preferred_timezone = timezone_name
        fields.append('preferred_timezone')
    if staff:
        user.is_staff = True
        fields.append('is_staff')
    if fields:
        user.save(update_fields=fields)
    return user


def enroll(user, cohort):
    return CohortEnrollment.objects.create(user=user, cohort=cohort)


def set_windows(user, timezone_name, windows):
    """``windows``: iterable of ``(weekday, 'HH:MM', 'HH:MM', preference)``."""
    profile, _ = AvailabilityProfile.objects.update_or_create(
        user=user, defaults={'timezone': timezone_name},
    )
    profile.windows.all().delete()
    for weekday, start, end, *rest in windows:
        sh, sm = (int(x) for x in start.split(':'))
        eh, em = (int(x) for x in end.split(':'))
        AvailabilityWindow.objects.create(
            profile=profile, weekday=weekday,
            start_minute=sh * 60 + sm, end_minute=eh * 60 + em,
            preference=rest[0] if rest else 'preferred',
        )
    return profile


def make_pod(cohort, name, members=(), *, owner=None, meeting_count=4, meeting_minutes=60, source='studio', **extra):
    """A pod with ``members`` (issue #1919 meeting tests)."""
    from pods.models import Pod, PodMembership

    pod = Pod.objects.create(
        cohort=cohort, name=name, purpose=f'{name} purpose', owner=owner, source=source,
        meeting_count=meeting_count, meeting_minutes=meeting_minutes, **extra,
    )
    for user in members:
        PodMembership.objects.create(pod=pod, user=user, source='staff')
    return pod


def make_meeting(pod, starts_at, *, status='scheduled', zone='UTC', series_id=None, created_via='studio', **extra):
    from pods.models import PodMeeting

    return PodMeeting.objects.create(
        pod=pod, starts_at=starts_at, duration_minutes=pod.meeting_minutes, timezone=zone,
        status=status, series_id=series_id, created_via=created_via, **extra,
    )
