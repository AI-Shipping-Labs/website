"""Database models for the Studio CRM (issue #560).

The CRM is a curated, opt-in dataset: a user is in the CRM iff a
:class:`CRMRecord` row exists for them. Staff create the row explicitly
via the ``Track in CRM`` button on the user profile.

Notes, plans, and sprint enrollment all keep their existing foreign keys
to ``User`` — the CRM record is the lens, not the owner. Member-facing
code MUST NOT render any of the staff-only fields on this model
(``persona``, ``summary``, ``next_steps``) or any
:class:`CRMExperiment` row.
"""

from django.conf import settings
from django.db import models

STATUS_CHOICES = [
    ('active', 'Active'),
    ('archived', 'Archived'),
]

EXPERIMENT_STATUS_CHOICES = [
    ('running', 'Running'),
    ('completed', 'Completed'),
    ('abandoned', 'Abandoned'),
]


class CRMRecord(models.Model):
    """A staff-curated CRM record for an engaged member.

    One CRM record per user. The :class:`User` table is the canonical
    user list; this table is the engaged subset staff actively follow.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='crm_record',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active',
    )
    persona = models.CharField(
        max_length=120,
        blank=True,
        default='',
        help_text=(
            'Free-text persona label, e.g. '
            '"Sam — The Technical Professional Moving to AI". '
            'Lives on the relationship so it survives across sprints.'
        ),
    )
    summary = models.TextField(
        blank=True,
        default='',
        help_text=(
            'Short staff summary of who this person is and why we are '
            'tracking them. Staff-only; never rendered to the member.'
        ),
    )
    next_steps = models.TextField(
        blank=True,
        default='',
        help_text=(
            'What is next for this member. Staff-only; never rendered '
            'to the member.'
        ),
    )

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'CRMRecord({self.user.email})'


class CRMExperiment(models.Model):
    """A lightweight experiment we are running with a tracked member.

    Stored on the CRM record so staff can capture what they are trying
    with a specific member ("pair-program 1h/week", "review their open
    source eval framework"). Staff-only — never rendered to members.
    """

    crm_record = models.ForeignKey(
        CRMRecord,
        on_delete=models.CASCADE,
        related_name='experiments',
    )
    title = models.CharField(max_length=200)
    hypothesis = models.TextField(blank=True, default='')
    result = models.TextField(blank=True, default='')
    status = models.CharField(
        max_length=20,
        choices=EXPERIMENT_STATUS_CHOICES,
        default='running',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'CRMExperiment({self.crm_record.user.email}: {self.title})'
