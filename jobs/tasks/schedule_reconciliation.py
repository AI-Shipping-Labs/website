"""Periodic recovery for the declarative recurring schedule set."""


def reconcile_schedules():
    """Re-run the same validated, atomic apply used at container boot."""
    from jobs.schedule_reconciliation import (
        apply_schedule_definitions,
        build_schedule_definitions,
    )
    from website.release_phase import background_work_enabled

    definitions = build_schedule_definitions(
        background_enabled=background_work_enabled(),
    )
    apply_schedule_definitions(definitions)
