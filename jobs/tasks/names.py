"""Helpers for human-readable Django-Q task names."""

import hashlib
import re

TASK_NAME_MAX_LENGTH = 100


def sanitize_task_name_part(value):
    """Return a compact, single-line label safe for worker history."""
    text = str(value or "").strip()
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def constrain_task_name(name, max_length=TASK_NAME_MAX_LENGTH):
    """Deterministically constrain a Django-Q task name to the DB limit."""
    name = sanitize_task_name_part(name)
    if len(name) <= max_length:
        return name

    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    suffix = f"...{digest}"
    return f"{name[: max_length - len(suffix)].rstrip()}{suffix}"


def build_task_name(action, target, source, max_length=TASK_NAME_MAX_LENGTH):
    """Build ``Action: target from source`` within Django-Q's name limit."""
    action = sanitize_task_name_part(action)
    target = sanitize_task_name_part(target)
    source = sanitize_task_name_part(source)
    return constrain_task_name(
        f"{action}: {target} from {source}",
        max_length=max_length,
    )
