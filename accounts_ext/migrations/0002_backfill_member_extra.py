"""Copy the contact-tag relation onto ``accounts_ext.MemberExtra`` (A3.2, #1692).

One ``MemberExtra`` row per user (its primary key is the user id), then every
``accounts_user_contact_tags`` row copied verbatim into the new through table.
Nothing is deleted: ``User.contact_tags`` stays populated for the expand
window, and ``accounts.utils.tags`` dual-writes both relations until the
contract unit lands.

Reverse: delete every ``MemberExtra`` row, which cascades the copied through
rows away. The legacy relation is untouched in both directions, so the reverse
loses no data.
"""

from django.db import migrations

CHUNK_SIZE = 1000


def _chunks(iterable, size):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def copy_forward(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    MemberExtra = apps.get_model("accounts_ext", "MemberExtra")
    alias = schema_editor.connection.alias

    existing = set(MemberExtra.objects.using(alias).values_list("user_id", flat=True))
    missing = (
        User.objects.using(alias)
        .exclude(pk__in=existing)
        .order_by("pk")
        .values_list("pk", flat=True)
        .iterator(chunk_size=CHUNK_SIZE)
    )
    for batch in _chunks(missing, CHUNK_SIZE):
        MemberExtra.objects.using(alias).bulk_create(
            [MemberExtra(user_id=user_id) for user_id in batch],
            ignore_conflicts=True,
            batch_size=CHUNK_SIZE,
        )

    legacy_through = User.contact_tags.through
    new_through = MemberExtra.contact_tags.through
    rows = (
        legacy_through.objects.using(alias)
        .order_by("pk")
        .values_list("user_id", "contacttag_id")
        .iterator(chunk_size=CHUNK_SIZE)
    )
    for batch in _chunks(rows, CHUNK_SIZE):
        new_through.objects.using(alias).bulk_create(
            [
                new_through(memberextra_id=user_id, contacttag_id=tag_id)
                for user_id, tag_id in batch
            ],
            ignore_conflicts=True,
            batch_size=CHUNK_SIZE,
        )


def copy_backward(apps, schema_editor):
    MemberExtra = apps.get_model("accounts_ext", "MemberExtra")
    alias = schema_editor.connection.alias
    MemberExtra.contact_tags.through.objects.using(alias).all().delete()
    MemberExtra.objects.using(alias).all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts_ext", "0001_initial"),
        ("accounts", "0030_move_contacttag_and_accountsession"),
    ]

    operations = [
        migrations.RunPython(copy_forward, copy_backward),
    ]
