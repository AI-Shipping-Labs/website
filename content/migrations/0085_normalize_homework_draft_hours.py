from django.db import migrations

from content.utils.hours import normalize_hours_text

HOURS_KEYS = ('time_spent_lectures', 'time_spent_homework')
ASSIGNMENT_KEY_PREFIX = 'aisl:homework:'


def normalize_draft_hours(apps, schema_editor):
    """Rewrite old ``"2.0"``-style hour strings in AISL homework drafts.

    Issue #1923: the accepted snapshot now formats stored hours canonically
    (``2`` not ``2.0``). The shared package compares a draft against that
    snapshot by string equality, so drafts seeded with the old ``str(float)``
    format would otherwise show a false "Saved changes are a draft" banner.
    Only numeric strings change; blanks and non-numeric input are left as
    typed. Re-running is a no-op because canonical values map to themselves.
    """
    HomeworkDraft = apps.get_model('cb_homework_steps', 'HomeworkDraft')
    drafts = HomeworkDraft.objects.filter(
        assignment_key__startswith=ASSIGNMENT_KEY_PREFIX,
    ).only('pk', 'final_fields')
    for draft in drafts.iterator():
        fields = draft.final_fields
        if not isinstance(fields, dict):
            continue
        changed = False
        for key in HOURS_KEYS:
            if key not in fields:
                continue
            normalized = normalize_hours_text(fields[key])
            if normalized != fields[key]:
                fields[key] = normalized
                changed = True
        if changed:
            # ``update`` keeps ``saved_at`` (auto_now) untouched.
            HomeworkDraft.objects.filter(pk=draft.pk).update(final_fields=fields)


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0084_projectsubmission_shared_form_fields'),
        ('cb_homework_steps', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(normalize_draft_hours, migrations.RunPython.noop),
    ]
