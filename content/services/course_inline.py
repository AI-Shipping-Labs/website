"""Single-page AI Buildcamp topics shown as lessons in their week."""

import re


def _is_capstone(unit):
    return 'capstone' in f'{unit.slug} {unit.title}'.casefold()


def inline_homework_units(module, course_slug):
    """Return the unambiguous homework/capstone pair for a Buildcamp week.

    In the source curriculum the homework and its capstone step share a
    one-unit ``Homework`` presentation wrapper. Keep that data structure, but
    expose the primary task at the wrapper URL and the capstone at a sibling
    URL. Ambiguous or unrelated wrappers keep their ordinary unit URLs.
    """
    if (
        course_slug != 'ai-buildcamp'
        or not module.parent_id
        or module.slug != 'homework'
        or module.title.strip().casefold() != 'homework'
    ):
        return ()

    units = [unit for unit in module.units.all() if unit.kind == 'homework']
    primary = [unit for unit in units if not _is_capstone(unit)]
    capstones = [unit for unit in units if _is_capstone(unit)]
    if len(primary) != 1 or len(capstones) > 1:
        return ()
    return (primary[0], *capstones)


def inline_homework_unit_for(module, course_slug):
    """Return the primary assignment represented by a Homework wrapper."""
    units = inline_homework_units(module, course_slug)
    return units[0] if units else None


def inline_homework_capstone_for_parent(parent, route_slug, course_slug):
    """Resolve a safe ``homework-capstone`` sibling URL for a Buildcamp week."""
    if course_slug != 'ai-buildcamp':
        return None
    if any(child.slug == route_slug for child in parent.children.all()):
        return None

    matches = []
    for child in parent.children.all():
        if route_slug != f'{child.slug}-capstone':
            continue
        units = inline_homework_units(child, course_slug)
        if len(units) == 2:
            matches.append(units[1])
    return matches[0] if len(matches) == 1 else None


def is_inline_homework_unit(module, unit, course_slug):
    """Whether a unit's old duplicate path is replaced by a canonical path."""
    units = inline_homework_units(module, course_slug)
    if not units or unit.pk not in {item.pk for item in units}:
        return False
    if unit.pk == units[0].pk:
        return True
    alias = f'{module.slug}-capstone'
    return not any(child.slug == alias for child in module.parent.children.all())


def canonical_inline_homework_url(unit):
    """Return the canonical sibling URL for a Buildcamp Homework unit."""
    module = unit.module
    course = module.course
    units = inline_homework_units(module, course.slug)
    if not units:
        return None
    if unit.pk == units[0].pk:
        return module.get_absolute_url()
    if not is_inline_homework_unit(module, unit, course.slug):
        return None
    return (
        f'/courses/{course.slug}/{module.parent.slug}/'
        f'{module.slug}-capstone'
    )


def inline_unit_for(module, course_slug):
    """Return the sole unit when this topic is only a presentation wrapper."""
    if course_slug != 'ai-buildcamp' or not module.parent_id:
        return None
    is_session = module.slug in {'session', 'session-8'} and bool(
        re.fullmatch(r'Session [1-8]', module.title)
    )
    is_overview = module.title in {
        'Week 1 Overview',
        'Testing and Evaluation Overview',
        'Capstone Presentations',
    } or (module.slug == 'overview' and module.parent.sort_order == 6)
    if not (is_session or is_overview):
        return None
    units = list(module.units.all())
    return units[0] if len(units) == 1 else None


def inline_units_for(module, course_slug):
    """Expose Buildcamp optional lessons alongside the week's other items."""
    if (
        course_slug == 'ai-buildcamp'
        and module.parent_id
        and module.slug == 'optional'
        and module.title == 'Optional Content'
        and module.is_bonus
    ):
        return list(module.units.all())
    unit = inline_unit_for(module, course_slug)
    return [unit] if unit is not None else []


def inline_units_and_topics(children, course_slug):
    """Keep the source tree intact while presenting its one-page topics inline."""
    units = []
    topics = []
    for child in children:
        child_units = inline_units_for(child, course_slug)
        if not child_units:
            topics.append(child)
        else:
            units.extend(child_units)
    return units, topics
