"""Generic content-completion tracking (issue #365).

A second, content-agnostic completion table. We did NOT replace
``UserCourseProgress`` — that model has ~145 references across views,
admin, signals, payments retention, integrations, and migrations, and
swapping it for a generic table in a single issue is high-risk for the
v1 user-visible value (workshops in Continue Learning).

Instead, we introduced this small additive table for new content types
that share the "user marked this thing finished" mechanic:

- ``content_type`` is a string discriminator (e.g. ``'workshop_page'``).
  We deliberately avoid Django's ``GenericForeignKey`` so queries stay
  flat and indexable.
- ``object_id`` is the integer PK of the underlying row. We store the
  PK rather than the slug so renaming/re-slugging a page does not orphan
  completion history (per the #365/#366 coordination decision).

Reads/writes flow through ``content/services/completion.py`` so views
never touch this table directly. The service dispatches on the input
class — ``Unit`` -> ``UserCourseProgress``, ``WorkshopPage`` -> this
table — keeping the public API symmetric across the two content types.
"""

from django.conf import settings
from django.db import models

# The choices list is intentionally small. Every new value here is a
# new dispatch branch in ``content/services/completion.py`` and should
# go through review — the service raises ``TypeError`` for any
# non-handled item class so we cannot silently start writing rows for a
# new content type without wiring up the read paths too.
CONTENT_TYPE_WORKSHOP_PAGE = 'workshop_page'

CONTENT_TYPE_CHOICES = [
    (CONTENT_TYPE_WORKSHOP_PAGE, 'Workshop page'),
]


class UserContentCompletion(models.Model):
    """A row indicating ``user`` finished the item identified by
    ``(content_type, object_id)``.

    Presence of the row means "completed". To "uncomplete", delete the
    row — this matches the ``UserCourseProgress`` toggle behaviour that
    deletes rather than nulling ``completed_at`` (see
    ``api_course_unit_complete``).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='content_completions',
    )
    content_type = models.CharField(
        max_length=32,
        choices=CONTENT_TYPE_CHOICES,
        help_text=(
            'Discriminator for the kind of content the row points at. '
            'Restricted to the values handled by content.services.completion.'
        ),
    )
    object_id = models.PositiveIntegerField(
        help_text=(
            'Integer PK of the underlying row (e.g. WorkshopPage.id). '
            'Stored as a plain integer rather than via GenericForeignKey '
            'so queries remain simple and indexable.'
        ),
    )
    completed_at = models.DateTimeField(
        help_text=(
            'When the row was inserted. Always set — presence of the row '
            'is the completion signal.'
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'content_type', 'object_id'],
                name='uniq_user_content_completion',
            ),
        ]
        indexes = [
            models.Index(
                fields=['content_type', 'object_id'],
                name='content_completion_lookup_idx',
            ),
        ]

    def __str__(self):
        return (
            f'{self.user} -> {self.content_type}:{self.object_id} '
            f'@ {self.completed_at}'
        )
