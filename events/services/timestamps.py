"""Event recording timestamp normalization helpers."""

from content.templatetags.video_utils import parse_video_timestamp

SUPPORTED_TIMESTAMP_KEYS = {"time_seconds", "time", "label", "title"}


def _canonicalize_timestamp_row(row):
    """Return ``{time_seconds, label}`` or raise ``ValueError``.

    ``time_seconds`` wins over ``time`` and ``label`` wins over ``title`` to
    match the existing content-sync/video normalization precedence.
    """
    if "time_seconds" in row:
        raw_seconds = row["time_seconds"]
        if isinstance(raw_seconds, bool):
            raise ValueError("time_seconds must be a non-negative integer.")
        if isinstance(raw_seconds, int):
            time_seconds = raw_seconds
        elif isinstance(raw_seconds, str) and raw_seconds.strip().isdigit():
            time_seconds = int(raw_seconds.strip())
        else:
            raise ValueError("time_seconds must be a non-negative integer.")
    elif "time" in row:
        try:
            time_seconds = parse_video_timestamp(row["time"])
        except ValueError as exc:
            raise ValueError(f"time must be MM:SS or H:MM:SS: {exc}") from exc
    else:
        raise ValueError("must include time_seconds or time.")

    if time_seconds < 0:
        raise ValueError("time_seconds must be a non-negative integer.")

    if "label" in row:
        label = row["label"]
        label_key = "label"
    elif "title" in row:
        label = row["title"]
        label_key = "title"
    else:
        raise ValueError("must include label or title.")

    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"{label_key} must be a non-empty string.")

    return {"time_seconds": time_seconds, "label": label.strip()}


def normalize_event_timestamps_for_sync(timestamps):
    """Store synced event timestamps in canonical shape, skipping bad rows."""
    if not timestamps:
        return []

    normalized = []
    for row in timestamps:
        if not isinstance(row, dict):
            continue

        if "time_seconds" in row:
            try:
                time_seconds = int(row.get("time_seconds") or 0)
            except (TypeError, ValueError):
                continue
        elif "time" in row:
            try:
                time_seconds = parse_video_timestamp(row.get("time"))
            except ValueError:
                continue
        else:
            continue

        if time_seconds < 0:
            continue

        normalized.append({
            "time_seconds": time_seconds,
            "label": row.get("label") or row.get("title") or "",
        })
    return normalized


def validate_event_timestamps_for_api(value):
    """Strictly validate API timestamp input.

    Returns ``(normalized_rows, error_message)``. API callers get one structured
    ``details.timestamps`` message; sync callers use the tolerant helper above.
    """
    if not isinstance(value, list):
        return None, "Must be an array of timestamp objects."

    normalized = []
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            return None, f"Item {index} must be an object."

        unsupported_keys = sorted(set(row) - SUPPORTED_TIMESTAMP_KEYS)
        if unsupported_keys:
            plural = "s" if len(unsupported_keys) != 1 else ""
            return (
                None,
                f"Item {index} has unsupported field{plural}: "
                f"{', '.join(unsupported_keys)}.",
            )

        try:
            normalized.append(_canonicalize_timestamp_row(row))
        except ValueError as exc:
            return None, f"Item {index} {exc}"

    return normalized, None
