"""Book Club summary-publish notifications (issue #1374).

Fires the in-app bell, a preference-respecting email, and a single Slack
broadcast when an organizer publishes a chapter or full-book summary. It
reuses the existing notification framework end to end — no new channel
plumbing.

The single entry point :func:`notify_summary_published` is called from BOTH
publish surfaces (Studio ``studio/views/books.py`` and the admin API
``api/views/books.py``) via the shared transition predicate
:func:`is_new_publish`, so the trigger cannot drift between them. The fire
condition is the ``summary_published_at`` ``null -> set`` transition that
``bookclub.summaries.resolve_publish_state`` assigns — a re-save of a still
published summary (body edit, form resubmit, notes-pull) keeps the old
timestamp and therefore never re-fires; an unpublish -> republish assigns a
fresh timestamp and DOES re-fire (a deliberate operator re-announcement,
mirroring the ``create_plan_shared`` re-share contract).

Every channel is best-effort: a notification/email/Slack failure must never
roll back the publish or 5xx the PATCH. Callers additionally wrap the entry
point in try/except + ``logger.exception`` for defence in depth.
"""

import logging

from django.contrib.auth import get_user_model
from django.urls import reverse

from bookclub.models import (
    BOOK_STATUS_CURRENT,
    BOOK_STATUS_FINISHED,
    BOOK_STATUS_UPCOMING,
    Chapter,
)
from bookclub.summaries import summary_excerpt
from email_app.services.email_service import EmailService
from integrations.config import site_base_url
from notifications.models import Notification
from notifications.services.notification_service import _get_eligible_users
from notifications.services.slack_announcements import (
    post_bookclub_summary_announcement,
)

logger = logging.getLogger(__name__)

User = get_user_model()

# Only a publicly reachable book notifies. Publishing a summary on a
# ``draft`` / ``cancelled`` book is a staff-preview action members cannot
# reach, so it must never notify anyone (issue #1374).
NOTIFIABLE_BOOK_STATUSES = (
    BOOK_STATUS_CURRENT,
    BOOK_STATUS_UPCOMING,
    BOOK_STATUS_FINISHED,
)


def is_new_publish(old_published_at, new_published_at):
    """True exactly on the ``null -> set`` summary publish transition (#1374).

    Both publish surfaces call this with the pre-save and post-save
    ``summary_published_at`` so they detect the transition identically. A
    re-save of a still-published summary passes ``(ts, ts)`` -> False; an
    unpublish passes ``(ts, None)`` -> False; a fresh publish passes
    ``(None, ts)`` -> True.
    """
    return old_published_at is None and new_published_at is not None


def _summary_payload(obj):
    """Return the notification payload for a ``Book`` or ``Chapter`` publish.

    Returns a dict with ``book``, ``title``, ``body``, ``url`` (relative),
    ``full_url``, ``email_template``, and ``email_context``. Copy is plain
    text (no markdown), matching issue #1374.
    """
    if isinstance(obj, Chapter):
        chapter = obj
        book = chapter.book
        title = f'New chapter summary: {book.title} (Ch. {chapter.number})'
        body = summary_excerpt(chapter.summary) or (
            f'Read the summary for "{chapter.title}."'
        )
        url = reverse(
            'bookclub_chapter_detail',
            kwargs={'slug': book.slug, 'number': chapter.number},
        ) + '#summary'
        full_url = f'{site_base_url()}{url}'
        email_template = 'bookclub_chapter_summary'
        email_context = {
            'book_title': book.title,
            'chapter_number': chapter.number,
            'chapter_title': chapter.title,
            'summary_line': body,
            'summary_url': full_url,
        }
    else:
        book = obj
        title = f'Book summary published: {book.title}'
        body = summary_excerpt(book.summary) or (
            f'The full summary for "{book.title}" is ready.'
        )
        url = reverse('bookclub_book_summary', kwargs={'slug': book.slug})
        full_url = f'{site_base_url()}{url}'
        email_template = 'bookclub_book_summary'
        email_context = {
            'book_title': book.title,
            'summary_line': body,
            'summary_url': full_url,
        }
    return {
        'book': book,
        'title': title,
        'body': body,
        'url': url,
        'full_url': full_url,
        'email_template': email_template,
        'email_context': email_context,
    }


