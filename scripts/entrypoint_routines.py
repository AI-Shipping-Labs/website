"""Best-effort and safety-critical startup routines for the entrypoint."""

import logging

logger = logging.getLogger("scripts.entrypoint_init")


def _register_schedules():
    """Register recurring django-q schedules. Idempotent and fail-safe."""
    from django.core.management import call_command

    print("Register recurring job schedules", flush=True)
    try:
        call_command("setup_schedules", verbosity=0)
    except Exception as exc:
        # A bad schedule entry cannot be allowed to take the web tier down.
        logger.exception("setup_schedules failed during entrypoint boot")
        _publish_degraded_schedule_state(exc)


def _publish_degraded_schedule_state(error):
    """Publish best-effort diagnostics after schedule registration fails."""
    try:
        from jobs.schedule_reconciliation import (
            build_schedule_definitions,
            expected_schedule_names,
            publish_schedule_reconciliation_state,
        )
        from website.release_phase import background_work_enabled

        definitions = build_schedule_definitions(
            background_enabled=background_work_enabled(),
        )
        publish_schedule_reconciliation_state(
            status="degraded",
            expected_names=expected_schedule_names(definitions),
            error=error,
        )
    except Exception:
        # Diagnostics remain subordinate to the explicit fail-open contract.
        logger.exception(
            "schedule reconciliation degraded state publication failed"
        )


def _suppress_r1_incompatible_schedules():
    """Delete stale R2 work vocabulary and propagate any failure."""
    from django.core.management import call_command

    print("Suppress R1-incompatible recurring schedules", flush=True)
    call_command("suppress_r1_incompatible_schedules", verbosity=0)


def _reconcile_r1_expand():
    """Repair compatibility writes before web serves or a worker consumes."""
    from django.core.management import call_command

    print("Reconcile R1 expanded-schema compatibility writes", flush=True)
    call_command("reconcile_r1_expand", verbosity=0)


def _initialize_runtime_observability():
    """Apply Studio observability settings after Django setup, before serve."""
    from integrations.services.observability import init_logfire

    init_logfire(use_runtime_config=True)
