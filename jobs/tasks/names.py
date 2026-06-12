"""Helpers for human-readable Django-Q task names."""

import hashlib
import re

from django_q.humanhash import DEFAULT_WORDLIST

TASK_NAME_MAX_LENGTH = 100

# Django-Q auto-generates a task name by humanizing the task UUID into four
# lowercase words drawn from ``DEFAULT_WORDLIST`` joined by a hyphen, e.g.
# ``sodium-mango-stairway-mountain``. We treat a stored name as a codename
# only when it matches that exact shape (four words, all from the word list).
# Building the set from the library's own list keeps detection conservative:
# a legitimate hyphenated schedule name like ``event-reminders`` is only two
# words and its words are not in the list, so it is never misclassified.
_CODENAME_WORDS = frozenset(DEFAULT_WORDLIST)
_CODENAME_WORD_COUNT = 4
# A hint appended to the func-path fallback so operators can tell the name was
# synthesized from the func path rather than supplied descriptively.
AUTO_NAMED_HINT = "(auto-named)"


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


def is_django_q_codename(name):
    """Return ``True`` when ``name`` is a Django-Q auto-generated codename.

    A codename is exactly four lowercase words from Django-Q's
    ``DEFAULT_WORDLIST`` joined by hyphens (e.g. ``texas-texas-oscar-earth``).
    Detection is deliberately strict — it requires the full four-word shape
    AND every word to be in the library word list — so a descriptive
    hyphenated schedule name such as ``event-reminders`` or
    ``slack-membership-refresh`` is never misclassified.
    """
    text = sanitize_task_name_part(name)
    if not text:
        return False
    parts = text.split("-")
    if len(parts) != _CODENAME_WORD_COUNT:
        return False
    return all(part in _CODENAME_WORDS for part in parts)


def humanize_task_name(name, func):
    """Return a display name for a worker-history row.

    Descriptive stored names are returned unchanged. When the stored name is
    empty or a Django-Q codename, fall back to the dotted ``func`` path with an
    ``(auto-named)`` hint so the operator can still identify the task. If both
    the name and func are missing, return the original (possibly empty) name.
    """
    text = sanitize_task_name_part(name)
    if text and not is_django_q_codename(text):
        return text
    func_path = sanitize_task_name_part(func)
    if func_path:
        return f"{func_path} {AUTO_NAMED_HINT}"
    return text
