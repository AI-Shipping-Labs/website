from django.db import migrations

CLOUDFLARE_WORKSHOP_REDIRECT = {
    'source_path': '/workshops/2026-06-17-cloudflare-workers-vectorize-agent',
    'target_path': '/workshops/cloudflare-workers-vectorize-agent',
    'redirect_type': 301,
    'is_active': True,
}


def seed_cloudflare_workshop_redirect(apps, schema_editor):
    Redirect = apps.get_model('integrations', 'Redirect')
    source_path = CLOUDFLARE_WORKSHOP_REDIRECT['source_path']
    defaults = {
        key: value
        for key, value in CLOUDFLARE_WORKSHOP_REDIRECT.items()
        if key != 'source_path'
    }
    Redirect.objects.update_or_create(source_path=source_path, defaults=defaults)


def unseed_cloudflare_workshop_redirect(apps, schema_editor):
    Redirect = apps.get_model('integrations', 'Redirect')
    Redirect.objects.filter(
        source_path=CLOUDFLARE_WORKSHOP_REDIRECT['source_path'],
        target_path=CLOUDFLARE_WORKSHOP_REDIRECT['target_path'],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0022_mavenenrollmentevent'),
    ]

    operations = [
        migrations.RunPython(
            seed_cloudflare_workshop_redirect,
            unseed_cloudflare_workshop_redirect,
        ),
    ]
