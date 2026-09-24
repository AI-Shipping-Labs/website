"""Give existing courses a cohort when no cohort was synced."""

from django.db import migrations


def create_missing_course_cohorts(apps, schema_editor):
    Cohort = apps.get_model('content', 'Cohort')
    Course = apps.get_model('cb_curriculum', 'Course')
    database = schema_editor.connection.alias

    course_ids_with_cohorts = Cohort.objects.using(database).values_list(
        'course_id', flat=True,
    ).distinct()
    missing_course_ids = Course.objects.using(database).exclude(
        pk__in=course_ids_with_cohorts,
    ).values_list('pk', flat=True)

    Cohort.objects.using(database).bulk_create([
        Cohort(
            course_id=course_id,
            name='Self-paced',
            start_date=None,
            end_date=None,
            mode='self_paced',
            external_key='',
        )
        for course_id in missing_course_ids.iterator()
    ])


class Migration(migrations.Migration):
    dependencies = [
        ('content', '0077_homework_homework_url_field_and_more'),
    ]

    operations = [
        migrations.RunPython(
            create_missing_course_cohorts,
            migrations.RunPython.noop,
        ),
    ]
