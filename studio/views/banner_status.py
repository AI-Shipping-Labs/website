"""Studio JSON status endpoint for the in-place banner loader (issue #995).

The progressively-enhanced "Regenerate banner" form on a Studio event edit
page polls this endpoint every ~2s after submitting. It reports the most
recent render task's terminal state plus the freshly-resolved effective
banner URL so the JS handler can swap the ``<img>`` src in place on success
(or restore the button + show the failure note on failure) without a
full-page reload.

Staff-session auth, consistent with the rest of Studio — it reuses the
existing :func:`studio.services.banner_status.get_last_banner_task` helper and
:func:`integrations.services.banner_generator.resolve.effective_banner_url`,
so it carries the resolved ``banner_url`` the token-auth
``/api/worker/tasks/<id>`` endpoint does not.
"""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from events.models import Event
from integrations.services.banner_generator.resolve import effective_banner_url
from studio.decorators import staff_required
from studio.services.banner_status import get_last_banner_task


@staff_required
def studio_event_banner_status(request, event_id):
    """Return the latest banner render state + resolved URL for an event.

    Shape::

        {
            "state": "none" | "in_progress" | "success" | "failed",
            "banner_url": "<resolved effective banner URL or ''>",
            "task_detail_url": "<Studio worker task detail URL or null>",
        }

    The poller treats ``success`` / ``failed`` as terminal. On ``success`` the
    ``banner_url`` is the freshly-resolved effective URL (cache-busted by the
    client).
    """
    event = get_object_or_404(Event, pk=event_id)
    # Re-fetch from the DB so a just-finished worker write (auto_banner_url) is
    # visible to the resolver rather than a stale in-memory copy.
    event.refresh_from_db()
    last_task = get_last_banner_task("event", event.pk)
    return JsonResponse(
        {
            "state": last_task["state"],
            "banner_url": effective_banner_url(event),
            "task_detail_url": last_task.get("task_detail_url"),
        }
    )
