"""Backfill CRMRecord rows for every user with at least one InterviewNote.

The CRM list (``/studio/crm/``) is the engaged-by-staff subset of users.
This migration runs once on deploy so existing engagement (member notes)
shows up on the CRM list without staff having to re-track each user.

- Users with notes but no plans still get a CRM record (notes alone are
  evidence of engagement).
- Users with plans but NO notes do NOT get an auto-created record. The
  rationale lives in the spec: plans can be member-self-created and we
  don't want self-onboarded plans to pollute the CRM list.

Idempotent: the ``OneToOneField`` on ``CRMRecord.user`` prevents
duplicates, and the ``filter(user__crm_record__isnull=True)`` clause
makes the migration a no-op on subsequent runs.
"""

from django.db import migrations


def backfill_crm_records_from_notes(apps, schema_editor):
    InterviewNote = apps.get_model('plans', 'InterviewNote')
    CRMRecord = apps.get_model('crm', 'CRMRecord')

    member_ids = (
        InterviewNote.objects
        .filter(member__crm_record__isnull=True)
        .values_list('member_id', flat=True)
        .distinct()
    )
    new_records = [
        CRMRecord(user_id=member_id, status='active')
        for member_id in member_ids
    ]
    if new_records:
        CRMRecord.objects.bulk_create(new_records, ignore_conflicts=True)


def noop_reverse(apps, schema_editor):
    """Reverse is a no-op — we cannot tell which CRM records were
    created by this backfill vs created later by staff. Leave them in
    place on rollback."""
    return


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0001_initial'),
        ('plans', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(
            backfill_crm_records_from_notes,
            noop_reverse,
        ),
    ]
