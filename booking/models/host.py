from django.db import models


class BookingHost(models.Model):
    """Staff host available for 1:1 public bookings."""

    name = models.CharField(max_length=150, default="Alexey Grigorev")
    slug = models.SlugField(unique=True, default="alexey")
    email = models.EmailField(default="alexey@datatalks.club")
    role_label = models.CharField(max_length=200, blank=True, default="30-min meeting")
    photo_url = models.CharField(max_length=500, blank=True, default="")
    timezone = models.CharField(max_length=64, default="Europe/Berlin")
    is_active = models.BooleanField(default=True)
    notice_hours = models.FloatField(
        default=12.0,
        help_text="Minimum notice required before a booking in hours.",
    )
    horizon_days = models.IntegerField(
        default=60,
        help_text="How many days into the future booking is offered.",
    )
    slot_duration_minutes = models.IntegerField(default=30)
    buffer_before_minutes = models.IntegerField(default=0)
    buffer_after_minutes = models.IntegerField(default=0)
    daily_count_limit = models.IntegerField(null=True, blank=True)
    daily_minutes_limit = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.slug})"


WEEKDAY_CHOICES = [
    (0, "Monday"),
    (1, "Tuesday"),
    (2, "Wednesday"),
    (3, "Thursday"),
    (4, "Friday"),
    (5, "Saturday"),
    (6, "Sunday"),
]


class BookingWeeklyWindow(models.Model):
    """Recurring weekly availability window for a host."""

    host = models.ForeignKey(
        BookingHost,
        on_delete=models.CASCADE,
        related_name="weekly_windows",
    )
    weekday = models.IntegerField(choices=WEEKDAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["weekday", "start_time"]

    def __str__(self):
        return f"{self.host.slug} - {self.get_weekday_display()}: {self.start_time} - {self.end_time}"


class BookingDateOverride(models.Model):
    """Specific date override (closure or custom hours) for a host."""

    host = models.ForeignKey(
        BookingHost,
        on_delete=models.CASCADE,
        related_name="date_overrides",
    )
    date = models.DateField()
    is_unavailable = models.BooleanField(
        default=False,
        help_text="If True, the entire day is blocked.",
    )
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    class Meta:
        ordering = ["date", "start_time"]
        unique_together = [("host", "date")]

    def __str__(self):
        status = "Closed" if self.is_unavailable else f"{self.start_time}-{self.end_time}"
        return f"{self.host.slug} on {self.date}: {status}"
