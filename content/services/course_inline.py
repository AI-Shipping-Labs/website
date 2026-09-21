"""Single-page AI Buildcamp topics shown as lessons in their week."""

import re


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


def inline_units_and_topics(children, course_slug):
    """Keep the source tree intact while presenting its one-page topics inline."""
    units = []
    topics = []
    for child in children:
        unit = inline_unit_for(child, course_slug)
        if unit is None:
            topics.append(child)
        else:
            units.append(unit)
    return units, topics
