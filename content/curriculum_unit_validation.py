"""AISL validation layered over the shared curriculum Unit model."""

from django.core.exceptions import ValidationError

from content.models.course import UNIT_KIND_EVENT


def validate_unit(unit, package_clean) -> None:
    """Keep AISL's tree and event rules after package validation."""
    package_clean(unit)
    if unit.module_id and unit.module.children.exists():
        raise ValidationError({
            'module': (
                f"Module {unit.module.title!r} has child modules; "
                'it cannot also have direct units.'
            ),
        })
    if unit.kind == UNIT_KIND_EVENT:
        if unit.session_position is None or unit.session_position < 1:
            raise ValidationError({
                'session_position': (
                    'kind="event" requires a positive session_position.'
                ),
            })
