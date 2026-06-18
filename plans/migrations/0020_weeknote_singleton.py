from django.conf import settings
from django.db import migrations, models


def _author_label(author):
    if author is None:
        return "unknown author"
    email = getattr(author, "email", "") or ""
    return email or f"user {author.pk}"


def _timestamp_label(note):
    value = note.created_at or note.updated_at
    return value.isoformat() if value else "unknown time"


def _folded_body(canonical, older_notes, authors_by_id):
    body = canonical.body or ""
    if not older_notes:
        return body

    sections = [body.rstrip(), "Earlier notes"]
    for note in older_notes:
        author = authors_by_id.get(note.author_id)
        sections.append(
            "\n".join(
                [
                    f"[{_timestamp_label(note)} by {_author_label(author)}]",
                    note.body or "",
                ]
            ).rstrip()
        )
    return "\n\n".join(part for part in sections if part)


def collapse_duplicate_week_notes(apps, schema_editor):
    WeekNote = apps.get_model("plans", "WeekNote")
    user_app_label, user_model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(user_app_label, user_model_name)

    duplicate_week_ids = (
        WeekNote.objects.values("week_id")
        .annotate(note_count=models.Count("id"))
        .filter(note_count__gt=1)
        .values_list("week_id", flat=True)
    )

    for week_id in duplicate_week_ids:
        notes = list(
            WeekNote.objects.filter(week_id=week_id).order_by(
                "-updated_at",
                "-created_at",
                "-id",
            )
        )
        canonical = notes[0]
        older_notes = sorted(
            notes[1:],
            key=lambda note: (
                note.created_at or note.updated_at,
                note.updated_at or note.created_at,
                note.id,
            ),
        )
        author_ids = {
            note.author_id for note in older_notes if note.author_id is not None
        }
        authors_by_id = {
            author.pk: author
            for author in User.objects.filter(pk__in=author_ids)
        }

        canonical.body = _folded_body(canonical, older_notes, authors_by_id)
        canonical.save(update_fields=["body"])
        WeekNote.objects.filter(pk__in=[note.pk for note in older_notes]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("plans", "0019_nextstep_kind"),
    ]

    operations = [
        migrations.RunPython(collapse_duplicate_week_notes, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="weeknote",
            constraint=models.UniqueConstraint(
                fields=("week",), name="unique_week_note_per_week"
            ),
        ),
    ]
