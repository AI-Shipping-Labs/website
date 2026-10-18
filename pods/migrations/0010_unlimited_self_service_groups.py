from django.db import migrations, models
from django.utils import timezone

REVIEWED_PURPOSES = {
    'Group 01 — Evidence-backed finance': 'Share progress on finance projects grounded in traceable evidence.',
    'Group 02 — Security investigations': 'Compare approaches to security research and investigation workflows.',
    'Group 03 — Technical agents': 'Build and review agents for technical workflows.',
    'Group 04 — Product and record matching': 'Exchange ideas on product discovery and record-matching systems.',
    'Group 05 — Everyday and solar energy insights': (
        'Explore assistants for everyday decisions and solar-energy insights.'
    ),
    'Group 06 — Knowledge from documents and conversations': (
        'Build tools that extract knowledge from documents and conversations.'
    ),
    'Group 07 — Business data agents': 'Develop agents for business data analysis and decision support.',
    'Group 08 — Hobby knowledge assistants': 'Build useful knowledge assistants around personal interests.',
    'Group 09 — Clinical knowledge': 'Explore grounded assistants for clinical knowledge workflows.',
    'Group 10 — Environmental and humanitarian analysis': (
        'Apply retrieval and agents to environmental and humanitarian analysis.'
    ),
    'Group 11 — Specialist knowledge in products': 'Build specialist product-knowledge assistants.',
    'Group 12 — Expertise and customer workflows': 'Apply domain expertise to customer and support workflows.',
    'Group 13 — Foundations and weekly demos': 'Work through course foundations and share weekly demos.',
    'Group 14 — UK study partners': 'Coordinate course progress and project feedback in UK-friendly hours.',
    'Group 15 — Europe and Southern Africa': 'Coordinate course progress across Europe and Southern Africa.',
    'Group 16 — RAG and agent project check-ins': (
        'Share RAG and agent project progress through regular check-ins.'
    ),
    'Group 17 — Australia study partners': (
        'Coordinate course progress and project feedback in Australia-friendly hours.'
    ),
    'Group 18 — Americas study partners': (
        'Coordinate course progress and project feedback across the Americas.'
    ),
    'Group 19 — US Central and Eastern': (
        'Coordinate course progress in US Central and Eastern time zones.'
    ),
    'Group 20 — US Central study partners': 'Coordinate course progress in US Central-friendly hours.',
    'Group 21 — First check-in and scheduling': 'Start with a shared check-in and agree a working schedule.',
    'Group 22 — Project exchange': 'Exchange project feedback and unblock one another.',
    'Group 23 — Project exchange': 'Exchange project feedback and unblock one another.',
}


def preserve_notes_and_retire_requests(apps, schema_editor):
    Pod = apps.get_model('pods', 'Pod')
    PodJoinRequest = apps.get_model('pods', 'PodJoinRequest')
    target = Pod.objects.filter(
        cohort__course__slug='ai-buildcamp',
        cohort__external_key='4',
    )
    for title, purpose in REVIEWED_PURPOSES.items():
        matches = list(target.filter(name=title))
        if len(matches) > 1:
            raise RuntimeError(f'Expected at most one ai-buildcamp Cohort 4 group named {title}.')
        if not matches:
            continue
        pod = matches[0]
        pod.internal_notes = pod.purpose
        pod.purpose = purpose
        pod.scheduling_note = ''
        pod.save(update_fields=['internal_notes', 'purpose', 'scheduling_note'])

    PodJoinRequest.objects.filter(status__in=('pending', 'waitlisted')).update(
        status='cancelled',
        decided_at=timezone.now(),
    )


class Migration(migrations.Migration):
    dependencies = [('pods', '0009_artifact_size_bytes')]

    operations = [
        migrations.AddField(
            model_name='pod',
            name='internal_notes',
            field=models.TextField(blank=True, db_default='', default='', max_length=2000),
        ),
        migrations.AddField(
            model_name='pod',
            name='scheduling_note',
            field=models.TextField(blank=True, db_default='', default='', max_length=500),
        ),
        migrations.RunPython(preserve_notes_and_retire_requests, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='pod',
            name='owner',
            field=models.ForeignKey(
                blank=True,
                help_text='Optional member responsible for this study group.',
                null=True,
                on_delete=models.SET_NULL,
                related_name='owned_pods',
                to='accounts.user',
            ),
        ),
        migrations.AlterField(
            model_name='pod',
            name='purpose',
            field=models.TextField(max_length=160),
        ),
        migrations.AlterField(
            model_name='podmembership',
            name='source',
            field=models.CharField(
                choices=[
                    ('creator', 'Creator'),
                    ('member', 'Member'),
                    ('request', 'Request'),
                    ('staff', 'Staff'),
                    ('api', 'API'),
                ],
                max_length=20,
            ),
        ),
    ]
