"""Backfill Instructor rows from existing legacy string fields (issue #308).

Collects distinct ``(name, bio)`` pairs from:

- ``Course.instructor_name`` + ``Course.instructor_bio``
- ``Workshop.instructor_name`` (no bio field — pair with empty string)
- ``Event.speaker_name`` + ``Event.speaker_bio``

Groups by name (case-sensitive, so typos stay surfaceable in admin), picks
the longest non-empty bio when a name has multiple bios, slugifies the name
into ``instructor_id`` with deterministic ``-2``/``-3`` suffixes on slug
collisions, and creates ``Instructor`` rows with ``status='published'`` and
``source_repo=NULL`` (these are backfill rows, not yet sync-managed —
operators can later add yaml under ``instructors/<id>.yaml`` and re-sync to
take ownership).

Then attaches the M2M relationships at ``position=0`` for each
Course/Workshop/Event with a non-empty legacy name.

The backfill matches the FIRST resolved instructor by exact name match. If
multiple Instructor rows share a name (unlikely after collapse, but
possible if the migration runs incrementally), the lookup uses the first
``Instructor.objects.filter(name=...).first()`` result.
"""
from django.db import migrations
from django.utils.text import slugify


def _pick_longest_bio(bios):
    """Return the longest non-empty bio string from an iterable.

    Tie-broken deterministically by sorting; an empty result returns ''.
    A warning is emitted by the caller when this collapses multiple
    distinct bios.
    """
    non_empty = [b for b in bios if b]
    if not non_empty:
        return ''
    # Sort by (-length, content) so longest wins, with deterministic tie-break.
    non_empty.sort(key=lambda b: (-len(b), b))
    return non_empty[0]


def _allocate_instructor_id(name, used_ids):
    """Slugify ``name`` and add a numeric suffix on collisions.

    Mutates ``used_ids`` to track allocated slugs across the backfill so
    two different names that slugify to the same id get ``-2``/``-3``.
    Returns the allocated id; falls back to ``instructor`` when slugify
    yields an empty string (e.g. all-non-ASCII names that don't simplify).
    """
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


def backfill_instructors(apps, schema_editor):
    Course = apps.get_model('content', 'Course')
    Workshop = apps.get_model('content', 'Workshop')
    Event = apps.get_model('events', 'Event')
    Instructor = apps.get_model('content', 'Instructor')
    CourseInstructor = apps.get_model('content', 'CourseInstructor')
    WorkshopInstructor = apps.get_model('content', 'WorkshopInstructor')
    EventInstructor = apps.get_model('events', 'EventInstructor')

    # Step 1: collect (name, bio) tuples across all three tables.
    name_to_bios = {}  # name -> set of bios

    def _record(name, bio):
        if not name:
            return
        bios = name_to_bios.setdefault(name, set())
        bios.add(bio or '')

    for n, b in Course.objects.values_list('instructor_name', 'instructor_bio'):
        _record(n, b)
    for n, in Workshop.objects.values_list('instructor_name'):
        _record(n, '')
    for n, b in Event.objects.values_list('speaker_name', 'speaker_bio'):
        _record(n, b)

    if not name_to_bios:
        return

    # Step 2: collapse multiple bios per name to the longest.
    name_to_bio = {}
    for name, bios in name_to_bios.items():
        chosen = _pick_longest_bio(bios)
        # Surface bio-collapses to operators (visible in `migrate` stdout).
        non_empty = [b for b in bios if b]
        if len(non_empty) > 1:
            print(
                f"  [backfill_instructors] '{name}': "
                f'collapsed {len(non_empty)} distinct bios; '
                f'kept longest ({len(chosen)} chars).'
            )
        name_to_bio[name] = chosen

    # Step 3: allocate instructor_id slugs (with collision suffixes).
    # Skip names whose pre-existing Instructor row already exists (idempotent
    # in case the migration is rerun against a partially-populated DB —
    # though Django migrations are normally one-shot).
    used_ids = set(
        Instructor.objects.values_list('instructor_id', flat=True),
    )
    name_to_instructor = {}  # name -> Instructor row
    # Pre-load existing instructors keyed by exact-name match so we don't
    # create duplicates on a partial rerun.
    existing_by_name = {}
    for inst in Instructor.objects.all():
        existing_by_name.setdefault(inst.name, inst)

    for name in sorted(name_to_bio):  # deterministic id allocation
        if name in existing_by_name:
            name_to_instructor[name] = existing_by_name[name]
            continue
        instructor_id = _allocate_instructor_id(name, used_ids)
        bio = name_to_bio[name]
        # Render bio_html via the live model's render_markdown (data
        # migrations use historical ORM, but rendering helpers are pure
        # functions on app code). Fall back to empty when bio is blank.
        bio_html = ''
        if bio:
            from content.models.instructor import render_markdown
            bio_html = render_markdown(bio)
        inst = Instructor.objects.create(
            instructor_id=instructor_id,
            name=name,
            bio=bio,
            bio_html=bio_html,
            photo_url='',
            links=[],
            status='published',
            source_repo=None,
            source_path=None,
            source_commit=None,
        )
        name_to_instructor[name] = inst

    # Step 4: attach M2M rows at position=0 for each row with non-empty name.
    for course in Course.objects.exclude(instructor_name=''):
        inst = name_to_instructor.get(course.instructor_name)
        if inst is None:
            continue
        CourseInstructor.objects.get_or_create(
            course=course, instructor=inst,
            defaults={'position': 0},
        )

    for workshop in Workshop.objects.exclude(instructor_name=''):
        inst = name_to_instructor.get(workshop.instructor_name)
        if inst is None:
            continue
        WorkshopInstructor.objects.get_or_create(
            workshop=workshop, instructor=inst,
            defaults={'position': 0},
        )

    for event in Event.objects.exclude(speaker_name=''):
        inst = name_to_instructor.get(event.speaker_name)
        if inst is None:
            continue
        EventInstructor.objects.get_or_create(
            event=event, instructor=inst,
            defaults={'position': 0},
        )


def reverse_backfill(apps, schema_editor):
    """Reverse: delete Instructor rows that came from this backfill.

    Backfill rows are identified by ``source_repo IS NULL``. Through-table
    rows referencing them go away via the FK (``on_delete=PROTECT`` on the
    Instructor side; we delete the through rows first to avoid the
    protection raising). Subsequent forward migration is a no-op when sync
    has already populated rows from yaml — the
    ``source_repo IS NULL`` filter excludes those.
    """
    Instructor = apps.get_model('content', 'Instructor')
    CourseInstructor = apps.get_model('content', 'CourseInstructor')
    WorkshopInstructor = apps.get_model('content', 'WorkshopInstructor')
    EventInstructor = apps.get_model('events', 'EventInstructor')

    backfilled = Instructor.objects.filter(source_repo__isnull=True)
    backfilled_ids = list(backfilled.values_list('id', flat=True))
    if not backfilled_ids:
        return

    # Drop through rows first so PROTECT on Instructor FK doesn't bite.
    CourseInstructor.objects.filter(instructor_id__in=backfilled_ids).delete()
    WorkshopInstructor.objects.filter(instructor_id__in=backfilled_ids).delete()
    EventInstructor.objects.filter(instructor_id__in=backfilled_ids).delete()
    backfilled.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0031_instructor_models'),
        ('events', '0010_event_instructors'),
    ]

    operations = [
        migrations.RunPython(
            backfill_instructors,
            reverse_backfill,
        ),
    ]
