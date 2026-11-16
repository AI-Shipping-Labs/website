from django.db import migrations

CRITERIA_REDIRECT = {
    'source_path': '/courses/ai-buildcamp/capstone/capstone-project/evaluation-criteria',
    'target_path': '/courses/ai-buildcamp/project-criteria',
    'redirect_type': 301,
    'is_active': True,
}


def seed_criteria_redirect(apps, schema_editor):
    Redirect = apps.get_model('integrations', 'Redirect')
    source_path = CRITERIA_REDIRECT['source_path']
    defaults = {
        'target_path': CRITERIA_REDIRECT['target_path'],
        'redirect_type': CRITERIA_REDIRECT['redirect_type'],
        'is_active': CRITERIA_REDIRECT['is_active'],
    }
    Redirect.objects.update_or_create(source_path=source_path, defaults=defaults)


def unseed_criteria_redirect(apps, schema_editor):
    Redirect = apps.get_model('integrations', 'Redirect')
    Redirect.objects.filter(
        source_path=CRITERIA_REDIRECT['source_path'],
        target_path=CRITERIA_REDIRECT['target_path'],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0035_rename_relay_sender_settings'),
    ]

    operations = [
        migrations.RunPython(seed_criteria_redirect, unseed_criteria_redirect),
    ]
