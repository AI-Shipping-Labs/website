"""Atomic ordered CourseInstructor association management."""

from django.db import transaction

from content.models import Course, CourseInstructor, Instructor


class CourseInstructorError(ValueError):
    """A safe validation or source-ownership failure."""

    def __init__(self, message, *, code="invalid_instructors"):
        self.code = code
        super().__init__(message)


def _lock_database_course(course):
    """Lock the ownership row before any association state is observed."""
    locked = Course.objects.select_for_update().get(pk=course.pk)
    if locked.source_repo:
        raise CourseInstructorError(
            "Course instructors are owned by course.yaml and must be changed in GitHub.",
            code="source_owned",
        )
    return locked


def _ordered_rows(locked):
    return list(
        CourseInstructor.objects.filter(course=locked)
        .select_related("instructor")
        .order_by("position", "pk")
    )


def _replace_locked_course_instructors(locked, instructor_ids):
    """Validate and replace associations while the course row lock is held."""
    if not isinstance(instructor_ids, list) or not all(
        isinstance(value, str) and value.strip() for value in instructor_ids
    ):
        raise CourseInstructorError("instructor_ids must be an array of instructor ids.")
    normalized_ids = [value.strip() for value in instructor_ids]
    if len(normalized_ids) != len(set(normalized_ids)):
        raise CourseInstructorError("Instructor ids must not contain duplicates.")

    instructors = {
        instructor.instructor_id: instructor
        for instructor in Instructor.objects.filter(instructor_id__in=normalized_ids)
    }
    unknown = [value for value in normalized_ids if value not in instructors]
    if unknown:
        raise CourseInstructorError(f"Unknown instructor id: {unknown[0]}.")

    CourseInstructor.objects.filter(course=locked).delete()
    CourseInstructor.objects.bulk_create([
        CourseInstructor(
            course=locked,
            instructor=instructors[instructor_id],
            position=position,
        )
        for position, instructor_id in enumerate(normalized_ids)
    ])
    return list(
        CourseInstructor.objects.filter(course=locked)
        .select_related("instructor")
        .order_by("position", "pk")
    )


@transaction.atomic
def replace_course_instructors(course, instructor_ids):
    """Atomically replace a database-owned course instructor order.

    Full replacement is intentionally serialized last-write-wins. Incremental
    Studio mutations use the helpers below so their read/modify/write cycle is
    protected by the same database row lock.
    """
    locked = _lock_database_course(course)
    return _replace_locked_course_instructors(locked, instructor_ids)


@transaction.atomic
def add_course_instructor(course, instructor_id, position):
    """Insert one instructor without losing a concurrent successful mutation."""
    locked = _lock_database_course(course)
    current = _ordered_rows(locked)
    if not isinstance(position, int) or position < 0 or position > len(current):
        raise CourseInstructorError(
            "Insertion position must be between 0 and the list length.",
            code="invalid_position",
        )
    normalized_id = instructor_id.strip() if isinstance(instructor_id, str) else ""
    if not normalized_id:
        raise CourseInstructorError("Choose an instructor.", code="unknown_instructor")
    if any(row.instructor.instructor_id == normalized_id for row in current):
        raise CourseInstructorError(
            "That instructor is already attached to this course.",
            code="duplicate_instructor",
        )
    ordered_ids = [row.instructor.instructor_id for row in current]
    ordered_ids.insert(position, normalized_id)
    return _replace_locked_course_instructors(locked, ordered_ids)


@transaction.atomic
def remove_course_instructor(course, association_id):
    """Remove one current association after locking and validating its scope."""
    locked = _lock_database_course(course)
    current = _ordered_rows(locked)
    association = next((row for row in current if row.pk == association_id), None)
    if association is None:
        raise CourseInstructorError(
            "That instructor association is stale or belongs to another course.",
            code="stale_association",
        )
    ordered_ids = [
        row.instructor.instructor_id for row in current if row.pk != association_id
    ]
    return _replace_locked_course_instructors(locked, ordered_ids)


@transaction.atomic
def reorder_course_instructors(course, association_ids, positions):
    """Reorder the exact current association set under the course row lock."""
    locked = _lock_database_course(course)
    current = _ordered_rows(locked)
    current_ids = [row.pk for row in current]
    if (
        not isinstance(association_ids, list)
        or not isinstance(positions, list)
        or not all(isinstance(value, int) for value in association_ids)
        or not all(isinstance(value, int) for value in positions)
        or len(association_ids) != len(positions)
        or len(association_ids) != len(set(association_ids))
        or set(association_ids) != set(current_ids)
        or any(position < 0 for position in positions)
    ):
        raise CourseInstructorError(
            "Instructor order was stale or invalid; nothing changed.",
            code="stale_order",
        )
    row_by_id = {row.pk: row for row in current}
    ordered = sorted(
        zip(positions, range(len(association_ids)), association_ids, strict=True),
    )
    instructor_ids = [
        row_by_id[association_id].instructor.instructor_id
        for _position, _original, association_id in ordered
    ]
    return _replace_locked_course_instructors(locked, instructor_ids)
