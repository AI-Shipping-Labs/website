"""Pre-drop safety backfill for legacy-only instructor data (issue #423).

Migration 0032 already backfilled ``Instructor`` rows from the legacy
``Course.instructor_name`` / ``Course.instructor_bio``,
``Workshop.instructor_name``, ``Event.speaker_name`` / ``Event.speaker_bio``
strings into the M2M through tables. Between 0032 running in production and
this issue dropping the legacy columns, sync paths and admin edits could
have populated the legacy strings without also writing the M2M (e.g. when
``instructors:`` was missing from yaml). This migration is the
belt-and-braces second pass: it walks every Course / Workshop / Event whose
legacy string field is non-empty AND whose M2M is empty, finds (or creates)
the matching ``Instructor`` row, and attaches it at ``position=0``.

Idempotent and safe to re-run on a freshly-migrated database (where 0032
already ran cleanly): the queries it filters on will return zero rows and
no work happens.

Reverse is a no-op — the schema-removal migration that follows this one is
where the destructive change lives, and reversing it would restore the
legacy columns. Once the columns exist again the regular reverse path of
0032 takes over.

Counts (printed during ``manage.py migrate`` so operators can compare the
"before" line in the commit body):

- Courses with non-empty ``instructor_name`` and empty ``instructors`` M2M
- Workshops with non-empty ``instructor_name`` and empty ``instructors`` M2M
- Events with non-empty ``speaker_name`` and empty ``instructors`` M2M
"""
from django.db import migrations
from django.utils.text import slugify


def _allocate_instructor_id(name, used_ids):
    base = slugify(name) or 'instructor'
    if base not in used_ids:
        used_ids.add(base)
        return base
    suffix = 2
    while f'{base}-{suffix}' in used_ids:
        suffix += 1
    candidate = f'{base}-{suffix}'
    used_ids.add(candidate)
    return candidate


def _resolve_or_create_instructor(name, bio, Instructor, used_ids):
    """Return an Instructor for ``name``, creating one if absent.

    Matches by exact name first (so it picks up whatever 0032 already
    created). Falls back to creating a backfill row with
    ``source_repo=NULL`` and the given bio.
    """
    inst = Instructor.objects.filter(name=name).first()
    if inst is not None:
        return inst
    instructor_id = _allocate_instructor_id(name, used_ids)
    bio_html = ''
    if bio:
        # Render bio_html via the live model's render_markdown — pure
        # function, fine to call from a data migration.
        from content.models.instructor import render_markdown
        bio_html = render_markdown(bio)
    return Instructor.objects.create(
        instructor_id=instructor_id,
        name=name,
        bio=bio or '',
        bio_html=bio_html,
        photo_url='',
        links=[],
        status='published',
        source_repo=None,
        source_path=None,
        source_commit=None,
    )


def backfill_legacy_only(apps, schema_editor):
    Course = apps.get_model('content', 'Course')
    Workshop = apps.get_model('content', 'Workshop')
    Event = apps.get_model('events', 'Event')
    Instructor = apps.get_model('content', 'Instructor')
    CourseInstructor = apps.get_model('content', 'CourseInstructor')
    WorkshopInstructor = apps.get_model('content', 'WorkshopInstructor')
    EventInstructor = apps.get_model('events', 'EventInstructor')

    used_ids = set(
        Instructor.objects.values_list('instructor_id', flat=True),
    )

    # Course: legacy string set, M2M empty.
    legacy_courses = Course.objects.exclude(
        instructor_name='',
    ).filter(instructors__isnull=True)
    workshops_legacy = Workshop.objects.exclude(
        instructor_name='',
    ).filter(instructors__isnull=True)
    events_legacy = Event.objects.exclude(
        speaker_name='',
    ).filter(instructors__isnull=True)

    course_count = legacy_courses.count()
    workshop_count = workshops_legacy.count()
    event_count = events_legacy.count()

    print(
        f'  [backfill_legacy_only_instructors] before: '
        f'courses={course_count} workshops={workshop_count} '
        f'events={event_count}'
    )

    if not (course_count or workshop_count or event_count):
        print(
            '  [backfill_legacy_only_instructors] nothing to do '
            '(no rows with legacy-only instructor data).'
        )
        return

    for course in legacy_courses:
        inst = _resolve_or_create_instructor(
            course.instructor_name, course.instructor_bio,
            Instructor, used_ids,
        )
        CourseInstructor.objects.get_or_create(
            course=course, instructor=inst,
            defaults={'position': 0},
        )

    for workshop in workshops_legacy:
        inst = _resolve_or_create_instructor(
            workshop.instructor_name, '',
            Instructor, used_ids,
        )
        WorkshopInstructor.objects.get_or_create(
            workshop=workshop, instructor=inst,
            defaults={'position': 0},
        )

    for event in events_legacy:
        inst = _resolve_or_create_instructor(
            event.speaker_name, event.speaker_bio,
            Instructor, used_ids,
        )
        EventInstructor.objects.get_or_create(
            event=event, instructor=inst,
            defaults={'position': 0},
        )

    # Re-count after attaching to confirm the after-state.
    after_courses = Course.objects.exclude(
        instructor_name='',
    ).filter(instructors__isnull=True).count()
    after_workshops = Workshop.objects.exclude(
        instructor_name='',
    ).filter(instructors__isnull=True).count()
    after_events = Event.objects.exclude(
        speaker_name='',
    ).filter(instructors__isnull=True).count()
    print(
        f'  [backfill_legacy_only_instructors] after: '
        f'courses={after_courses} workshops={after_workshops} '
        f'events={after_events}'
    )


def reverse_noop(apps, schema_editor):
    """No-op reverse.

    Reverse for the data backfill is the same as the regular 0032 reverse:
    drop the rows we created. We don't try to undo here because the legacy
    string fields are still present at this migration's point in the chain;
    the next migration drops them. Reversing 0032 already deletes
    backfill-origin rows (``source_repo IS NULL``).
    """
    return


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0037_course_unit_default_access_level'),
        ('events', '0012_remove_event_type_and_live_status'),
    ]

    operations = [
        migrations.RunPython(
            backfill_legacy_only,
            reverse_noop,
        ),
    ]
