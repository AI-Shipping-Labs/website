"""Give every AISL cohort its cb_curriculum.Cohort mirror (issue #1696).

Frozen copy of the rules in ``content.services.coursework_bridge`` (a
migration must not import code that may change later). Only cb_curriculum
cohort rows are created and ``content.Cohort.curriculum_cohort`` is set; no
existing AISL row is otherwise changed. Idempotent: a cohort that already has
a mirror is skipped.

The reverse removes the mirrors this created, keeping any that coursework
data already uses.
"""

from django.db import migrations
from django.utils import timezone
from django.utils.text import slugify

SLUG_MAX_LENGTH = 300


def _base_slug(cohort):
    if cohort.external_key:
        slug = slugify(cohort.external_key)
    elif cohort.mode == 'self_paced':
        slug = 'self-paced'
    else:
        slug = slugify(cohort.name)
    return slug[:SLUG_MAX_LENGTH] or f'cohort-{cohort.pk}'


def create_mirrors(apps, schema_editor):
    Cohort = apps.get_model('content', 'Cohort')
    CurriculumCohort = apps.get_model('cb_curriculum', 'Cohort')
    database = schema_editor.connection.alias
    today = timezone.localdate()

    taken = set(
        CurriculumCohort.objects.using(database).values_list('course_id', 'slug'),
    )
    cohorts = (
        Cohort.objects.using(database)
        .filter(curriculum_cohort__isnull=True)
        .order_by('pk')
    )
    for cohort in cohorts.iterator():
        slug = _base_slug(cohort)
        if (cohort.course_id, slug) in taken:
            suffix = f'-{cohort.pk}'
            slug = f'{slug[:SLUG_MAX_LENGTH - len(suffix)]}{suffix}'
        taken.add((cohort.course_id, slug))
        mirror = CurriculumCohort.objects.using(database).create(
            course_id=cohort.course_id,
            slug=slug,
            title=cohort.name,
            mode=cohort.mode,
            start_date=cohort.start_date,
            end_date=cohort.end_date,
            max_participants=cohort.max_participants,
            visible=cohort.is_active,
            finished=cohort.end_date is not None and cohort.end_date < today,
            project_passing_score=0,
        )
        Cohort.objects.using(database).filter(pk=cohort.pk).update(
            curriculum_cohort=mirror,
        )


def remove_mirrors(apps, schema_editor):
    Cohort = apps.get_model('content', 'Cohort')
    CurriculumCohort = apps.get_model('cb_curriculum', 'Cohort')
    database = schema_editor.connection.alias

    mirror_ids = list(
        Cohort.objects.using(database)
        .filter(curriculum_cohort__isnull=False)
        .values_list('curriculum_cohort_id', flat=True),
    )
    Cohort.objects.using(database).update(curriculum_cohort=None)
    unused = CurriculumCohort.objects.using(database).filter(
        pk__in=mirror_ids,
        enrollments__isnull=True,
        projects__isnull=True,
        homeworks__isnull=True,
    )
    CurriculumCohort.objects.using(database).filter(
        pk__in=list(unused.values_list('pk', flat=True)),
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('cb_coursework', '0006_project_module_commit_id_field'),
        ('content', '0081_cohort_curriculum_cohort'),
    ]

    operations = [
        migrations.RunPython(create_mirrors, remove_mirrors),
    ]
