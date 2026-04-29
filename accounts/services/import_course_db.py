"""DataTalks course database CSV adapter for imported alumni users."""

import csv
from collections import OrderedDict
from pathlib import Path

from accounts.models import IMPORT_SOURCE_COURSE_DB
from accounts.services.import_users import ImportRow, register_import_adapter
from accounts.utils.tags import normalize_tag

REQUIRED_COLUMNS = {"email", "name", "course_slug"}
OPTIONAL_COLUMNS = {"enrollment_date", "course_db_user_id"}


class CourseDbCsvError(ValueError):
    """Raised when a course-db CSV cannot be loaded before a batch starts."""


def build_course_db_import_adapter(csv_path):
    """Return an adapter callable yielding aggregated course-db ImportRows."""
    rows = _load_rows(csv_path)
    import_rows = _build_import_rows(rows)
    return lambda: iter(import_rows)


def register_course_db_import_adapter():
    register_import_adapter(IMPORT_SOURCE_COURSE_DB, build_course_db_import_adapter)


def _load_rows(csv_path):
    if not csv_path:
        raise CourseDbCsvError("--csv is required for course_db imports")

    path = Path(csv_path)
    try:
        with path.open(newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            if reader.fieldnames is None:
                raise CourseDbCsvError("course_db CSV is empty or missing a header row")

            fieldnames = {field.strip() for field in reader.fieldnames if field}
            missing = sorted(REQUIRED_COLUMNS - fieldnames)
            if missing:
                raise CourseDbCsvError(
                    "course_db CSV is missing required column(s): "
                    + ", ".join(missing)
                )

            rows = []
            for row in reader:
                rows.append(
                    {
                        (key or "").strip(): (value or "").strip()
                        for key, value in row.items()
                    }
                )
            return rows
    except OSError as exc:
        raise CourseDbCsvError(f"Cannot read course_db CSV: {csv_path}") from exc


def _build_import_rows(rows):
    aggregated = OrderedDict()
    invalid_rows = []

    for row in rows:
        email = row.get("email", "").strip()
        course_slug = normalize_tag(row.get("course_slug", ""))
        if not course_slug:
            invalid_rows.append(
                ImportRow(
                    email=email,
                    name=row.get("name", ""),
                    validation_error="blank course_slug",
                )
            )
            continue

        key = email.lower()
        if key not in aggregated:
            aggregated[key] = {
                "email": email,
                "name": row.get("name", ""),
                "course_slugs": [],
                "enrollment_dates_by_course": OrderedDict(),
                "course_db_user_ids": [],
            }

        item = aggregated[key]
        if not item["name"] and row.get("name", ""):
            item["name"] = row["name"]
        if course_slug not in item["course_slugs"]:
            item["course_slugs"].append(course_slug)

        enrollment_date = row.get("enrollment_date", "")
        if enrollment_date:
            dates = item["enrollment_dates_by_course"].setdefault(course_slug, [])
            if enrollment_date not in dates:
                dates.append(enrollment_date)

        course_db_user_id = row.get("course_db_user_id", "")
        if course_db_user_id and course_db_user_id not in item["course_db_user_ids"]:
            item["course_db_user_ids"].append(course_db_user_id)

    return [*_rows_from_aggregates(aggregated.values()), *invalid_rows]


def _rows_from_aggregates(aggregates):
    for item in aggregates:
        source_metadata = {"course_slugs": item["course_slugs"]}
        if item["enrollment_dates_by_course"]:
            source_metadata["enrollment_dates_by_course"] = dict(
                item["enrollment_dates_by_course"]
            )
        if item["course_db_user_ids"]:
            source_metadata["course_db_user_ids"] = item["course_db_user_ids"]

        yield ImportRow(
            email=item["email"],
            name=item["name"],
            tier_slug="main",
            tier_expiry=None,
            tags=[f"course:{course_slug}" for course_slug in item["course_slugs"]],
            source_metadata=source_metadata,
            extra_user_fields={},
        )
