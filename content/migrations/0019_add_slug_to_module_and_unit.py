"""Add slug fields to Module and Unit, populate from source_path/title, add unique constraints."""

import re

from django.db import migrations, models
from django.utils.text import slugify


def derive_slug(name):
    """Derive slug from filename/dirname, stripping numeric prefix."""
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    match = re.match(r'^\d+-(.+)', stem)
    return match.group(1) if match else stem


def populate_module_slugs(apps, schema_editor):
    Module = apps.get_model('content', 'Module')
    for module in Module.objects.all():
        if module.source_path:
            # source_path is like 'courses/aihero/01-day-1'
            dirname = module.source_path.rstrip('/').rsplit('/', 1)[-1]
            module.slug = derive_slug(dirname)
        else:
            module.slug = slugify(module.title) or f'module-{module.pk}'
        module.save(update_fields=['slug'])

    # Handle duplicates within same course
    from collections import Counter
    for course_id in Module.objects.values_list('course_id', flat=True).distinct():
        modules = list(Module.objects.filter(course_id=course_id).order_by('sort_order'))
        slug_counts = Counter()
        for module in modules:
            slug_counts[module.slug] += 1
            if slug_counts[module.slug] > 1:
                module.slug = f'{module.slug}-{slug_counts[module.slug]}'
                module.save(update_fields=['slug'])


def populate_unit_slugs(apps, schema_editor):
    Unit = apps.get_model('content', 'Unit')
    for unit in Unit.objects.all():
        if unit.source_path:
            # source_path is like 'courses/aihero/01-day-1/02-setup.md'
            filename = unit.source_path.rsplit('/', 1)[-1]
            unit.slug = derive_slug(filename)
        else:
            unit.slug = slugify(unit.title) or f'unit-{unit.pk}'
        unit.save(update_fields=['slug'])

    # Handle duplicates within same module
    from collections import Counter
    for module_id in Unit.objects.values_list('module_id', flat=True).distinct():
        units = list(Unit.objects.filter(module_id=module_id).order_by('sort_order'))
        slug_counts = Counter()
        for unit in units:
            slug_counts[unit.slug] += 1
            if slug_counts[unit.slug] > 1:
                unit.slug = f'{unit.slug}-{slug_counts[unit.slug]}'
                unit.save(update_fields=['slug'])


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0018_add_content_id_to_models'),
    ]

    operations = [
        # Step 1: Add slug fields without unique constraint
        migrations.AddField(
            model_name='module',
            name='slug',
            field=models.SlugField(default='', max_length=300),
        ),
        migrations.AddField(
            model_name='unit',
            name='slug',
            field=models.SlugField(default='', max_length=300),
        ),
        # Step 2: Populate slugs for existing rows
        migrations.RunPython(populate_module_slugs, migrations.RunPython.noop),
        migrations.RunPython(populate_unit_slugs, migrations.RunPython.noop),
        # Step 3: Add unique constraints
        migrations.AlterUniqueTogether(
            name='module',
            unique_together={('course', 'slug')},
        ),
        migrations.AlterUniqueTogether(
            name='unit',
            unique_together={('module', 'slug')},
        ),
    ]
