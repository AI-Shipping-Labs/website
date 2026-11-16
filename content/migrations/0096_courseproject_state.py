from django.db import migrations, models
from django.utils import timezone


def backfill_state(apps, schema_editor):
    """Map existing attempts to the CMP state matching their current behaviour."""
    CourseProject = apps.get_model('content', 'CourseProject')
    PeerReview = apps.get_model('content', 'PeerReview')
    now = timezone.now()
    for project in CourseProject.objects.select_related('cohort'):
        if project.cohort_id and project.cohort.mode == 'self_paced':
            continue
        window_over = project.review_due_at is not None and project.review_due_at <= now
        assigned = PeerReview.objects.filter(submission__course_project=project).exists()
        if window_over:
            state = 'CO'
        elif assigned:
            state = 'PR'
        else:
            continue
        CourseProject.objects.filter(pk=project.pk).update(state=state)


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0095_courseextension_project_criteria_intro_outro'),
    ]

    operations = [
        migrations.AddField(
            model_name='courseproject',
            name='state',
            field=models.CharField(choices=[('CL', 'Closed'), ('CS', 'Collecting submissions'), ('PR', 'Peer reviewing'), ('CO', 'Completed')], db_default='CS', default='CS', max_length=2),
        ),
        migrations.RunPython(backfill_state, migrations.RunPython.noop),
    ]
