from datetime import time
from django.db import migrations


def seed_alexey_host(apps, schema_editor):
    BookingHost = apps.get_model("booking", "BookingHost")
    BookingWeeklyWindow = apps.get_model("booking", "BookingWeeklyWindow")

    host, _ = BookingHost.objects.update_or_create(
        slug="alexey",
        defaults={
            "name": "Alexey Grigorev",
            "email": "alexey@datatalks.club",
            "role_label": "Founder & 30-min meeting",
            "timezone": "Europe/Berlin",
            "is_active": True,
            "notice_hours": 12.0,
            "horizon_days": 60,
            "slot_duration_minutes": 30,
            "buffer_before_minutes": 0,
            "buffer_after_minutes": 0,
        },
    )

    # Monday through Friday (0 to 4), 10:00 - 18:00
    for weekday in range(5):
        BookingWeeklyWindow.objects.get_or_create(
            host=host,
            weekday=weekday,
            defaults={
                "start_time": time(10, 0),
                "end_time": time(18, 0),
                "is_active": True,
            },
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("booking", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_alexey_host, noop_reverse),
    ]