def _email_eligible_users(required_level, *, exclude_user=None):
    """Book-access members who should receive the summary email (issue #1374).

    Starts from the tier-eligible bell audience (``_get_eligible_users``) and
    applies the promotional email filters used by the workshop channel (#655):
    ``unsubscribed=False`` (permanent SES bounces are already unsubscribed via
    #766), ``email_verified=True``, and the per-type ``bookclub_emails``
    opt-out (default opted-in when the key is absent). The acting staff user
    is excluded when resolvable so an organizer never emails themselves.
    """
    base = _get_eligible_users(required_level).filter(
        unsubscribed=False,
        email_verified=True,
    )
    if exclude_user is not None and exclude_user.pk is not None:
        base = base.exclude(pk=exclude_user.pk)

    # Exclude only rows where ``bookclub_emails`` is explicitly False; keep
    # rows where the key is absent (default opted-in). Collected in Python so
    # the ``__key=False`` JSONField quirk on SQLite does not drop opted-in
    # users (mirrors ``get_email_eligible_users``).
    opted_out_ids = [
        pk for pk, prefs in
        User.objects.filter(
            is_active=True,
            email_preferences__has_key='bookclub_emails',
        ).values_list('pk', 'email_preferences')
        if prefs.get('bookclub_emails') is False
    ]
    if opted_out_ids:
        return base.exclude(pk__in=opted_out_ids)
    return base


def _send_summary_emails(payload, required_level, *, exclude_user=None):
    """Fan out the summary email; return the success count (best-effort).

    Per-recipient try/except so one bad address never blocks the rest of the
    fan-out (mirrors ``_send_email_channel``).
    """
    service = EmailService()
    template = payload['email_template']
    context = payload['email_context']
    sent = 0
    for user in _email_eligible_users(required_level, exclude_user=exclude_user):
        try:
            log = service.send(user, template, context)
        except Exception:
            logger.warning(
                'Failed to send "%s" email to %s',
                template, user.email, exc_info=True,
            )
            continue
        # EmailService.send returns None for skipped recipients (e.g. a
        # globally unsubscribed user for promotional mail).
        if log is not None:
            sent += 1
    return sent


def notify_summary_published(obj, *, acting_user=None):
    """Notify book-access members that a summary was published (issue #1374).

    ``obj`` is a ``Book`` (full-book summary) or a ``Chapter`` (per-chapter
    summary). Callers invoke this ONLY when
    :func:`is_new_publish` detected the publish transition, so this function
    does not re-check the timestamp — it fans out unconditionally for a
    publicly visible book.

    Channels (all best-effort; a failure in one never blocks the others or
    the publish):

    - In-app bell: one ``bookclub_summary`` ``Notification`` per book-access
      member (``_get_eligible_users(required_level)``), excluding the acting
      staff user (no self-notify) when resolvable.
    - Email: a preference-respecting promotional send (``bookclub_emails``
      opt-out).
    - Slack: a single broadcast to the announcements channel.

    Returns ``{"notified": int, "emailed": int}``.
    """
    result = {'notified': 0, 'emailed': 0}

    payload = _summary_payload(obj)
    book = payload['book']

    # Visibility guard: never notify for a draft/cancelled book.
    if book.status not in NOTIFIABLE_BOOK_STATUSES:
        return result

    required_level = book.required_level

    # In-app bell first (durable state persisted before the email send, so a
    # SES failure never unwinds the bell rows — mirrors create_event_reminder).
    audience = _get_eligible_users(required_level)
    if acting_user is not None and acting_user.pk is not None:
        audience = audience.exclude(pk=acting_user.pk)
    notifications = [
        Notification(
            user=user,
            title=payload['title'],
            body=payload['body'],
            url=payload['url'],
            notification_type='bookclub_summary',
        )
        for user in audience
    ]
    if notifications:
        Notification.objects.bulk_create(notifications)
    result['notified'] = len(notifications)
    logger.info(
        'Created %d bookclub_summary notifications for %s',
        result['notified'], book.slug,
    )

    # Email channel (best-effort).
    try:
        result['emailed'] = _send_summary_emails(
            payload, required_level, exclude_user=acting_user,
        )
    except Exception:
        logger.exception(
            'Failed to fan out bookclub summary email for %s', book.slug,
        )

    # Slack broadcast (best-effort).
    try:
        post_bookclub_summary_announcement(payload)
    except Exception:
        logger.exception(
            'Failed to post bookclub summary Slack announcement for %s',
            book.slug,
        )

    return result
