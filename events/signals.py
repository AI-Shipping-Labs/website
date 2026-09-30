"""Event model signals: queue the automatic recap notice.

A recap becomes ready through several writers (Studio edit, the events API
and the recap skill that drives it, the transcript recap draft, content
sync). Hooking the model save covers all of them. ``pre_save`` records
whether the stored row was already ready; ``post_save`` queues the notice
only on a not-ready -> ready transition, so later recap edits never resend.
New rows are skipped: creating an already-finished event with a recap (an
import or a sync backfill) must not mail its audience.
"""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from events.models import Event
from events.services.event_recap_notification import (
    is_recap_ready,
    maybe_enqueue_recap_auto_notify,
)

_WAS_READY_ATTR = "_recap_ready_before_save"


@receiver(pre_save, sender=Event)
def remember_recap_readiness(sender, instance, raw=False, **kwargs):
    if raw or instance.pk is None:
        return
    # Only rows that are ready after this save can transition; skip the
    # extra read for every other save.
    if not is_recap_ready(instance):
        setattr(instance, _WAS_READY_ATTR, True)
        return
    stored = Event.objects.filter(pk=instance.pk).first()
    setattr(
        instance, _WAS_READY_ATTR,
        stored is None or is_recap_ready(stored),
    )


@receiver(post_save, sender=Event)
def queue_recap_auto_notify(sender, instance, created, raw=False, **kwargs):
    if raw or created:
        return
    was_ready = getattr(instance, _WAS_READY_ATTR, True)
    try:
        del instance.__dict__[_WAS_READY_ATTR]
    except KeyError:
        pass
    maybe_enqueue_recap_auto_notify(instance, was_ready=was_ready)
