"""CRM calls (issue #1949): when a call was, and what was discussed.

A :class:`Call` is the CRM-owned log of a conversation with a member.
It is deliberately separate from :class:`community.BookedCall` (the
Calendly webhook row): a booked call becomes a :class:`Call` only
through :func:`crm.services.calls.record_call`, the documented
integration point the booking app (issue #1948) writes to.

Staff-facing only: the Studio CRM surfaces render these rows; no
member-facing template or member API may render a :class:`Call`.
"""

from django.conf import settings
from django.db import models

from crm.models.crm_record import CRMRecord

#: Suggested values for :attr:`Call.source`. The field itself is
#: free-form (no ``choices`` constraint) so a new origin never needs a
#: migration; writers should stick to these tokens.
SOURCE_MANUAL = 'manual'
SOURCE_BOOKING = 'booking'
SOURCE_CALENDLY = 'calendly'
CALL_SOURCE_CHOICES = [
    (SOURCE_MANUAL, 'Manual'),
    (SOURCE_BOOKING, 'Booking'),
    (SOURCE_CALENDLY, 'Calendly'),
]


class Call(models.Model):
    """One logged conversation with a member.

    ``contact`` is nullable: the email resolves to a user
    case-insensitively and links only when that user is already tracked
    in the CRM (``CRMRecord`` is opt-in and never auto-created).
    ``email`` keeps the raw address either way, so a call recorded for
    an email that matches nobody (yet) still renders everywhere. When
    the writer supplies ``source_ref`` (booking confirmation id,
    Calendly event URI), rows upsert on ``(source, source_ref)`` -- the
    partial unique constraint below makes double delivery a
    database-level impossibility. Rows with a blank ``source_ref``
    (manual entries) never deduplicate against each other.
    """

    contact = models.ForeignKey(
        CRMRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='calls',
        help_text=(
            'The tracked CRM record, when the email matched a user who '
            'is already in the CRM. Never auto-created.'
        ),
    )
    email = models.EmailField(
        help_text='Raw email the call is associated with; always stored.',
    )
    occurred_at = models.DateTimeField(
        help_text='When the call was.',
    )
    summary = models.TextField(
        blank=True,
        default='',
        help_text='What was discussed. Staff-only; never shown to the member.',
    )
    source = models.CharField(
        default='manual',
        max_length=50,
        choices=CALL_SOURCE_CHOICES,
        help_text=(
            'Free-form origin token; ``manual`` for staff-logged calls, '
            '``booking`` for the booking app, ``calendly`` for the '
            'Calendly era.'
        ),
    )
    source_ref = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text=(
            'External id from the source system (e.g. booking '
            'confirmation id); enables the ``(source, source_ref)`` '
            'idempotent upsert. Blank for manual entries.'
        ),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='Staff member who logged the call, when known.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-occurred_at']
        indexes = [
            models.Index(
                fields=['occurred_at'], name='crmcall_occurred_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['source', 'source_ref'],
                condition=~models.Q(source_ref=''),
                name='crmcall_source_source_ref_uq',
            ),
        ]

    def __str__(self):
        who = self.contact.user.email if self.contact_id else self.email
        return f'Call({who}, {self.occurred_at:%Y-%m-%d %H:%M})'
