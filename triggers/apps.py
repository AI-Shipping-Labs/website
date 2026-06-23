"""App config for the partner-agnostic outbound event-hooks subsystem.

The ``triggers`` app owns the emit_event dispatch pipeline, the
subscription/widget registry, the content-embeddable claim widget, the
Studio screens, and the authenticated API. The word "event" is reserved
for the payload/envelope only — the existing ``events`` app (Zoom/live
events) is a completely separate concern (issue #1070).
"""

from django.apps import AppConfig


class TriggersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "triggers"
    verbose_name = "Event triggers"
