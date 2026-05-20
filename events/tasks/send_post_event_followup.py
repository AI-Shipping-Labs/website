"""Background tasks for the post-event follow-up email (issue #680).

Three-function module modelled on
``events.tasks.notify_reschedule``:

1. ``enqueue_post_event_followup(event_id)`` — enqueues the stage-1
   fan-out. Called from ``complete_finished_events`` after the cron
   flips an event to ``completed`` (when a recording URL is set and
   no ``EventReminderLog(interval='followup')`` row exists yet) and
   from the Studio "Send follow-up now" button.

2. ``send_post_event_followup_fanout(event_id)`` — loads the event,
   iterates ``EventRegistration.objects.filter(event=event)`` with
   ``select_related('user')``, enqueues one per-user task per
   registration. Mirrors the campaign / batch split so a poisoned
   per-user send does not kill the whole batch.

3. ``send_post_event_followup_one(event_id, user_id)`` — per-user
   send. Dedups via ``EventReminderLog.get_or_create(event, user,
   interval='followup')``, calls ``EmailService().send(user,
   'post_event_followup', ctx)``, wraps the send in ``try / except``
   so a single bad address never blocks the fan-out, same best-effort
   guarantee as #706.

Unsubscribed users still receive the follow-up — the message is
transactional (the recipient registered for this event), same policy
as ``event_reminder`` / ``event_rescheduled``. The
``post_event_followup`` template name is registered in
``email_app.services.email_classification.TRANSACTIONAL_EMAIL_TYPES``
so ``EmailService.send`` does NOT short-circuit on
``user.unsubscribed``.

Feedback CTA wiring (issue #679 dependency, soft):

- The template branches on ``{% if feedback_url %}``.
- The per-user task only populates ``feedback_url`` when the
  ``events.EventFeedback`` model exists AND
  ``reverse('event_feedback_submit', kwargs={'slug': event.slug})``
  resolves. Both conditions are required because either could ship
  separately.
- When the conditions are not satisfied the CTA stays dormant; the
  feature lights up automatically once #679 lands without any code
  change here.

Generic-fallback summary copy:

- When ``event.post_event_summary`` is blank the task substitutes a
  short fallback so the email never renders an empty paragraph.
- The fallback wording matches the spec pin in the groomed issue.
"""

import logging

from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch, reverse

from events.models import Event, EventRegistration
from integrations.config import site_base_url

logger = logging.getLogger(__name__)

INTERVAL_FOLLOWUP = 'followup'

_FALLBACK_SUMMARY_TEMPLATE = (
    "Thanks for joining us at {event_title}. The recording is now "
    "available below."
)


def enqueue_post_event_followup(event_id):
    """Enqueue the stage-1 fan-out for the post-event follow-up.

    Called from ``complete_finished_events`` (cron) and from the
    Studio "Send follow-up now" button. Always enqueues — the
    eligibility gate (recording URL set, no existing followup rows)
    lives in the cron, and the per-user idempotency gate lives in
    ``send_post_event_followup_one`` via ``EventReminderLog``.
    """
    from jobs.tasks import async_task, build_task_name

    return async_task(
        'events.tasks.send_post_event_followup.send_post_event_followup_fanout',
        event_id,
        task_name=build_task_name(
            'Send post-event follow-up',
            f'event #{event_id}',
            'post-event recap fan-out',
        ),
    )


def send_post_event_followup_fanout(event_id):
    """Stage-1 fan-out: enqueue one per-user send job per registration."""
    try:
        event = Event.objects.get(pk=event_id)
    except Event.DoesNotExist:
        logger.warning(
            'send_post_event_followup_fanout: event %s no longer exists',
            event_id,
        )
        return {'status': 'skipped', 'reason': 'missing_event', 'event_id': event_id}

    from jobs.tasks import async_task, build_task_name

    registrations = list(
        EventRegistration.objects.filter(event=event).select_related('user'),
    )

    for registration in registrations:
        async_task(
            'events.tasks.send_post_event_followup.send_post_event_followup_one',
            event_id,
            registration.user_id,
            task_name=build_task_name(
                'Send post-event follow-up (user)',
                f'event #{event_id} user #{registration.user_id}',
                'post-event recap per-user',
            ),
        )

    logger.info(
        'Post-event follow-up fan-out: event %s, %d registrations enqueued',
        event_id, len(registrations),
    )
    return {
        'status': 'enqueued',
        'event_id': event_id,
        'count': len(registrations),
    }


