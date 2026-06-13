from django.db import migrations


def heal_series_draft_published(apps, schema_editor):
    """Reconcile contradictory draft+published series occurrences (#878).

    Any SERIES occurrence (``event_series__isnull=False``) left in the
    contradictory ``status='draft'`` + ``published=True`` state by the old
    bulk creator is reset to ``published=False`` with ``published_at=None``.
    ``status`` stays the single source of truth for public visibility and is
    left unchanged. Standalone (non-series) events are NOT touched — this
    issue removes the contradiction for series occurrences only and does not
    redefine ``published`` for the past/recordings page.
    """
    Event = apps.get_model("events", "Event")
    Event.objects.filter(
        event_series__isnull=False,
        status="draft",
        published=True,
    ).update(published=False, published_at=None)


def noop_reverse(apps, schema_editor):
    """Irreversible: there is no record of which rows we healed."""


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0031_backfill_title_is_auto"),
    ]

    operations = [
        migrations.RunPython(heal_series_draft_published, noop_reverse),
    ]
