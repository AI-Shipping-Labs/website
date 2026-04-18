"""Backfill Enrollment rows for users with completed-unit history.

Issue #236. Before this change there was no Enrollment concept — the
dashboard inferred "in progress" from ``UserCourseProgress.completed_at``
rows. To preserve that signal when the dashboard switches to querying
Enrollments, we create one ``Enrollment`` per (user, course) pair where
the user has any completed unit on that course, with:

- ``source = 'auto_progress'``
- ``enrolled_at`` = the earliest completion timestamp on that course

Idempotent: skips pairs that already have an active enrollment, so
re-running the migration (or running it on a partially-populated DB)
will not create duplicates.

We can't use ``auto_now_add`` for the historical timestamp, so we set
``enrolled_at`` directly via ``update()`` after create.
"""

from django.db import migrations
from django.db.models import Min


def backfill_enrollments(apps, schema_editor):
    Enrollment = apps.get_model('content', 'Enrollment')
    UserCourseProgress = apps.get_model('content', 'UserCourseProgress')

    # For every (user, course) with at least one completed_at, find the earliest
    # completion timestamp. We aggregate via the ORM and avoid loading full
    # progress rows.
    pairs = (
        UserCourseProgress.objects
        .filter(completed_at__isnull=False)
        .values('user_id', 'unit__module__course_id')
        .annotate(first_completion=Min('completed_at'))
    )

    # Existing active enrollments — skip these so the migration is idempotent.
    existing_active = set(
        Enrollment.objects
        .filter(unenrolled_at__isnull=True)
        .values_list('user_id', 'course_id')
    )

    to_create = []
    for row in pairs:
        key = (row['user_id'], row['unit__module__course_id'])
        if key in existing_active:
            continue
        to_create.append(Enrollment(
            user_id=row['user_id'],
            course_id=row['unit__module__course_id'],
            source='auto_progress',
        ))

    if not to_create:
        return

    # bulk_create won't honor auto_now_add when we want to set enrolled_at
    # explicitly. Strategy: create rows, then update each row's enrolled_at
    # via a per-pair UPDATE keyed on (user_id, course_id, source).
    # bulk_create returns the new instances when supported by the DB.
    created = Enrollment.objects.bulk_create(to_create)

    # Map (user_id, course_id) -> first_completion for the UPDATE pass.
    first_by_pair = {
        (row['user_id'], row['unit__module__course_id']): row['first_completion']
        for row in pairs
    }

    # Update enrolled_at on each newly-created row.
    for enr in created:
        first = first_by_pair.get((enr.user_id, enr.course_id))
        if first is None:
            continue
        Enrollment.objects.filter(pk=enr.pk).update(enrolled_at=first)


def noop_reverse(apps, schema_editor):
    """Reverse: drop only the auto_progress enrollments we created.

    We can't perfectly distinguish backfilled rows from rows users created
    later, so we only delete auto_progress rows that have no completed
    units after their enrolled_at — but that's brittle. The safest reverse
    is a no-op; rolling back the schema migration drops the table anyway.
    """
    return


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0024_enrollment'),
    ]

    operations = [
        migrations.RunPython(backfill_enrollments, noop_reverse),
    ]