def send_post_event_followup_one(event_id, user_id):
    """Stage-2 per-user send.

    Idempotency contract:

    - ``EventReminderLog.get_or_create(event, user, interval='followup')``
      is the single dedup gate. When ``created=False`` we return
      immediately without re-sending and without re-writing an
      ``EmailLog`` row.
    - The dedup row is created BEFORE the email send. A 5xx from SES
      logs via ``logger.exception`` but the dedup row stays put — the
      next cron tick / manual press will NOT retry this user. This is
      the same trade-off ``create_event_reminder`` makes (issue #706):
      it is better to drop one poison-address send than to fan-loop
      forever.
    """
    User = get_user_model()
    from notifications.models import EventReminderLog

    try:
        event = Event.objects.get(pk=event_id)
    except Event.DoesNotExist:
        return {'status': 'skipped', 'reason': 'missing_event', 'event_id': event_id}

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return {'status': 'skipped', 'reason': 'missing_user', 'user_id': user_id}

    # Idempotency: the row is the single gate. ``get_or_create`` returns
    # ``created=False`` on the second pass over the same (event, user)
    # pair, and we short-circuit before touching SES.
    _, created = EventReminderLog.objects.get_or_create(
        event=event,
        user=user,
        interval=INTERVAL_FOLLOWUP,
    )
    if not created:
        return {'status': 'skipped', 'reason': 'already_sent', 'user_id': user_id}

    recording_url = event.recording_s3_url or event.recording_url
    if not recording_url:
        # Defensive: the cron's gate should have already filtered
        # events without a recording URL. If we somehow reach this
        # branch (e.g. the manual Studio button on a poorly-gated
        # event), log loudly and drop the send. The dedup row stays
        # so a later tick does not retry.
        logger.warning(
            'send_post_event_followup_one: event %s has no recording URL; '
            'skipping send for user %s',
            event_id, user_id,
        )
        return {'status': 'skipped', 'reason': 'no_recording_url', 'user_id': user_id}

    event_summary = event.post_event_summary or _FALLBACK_SUMMARY_TEMPLATE.format(
        event_title=event.title,
    )

    site_url = site_base_url()
    event_url = f'{site_url}{event.get_absolute_url()}'

    context = {
        'event_title': event.title,
        'event_summary': event_summary,
        'recording_url': recording_url,
        'event_url': event_url,
        'notes_placeholder': True,
    }

    feedback_url = _build_feedback_url(event)
    if feedback_url:
        context['feedback_url'] = feedback_url

    # Lazy import to avoid pulling EmailService at module load.
    from email_app.services.email_service import EmailService

    try:
        EmailService().send(user, 'post_event_followup', context)
    except Exception:
        # Best-effort: a single bad address must NOT block subsequent
        # per-user tasks in the fan-out. Same policy as #706.
        logger.exception(
            'Failed to send post_event_followup to %s for event %s',
            user.email, event.slug,
        )
        return {'status': 'errored', 'user_id': user_id}

    logger.info(
        'Sent post-event follow-up to %s for event "%s"',
        user.email, event.title,
    )
    return {'status': 'sent', 'user_id': user_id}


def _build_feedback_url(event):
    """Return the public feedback submit URL or ``None`` when unavailable.

    The CTA is wired conditionally on issue #679 having shipped:

    - ``events.EventFeedback`` model must be importable.
    - ``reverse('event_feedback_submit', kwargs={'slug': event.slug})``
      must resolve.

    When either condition is unmet we return ``None`` and the
    template's ``{% if feedback_url %}`` block stays empty.
    """
    try:
        from events.models import EventFeedback  # noqa: F401
    except ImportError:
        return None

    try:
        path = reverse('event_feedback_submit', kwargs={'slug': event.slug})
    except NoReverseMatch:
        return None

    return f'{site_base_url()}{path}'
