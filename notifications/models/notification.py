from django.conf import settings
from django.db import models

NOTIFICATION_TYPE_CHOICES = [
    ('new_content', 'New Content'),
    ('event_reminder', 'Event Reminder'),
    ('announcement', 'Announcement'),
    # Plan request from a sprint member who has no plan yet (issue #585).
    # Created for every active staff user when an enrolled member with
    # no plan clicks "Ask the team to plan with me" on the cohort board.
    ('plan_request', 'Plan Request'),
    # Issue #732: fired when staff shares a sprint plan with the member
    # (Studio button OR PATCH /api/plans/<id>/ with {"shared_at": ...}).
    # Targets exactly one user (the plan owner) — no tier fan-out.
    ('plan_shared', 'Plan Shared'),
    # Sprint-end progress recap for enrolled members with shared plans.
    ('sprint_recap', 'Sprint Recap'),
    # Issue #882: fired when a member submits the onboarding form (or
    # finishes the AI onboarding chat). Created for every active staff
    # user, mirroring the plan-request fan-out. Links to the member's CRM
    # record when tracked, else the Django admin user-change page.
    ('onboarding_submitted', 'Onboarding Submitted'),
    ('sprint_week_start', 'Sprint Week Start'),
    ('week_note_prompt', 'Week Note Prompt'),
    ('slack_progress', 'Slack Progress'),
]


class Notification(models.Model):
    """On-platform notification for a user.

    user is nullable for broadcast notifications (e.g. Slack channel posts
    that don't target a specific user).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications',
        help_text='Target user. Null for broadcast notifications.',
    )
    title = models.CharField(max_length=300)
    body = models.TextField(blank=True, default='')
    url = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text='Link to the content (e.g. /blog/new-article).',
    )
    notification_type = models.CharField(
        max_length=20,
        choices=NOTIFICATION_TYPE_CHOICES,
        default='new_content',
    )
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['user', 'read']),
        ]

    def __str__(self):
        return f'{self.title} ({self.notification_type})'


class EventReminderLog(models.Model):
    """Tracks which event reminders / follow-ups have been sent to avoid duplicates.

    Each (event, user, interval) triple should only generate one notification.

    Known interval values:

    - ``24h`` and ``20m``: pre-event reminder bells/emails (issue #706).
    - ``followup``: post-event recap email (issue #680). One row per
      (event, user) — the per-user task uses ``get_or_create`` so a
      re-fired cron or a manual "Send follow-up now" press never
      double-sends.
    - ``24h_slack``: per-event guard for the 24h channel Slack
      announcement (issue #887). ``user`` is NULL — one row per event,
      so the announcement posts at most once even though the 24h cron
      window is wider than the 15-min tick interval.
    """

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='reminder_logs',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='event_reminder_logs',
        null=True,
        blank=True,
        help_text=(
            'NULL for per-event guard rows (e.g. the "24h_slack" channel '
            'announcement marker, issue #887). Set for per-user reminders.'
        ),
    )
    interval = models.CharField(
        max_length=16,
        help_text=(
            'Reminder interval: "24h" or "20m" for pre-event reminders, '
            '"followup" for the post-event recap email (issue #680), '
            '"24h_slack" for the per-event channel announcement guard '
            '(issue #887).'
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('event', 'user', 'interval')]

    def __str__(self):
        return f'{self.user} - {self.event} ({self.interval})'
