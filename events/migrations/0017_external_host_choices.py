"""Issue #579: constrain ``Event.external_host`` to known partners.

Schema step adds ``choices`` to the field. Data step normalizes
existing non-canonical values to the canonical casing for known
partners (Maven, Luma, DataTalksClub). Values that are not in the
known map are left as-is and printed to stdout so a staff member can
review them manually after the migration runs — never auto-cleared.

Idempotent: running on a DB where everything is already canonical is
a no-op.
"""

from django.db import migrations, models

CANONICAL_MAP = {
    'datatalks club': 'DataTalksClub',
    'datatalksclub': 'DataTalksClub',
    'datatalks.club': 'DataTalksClub',
    'dtc': 'DataTalksClub',
    'maven': 'Maven',
    'maven.com': 'Maven',
    'luma': 'Luma',
    'lu.ma': 'Luma',
}

CANONICAL_VALUES = {'', 'Maven', 'Luma', 'DataTalksClub'}


def normalize_external_host(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    qs = Event.objects.exclude(external_host='')
    for event in qs.only('pk', 'slug', 'external_host'):
        raw = event.external_host
        if raw in CANONICAL_VALUES:
            continue
        canonical = CANONICAL_MAP.get(raw.strip().lower())
        if canonical:
            Event.objects.filter(pk=event.pk).update(external_host=canonical)
            continue
        print(
            f"[external_host migration] Unknown value {raw!r} on event "
            f"slug={event.slug!r} — review manually"
        )


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0016_event_external_host'),
    ]

    operations = [
        migrations.RunPython(normalize_external_host, reverse_noop),
        migrations.AlterField(
            model_name='event',
            name='external_host',
            field=models.CharField(
                blank=True,
                choices=[
                    ('', 'Community-hosted'),
                    ('Maven', 'Maven'),
                    ('Luma', 'Luma'),
                    ('DataTalksClub', 'DataTalksClub'),
                ],
                default='',
                help_text=(
                    'Third-party host shown as a "Hosted on X" pill. '
                    'Supported: Maven, Luma, DataTalksClub. Leave blank '
                    'for community-hosted events.'
                ),
                max_length=100,
            ),
        ),
    ]
