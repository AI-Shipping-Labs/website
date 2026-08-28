"""Lifecycle registry for models that own shared comment threads.

The comments app stores opaque UUIDs and deliberately does not import the
owning applications at module load. Each owner registers itself from its
``AppConfig.ready()`` hook instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models, transaction
from django.db.models import Count, Max
from django.db.models.signals import post_delete

from comments.models import Comment, CommentVote

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class ThreadOwner:
    """One model whose UUID field can own a shared comment thread."""

    model: type[models.Model]
    content_id_field: str
    cascade_thread_delete: bool
    user_field: str | None

    @property
    def app_label(self):
        return self.model._meta.app_label

    @property
    def model_name(self):
        return self.model._meta.object_name


_THREAD_OWNERS: dict[str, ThreadOwner] = {}


def _user_field_for(model):
    """Return the model's sole direct user FK, if it has one."""
    candidates = [
        field.name
        for field in model._meta.fields
        if field.is_relation
        and field.remote_field.model._meta.label_lower
        == settings.AUTH_USER_MODEL.lower()
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def register_thread_owner(
    model,
    *,
    content_id_field,
    cascade_thread_delete,
):
    """Register one owner and optionally wire its thread-delete invariant."""
    model._meta.get_field(content_id_field)
    owner = ThreadOwner(
        model=model,
        content_id_field=content_id_field,
        cascade_thread_delete=cascade_thread_delete,
        user_field=_user_field_for(model),
    )
    key = model._meta.label_lower
    existing = _THREAD_OWNERS.get(key)
    if existing is not None:
        if existing != owner:
            raise ValueError(f'Conflicting comment-thread registration for {key}.')
        return existing

    if cascade_thread_delete and owner.user_field is None:
        raise ValueError(
            f'Cascading comment-thread owner {key} must have one direct user field.'
        )

    _THREAD_OWNERS[key] = owner
    if cascade_thread_delete:
        # A post_delete listener removes this model from Django's fast-delete
        # path. That is intentional: queryset and parent-cascade deletes must
        # instantiate every owner so each UUID receives this invariant.
        def _delete_owned_thread(sender, instance, **kwargs):
            del sender, kwargs
            delete_thread(instance, getattr(instance, content_id_field))

        post_delete.connect(
            _delete_owned_thread,
            sender=model,
            dispatch_uid=f'comments.delete_thread.{key}',
            weak=False,
        )
    return owner


def thread_owners():
    """Return every registered thread owner in deterministic label order."""
    return tuple(_THREAD_OWNERS[key] for key in sorted(_THREAD_OWNERS))


@transaction.atomic
def delete_thread(instance, content_id):
    """Delete one UUID thread, its votes, and only exactly mapped notices."""
    from notifications.models import Notification  # noqa: PLC0415

    # The owner instance remains part of the public operation signature for
    # live-delete callers, but notification identity is the durable UUID only.
    # In particular, URL text must never become a deletion predicate.
    del instance

    comments = Comment.objects.filter(content_id=content_id)
    comment_count = comments.count()
    vote_count = CommentVote.objects.filter(comment__content_id=content_id).count()

    notifications = Notification.objects.filter(
        notification_type='content_comment',
        thread_content_id=content_id,
    )
    notification_count = notifications.count()
    notifications.delete()
    comments.delete()
    return {
        'comments': comment_count,
        'comment_votes': vote_count,
        'notifications': notification_count,
    }


def orphaned_thread_queryset(*, cutoff: datetime):
    """Return old UUID threads that resolve to no registered owner."""
    candidates = (
        Comment.objects
        .values('content_id')
        .annotate(
            comment_count=Count('id'),
            newest_comment_at=Max('created_at'),
        )
        .filter(newest_comment_at__lte=cutoff)
        .order_by('content_id')
    )
    for owner in thread_owners():
        owned_ids = (
            owner.model.objects
            .exclude(**{f'{owner.content_id_field}__isnull': True})
            .values_list(owner.content_id_field, flat=True)
        )
        candidates = candidates.exclude(content_id__in=owned_ids)
    return candidates
