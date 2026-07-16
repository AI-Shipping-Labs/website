"""Compile-time release gates for the staged schema rollout in issue #1266.

R1 must remain work-vocabulary compatible with the production image at
``524153b6`` while that image overlaps the new web and worker tasks.  These
constants deliberately are not settings: changing them requires a separately
built, reviewed artifact (R2), which prevents an operator toggle from
publishing incompatible queue work during R1.
"""

R1_EXPAND_COMPATIBILITY = True
R2_BACKGROUND_WORK_ENABLED = False


def background_work_enabled() -> bool:
    """Return whether post-baseline queue producers may publish work."""

    return R2_BACKGROUND_WORK_ENABLED
