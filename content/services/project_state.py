"""Staff-driven project state changes, mirroring CMP's admin actions."""

import logging

from content.models.peer_review import ProjectState

logger = logging.getLogger(__name__)


class ProjectStateError(ValueError):
    """The requested state change is not allowed."""


def parse_state(value):
    """A ProjectState from its code ('PR'), name ('peer_reviewing') or label."""
    text = str(value).strip().lower().replace('-', '_').replace(' ', '_')
    for state in ProjectState:
        if text in (state.value.lower(), state.name.lower(), state.label.lower().replace(' ', '_')):
            return state
    raise ProjectStateError(
        'Unknown state "%s". Use one of: %s.' % (value, ', '.join(s.name.lower() for s in ProjectState)),
    )


def set_project_state(project, state, *, actor=None):
    """Move ``project`` to ``state``; returns the previous state."""
    state = ProjectState(state)
    if project.is_self_paced:
        raise ProjectStateError('Self-paced attempts have no project state.')
    previous = project.state
    if previous == state:
        return previous
    if state == ProjectState.COMPLETED and previous != ProjectState.PEER_REVIEWING:
        raise ProjectStateError(
            'Only a project in Peer reviewing can be completed (it is %s).'
            % ProjectState(previous).label,
        )
    project.state = state
    project.save(update_fields=['state'])
    logger.info(
        'Project state changed: course=%s project=%s %s -> %s by %s',
        project.course_id, project.slug, previous, state.value,
        getattr(actor, 'email', None) or 'system',
    )
    return previous
