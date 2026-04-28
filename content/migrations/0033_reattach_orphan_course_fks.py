"""Reattach orphan Course FKs to the live published row (issue #366).

Before the sync fix in this issue, a Course rename (slug changed but
``content_id`` stable) or a cross-repo move could end up as TWO Course
rows that share a ``content_id``: a stale ``draft`` row (the original)
and a newly-created ``published`` row at the new slug. Any
``Enrollment``, ``CourseAccess``, ``Cohort``, or ``UserCourseProgress``
rows that pointed at the original kept pointing at the orphan, so the
dashboard's Continue Learning widget kept building URLs against the
old slug.

This one-shot migration walks every draft Course with a non-null
``content_id`` and, if a published sibling shares that ``content_id``,
moves enrollments / access / cohorts / per-unit progress over and
deletes the orphan. ``UserCourseProgress`` rows are matched
unit-by-unit via ``Unit.content_id``: an orphan unit with no match in
the destination keeps its progress rows attached to the orphan (and is
removed by the cascade when the orphan course is deleted, which is the
correct outcome — that unit no longer exists). A WARNING is logged for
every such unmatched unit so operators can audit losses.

Idempotent: a second run finds no draft rows that share content_id
with a published row (the first run deleted them) so it short-circuits.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def reattach_orphan_course_fks(apps, schema_editor):
    Course = apps.get_model('content', 'Course')
    Unit = apps.get_model('content', 'Unit')
    Enrollment = apps.get_model('content', 'Enrollment')
    CourseAccess = apps.get_model('content', 'CourseAccess')
    UserCourseProgress = apps.get_model('content', 'UserCourseProgress')
    Cohort = apps.get_model('content', 'Cohort')

    drafts = Course.objects.filter(
        status='draft',
    ).exclude(content_id__isnull=True)

    for orphan in drafts:
        target = Course.objects.filter(
            content_id=orphan.content_id,
            status='published',
        ).exclude(pk=orphan.pk).first()
        if target is None:
            # No live sibling — leave the draft alone. It may legitimately
            # represent a course the operator removed from the content
            # repo; keeping the row preserves user history.
            continue

        Enrollment.objects.filter(course=orphan).update(course=target)
        CourseAccess.objects.filter(course=orphan).update(course=target)
        Cohort.objects.filter(course=orphan).update(course=target)

        target_units_by_cid = {
            u.content_id: u
            for u in Unit.objects.filter(
                module__course=target,
            ).exclude(content_id__isnull=True)
        }

        orphan_units = Unit.objects.filter(module__course=orphan)
        for unit in orphan_units:
            target_unit = (
                target_units_by_cid.get(unit.content_id)
                if unit.content_id is not None else None
            )
            if target_unit is None:
                if UserCourseProgress.objects.filter(unit=unit).exists():
                    logger.warning(
                        'Course %s (pk=%s): orphan unit %s '
                        '(content_id=%s) has no match in target course '
                        '%s; leaving UserCourseProgress rows attached '
                        'to the orphan unit.',
                        orphan.slug, orphan.pk, unit.slug,
                        unit.content_id, target.slug,
                    )
                continue
            if target_unit.pk == unit.pk:
                continue
            UserCourseProgress.objects.filter(unit=unit).update(
                unit=target_unit,
            )

        # Delete the orphan course. CASCADE removes any leftover modules
        # and units that had no content_id match — those rows are no
        # longer reachable from any course slug, so keeping them around
        # would leave dead rows in the DB.
        orphan.delete()


def noop_reverse(apps, schema_editor):
    """Reverse is a no-op: we cannot recreate the deleted draft rows."""


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0032_backfill_instructors'),
    ]

    operations = [
        migrations.RunPython(
            reattach_orphan_course_fks,
            reverse_code=noop_reverse,
        ),
    ]
