# Generated for issue #951.

from django.db import migrations

# Alexey's Calendly booking link (provided 2026-06-13). Backfilled into the
# existing CallHost(slug='alexey') row only when its booking_url is still
# blank, so an operator-set value in Studio is never overwritten.
ALEXEY_SLUG = 'alexey'
ALEXEY_BOOKING_URL = 'https://calendly.com/dtc-alexey/ai-shipping-labs-call'


def backfill_alexey_booking_url(apps, schema_editor):
    CallHost = apps.get_model('community', 'CallHost')
    CallHost.objects.filter(slug=ALEXEY_SLUG, booking_url='').update(
        booking_url=ALEXEY_BOOKING_URL,
    )


def noop_reverse(apps, schema_editor):
    # No reverse: clearing the URL could discard an operator-set value and
    # the backfill is idempotent, so leave the data in place on rollback.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('community', '0011_bookedcall'),
    ]

    operations = [
        migrations.RunPython(backfill_alexey_booking_url, noop_reverse),
    ]
