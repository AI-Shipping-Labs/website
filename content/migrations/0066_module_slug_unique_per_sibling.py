"""Rescope Module slug uniqueness from course-wide to per-sibling-group
(issue #1674, corrected during #1675 grooming).

The original 0064 migration in this issue kept the pre-existing
``unique_together = [('course', 'slug')]`` unchanged. That is too strict
for the real three-level Maven content #1675 imports: a "Homework"
submodule repeats under weeks 1, 3, 4, 5, 6, and "(Overview)" submodules
repeat across several weeks — a course-wide constraint would reject that
import outright. Matches the community_base package's equivalent
``(course, parent, slug)`` scoping.

Purely a constraint change, always safe against existing data: the old
``unique_together`` is a strict superset of the two new partial
constraints below (course-wide uniqueness already implies both narrower
groupings), so relaxing it cannot violate any existing row. No new
column, no ``db_default`` concern.

A plain ``UniqueConstraint(fields=['course', 'parent', 'slug'])`` would
NOT enforce uniqueness among top-level modules — standard SQL unique
constraints never treat two NULLs as equal, so every top-level module's
(course, NULL, slug) tuple is distinct regardless of slug collisions.
Two explicit constraints close that gap instead: one scoped to
``parent IS NULL`` (top-level siblings share a course), one scoped to
``parent IS NOT NULL`` (submodule siblings share a parent, which already
pins the course).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0065_cohort_mode'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='module',
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name='module',
            constraint=models.UniqueConstraint(
                condition=models.Q(('parent__isnull', True)),
                fields=('course', 'slug'),
                name='module_top_level_slug_unique_per_course',
            ),
        ),
        migrations.AddConstraint(
            model_name='module',
            constraint=models.UniqueConstraint(
                condition=models.Q(('parent__isnull', False)),
                fields=('parent', 'slug'),
                name='module_submodule_slug_unique_per_parent',
            ),
        ),
    ]
