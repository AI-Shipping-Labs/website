"""Studio app models.

Currently hosts the audit trail for the Studio AI assistant (issue #872).
The assistant turns a natural-language staff request into a proposed CRM
action; every EXECUTED action is recorded here so there is a durable trail
of who ran what, against whom, and with what outcome.
"""

from django.conf import settings
from django.db import models


class AssistantActionLog(models.Model):
    """Audit record of one EXECUTED Studio assistant action (issue #872).

    Written once per Confirm — never at propose time. The ``payload`` is
    the exact reviewed payload that was replayed at execute (the model is
    not re-invoked between propose and execute), so this row is a faithful
    record of what actually ran.
    """

    OUTCOME_SUCCESS = 'success'
    OUTCOME_ERROR = 'error'
    OUTCOME_CHOICES = [
        (OUTCOME_SUCCESS, 'Success'),
        (OUTCOME_ERROR, 'Error'),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='Staff member who confirmed and executed the action.',
    )
    tool_name = models.CharField(
        max_length=64,
        help_text='Which assistant tool ran (e.g. add_member_note).',
    )
    payload = models.JSONField(
        default=dict,
        help_text='The exact reviewed payload that was executed.',
    )
    target_member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='The member the action targeted, when resolved.',
    )
    target_email = models.CharField(
        max_length=254,
        blank=True,
        default='',
        help_text='Email the action targeted (kept even if user is gone).',
    )
    outcome = models.CharField(
        max_length=10,
        choices=OUTCOME_CHOICES,
    )
    message = models.TextField(
        blank=True,
        default='',
        help_text='Human-readable result or error detail.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['tool_name']),
        ]

    def __str__(self):
        return f'AssistantActionLog({self.tool_name}, {self.outcome})'
