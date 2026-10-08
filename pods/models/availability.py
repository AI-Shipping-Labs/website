"""Weekly availability, one profile per member (issue #1918).

Windows are local wall-clock times in ``AvailabilityProfile.timezone``
(an IANA zone name). They are never stored as UTC or as a fixed offset:
the suggestion service expands them per concrete date so DST changes are
handled per member and per date.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

__all__ = [
    'AVAILABILITY_PREFERENCE_CHOICES',
    'MAX_WINDOWS_PER_PROFILE',
    'PREFERENCE_IF_NEEDED',
    'PREFERENCE_PREFERRED',
    'WEEKDAY_NAMES',
    'AvailabilityProfile',
    'AvailabilityWindow',
]

PREFERENCE_PREFERRED = 'preferred'
PREFERENCE_IF_NEEDED = 'if_needed'
AVAILABILITY_PREFERENCE_CHOICES = [
    (PREFERENCE_PREFERRED, 'Works well'),
    (PREFERENCE_IF_NEEDED, 'If needed'),
]

MAX_WINDOWS_PER_PROFILE = 21

WEEKDAY_NAMES = (
    'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday',
)


class AvailabilityProfile(models.Model):
    """A member's weekly availability, reused across every pod."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='availability_profile',
    )
    timezone = models.CharField(max_length=64)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user} ({self.timezone})'


class AvailabilityWindow(models.Model):
    """One weekly window: weekday plus local start/end minute of the day."""

    profile = models.ForeignKey(
        AvailabilityProfile,
        on_delete=models.CASCADE,
        related_name='windows',
    )
    weekday = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(6)],
        help_text='0 = Monday ... 6 = Sunday.',
    )
    start_minute = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(1410)],
    )
    end_minute = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(30), MaxValueValidator(1440)],
    )
    preference = models.CharField(
        max_length=20,
        choices=AVAILABILITY_PREFERENCE_CHOICES,
        default=PREFERENCE_PREFERRED,
    )

    class Meta:
        ordering = ['weekday', 'start_minute', 'pk']

    def __str__(self):
        return f'{WEEKDAY_NAMES[self.weekday]} {self.start_minute}-{self.end_minute}'
