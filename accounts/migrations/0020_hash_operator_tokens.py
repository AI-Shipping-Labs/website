"""Hash operator API tokens and remove plaintext credential storage."""

import uuid

from django.contrib.auth.hashers import make_password
from django.db import migrations, models

import accounts.models.token

LOOKUP_PREFIX_LENGTH = 24


def backfill_operator_token_hashes(apps, schema_editor):
    Token = apps.get_model("accounts", "Token")
    plaintext_keys = list(Token.objects.values_list("key", flat=True))
    for plaintext_key in plaintext_keys:
        if not plaintext_key:
            raise ValueError("Cannot hash a blank operator API token row.")

        Token.objects.filter(key=plaintext_key).update(
            key=uuid.uuid4().hex,
            key_hash=make_password(plaintext_key),
            lookup_prefix=plaintext_key[:LOOKUP_PREFIX_LENGTH],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0019_user_dashboard_dismissals"),
    ]

    operations = [
        migrations.AddField(
            model_name="token",
            name="key_hash",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="token",
            name="lookup_prefix",
            field=models.CharField(blank=True, db_index=True, default="", max_length=32),
        ),
        migrations.RunPython(
            backfill_operator_token_hashes,
            migrations.RunPython.noop,
        ),
        migrations.RenameField(
            model_name="token",
            old_name="key",
            new_name="id",
        ),
        migrations.AlterField(
            model_name="token",
            name="id",
            field=models.CharField(
                default=accounts.models.token.generate_token_identifier,
                editable=False,
                max_length=64,
                primary_key=True,
                serialize=False,
            ),
        ),
        migrations.AlterField(
            model_name="token",
            name="key_hash",
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name="token",
            name="lookup_prefix",
            field=models.CharField(db_index=True, max_length=32),
        ),
    ]
