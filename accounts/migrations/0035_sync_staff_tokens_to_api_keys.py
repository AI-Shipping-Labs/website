"""Bridge staff operator tokens from accounts.Token to community_base.api.APIKey."""

from django.db import migrations


def bridge_tokens_to_api_keys(apps, schema_editor):
    try:
        APIKey = apps.get_model("cb_api", "APIKey")
        Token = apps.get_model("accounts", "Token")
    except LookupError:
        return

    for token in Token.objects.select_related("user").all():
        if token.user and (token.user.is_staff or token.user.is_superuser):
            prefix = token.lookup_prefix
            if not prefix:
                continue
            if not APIKey.objects.filter(lookup_prefix=prefix).exists():
                APIKey.objects.create(
                    id=f"key_{token.id[:32]}",
                    user=token.user,
                    name=token.name or f"Staff Token {prefix[:8]}",
                    key_hash=token.key_hash,
                    lookup_prefix=prefix,
                    scopes=["*"],
                    kind="staff",
                )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0034_user_email_upper_index"),
        ("cb_api", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            bridge_tokens_to_api_keys,
            migrations.RunPython.noop,
        ),
    ]
