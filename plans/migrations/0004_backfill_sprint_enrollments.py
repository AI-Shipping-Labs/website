"""Backfill ``SprintEnrollment`` rows for every existing ``Plan`` (issue #443).

Before this migration, sprint membership was implied by plan-existence
(``Plan.objects.filter(sprint=..., member=...).exists()``). This issue
introduces ``SprintEnrollment`` as the authoritative membership row;
the ``post_save`` signal on ``Plan`` keeps that table in sync going
forward, but pre-existing plans need a one-shot backfill.

Idempotent via ``get_or_create`` on the unique ``(sprint, user)``
constraint, so re-running the migration (e.g. in --run-syncdb scenarios)
creates zero duplicates.
"""

from django.db import migrations


def backfill_enrollments(apps, schema_editor):
    Plan = apps.get_model('plans', 'Plan')
    SprintEnrollment = apps.get_model('plans', 'SprintEnrollment')
    for plan in Plan.objects.all().iterator():
        SprintEnrollment.objects.get_or_create(
            sprint_id=plan.sprint_id,
            user_id=plan.member_id,
            defaults={'enrolled_by': None},
        )


def remove_backfilled_enrollments(apps, schema_editor):
    """Reverse: drop every enrollment whose pair has a Plan.

    Conservative -- leaves enrollments that exist independently of any
    Plan (i.e. self-joined or bulk-enrolled) untouched. Reversibility
    here is defensive; in practice you would not roll this back.
    """
    Plan = apps.get_model('plans', 'Plan')
    SprintEnrollment = apps.get_model('plans', 'SprintEnrollment')
    for plan in Plan.objects.all().iterator():
        SprintEnrollment.objects.filter(
            sprint_id=plan.sprint_id, user_id=plan.member_id,
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('plans', '0003_sprint_min_tier_level_sprintenrollment'),
    ]

    operations = [
        migrations.RunPython(backfill_enrollments, remove_backfilled_enrollments),
    ]
