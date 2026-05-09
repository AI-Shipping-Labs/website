# Generated manually for issue #554.

from django.db import migrations


def delete_member_plan_editor_tokens(apps, schema_editor):
    Token = apps.get_model("accounts", "Token")
    Token.objects.filter(name="member-plan-editor").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_user_soft_bounce_count"),
    ]

    operations = [
        migrations.RunPython(delete_member_plan_editor_tokens, migrations.RunPython.noop),
    ]
