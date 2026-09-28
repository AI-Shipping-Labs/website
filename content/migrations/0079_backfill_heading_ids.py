"""Backfill section-anchor heading ids into stored content HTML (issue #1833).

``render_markdown`` now gives every heading a slug ``id``. Stored HTML is only
re-rendered when its source changes, so existing rows would stay without
anchors. This migration post-processes the stored HTML with the same shared
helper the renderer uses (``content.utils.heading_ids.add_heading_ids``) and
writes the result back with a queryset ``update()``.

It deliberately does NOT call ``save()``: re-rendering would drop sync-time
post-processing such as article/marketing include expansion and video URL
replacement. The helper only inserts ``id`` into ``h1``..``h6`` opening tags
that lack one, so everything else stays byte-for-byte identical, and it is
idempotent.

Fields covered here:

- ``content.Article.content_html``
- ``content.Project.content_html``
- ``content.Tutorial.content_html``
- ``content.MarketingPage.content_html``
- ``content.Workshop.description_html``
- ``content.WorkshopPage.body_html``
- ``content.Instructor.bio_html``
- ``content.CourseExtension.peer_review_criteria_html``
- ``cb_curriculum.Course.description_html``
- ``cb_curriculum.Module.overview_html``
- ``cb_curriculum.Unit.body_html`` and ``homework_html``

Sibling migrations cover ``events`` (Event description/recap HTML,
EventSeries description, Host bio), ``topics`` (TopicPage body) and
``bookclub`` (Note body).

The reverse is a no-op: removing anchors has no operational value.
"""

from django.db import migrations

from content.utils.heading_ids import backfill_heading_ids

TARGETS = (
    ('content', 'Article', ('content_html',)),
    ('content', 'Project', ('content_html',)),
    ('content', 'Tutorial', ('content_html',)),
    ('content', 'MarketingPage', ('content_html',)),
    ('content', 'Workshop', ('description_html',)),
    ('content', 'WorkshopPage', ('body_html',)),
    ('content', 'Instructor', ('bio_html',)),
    ('content', 'CourseExtension', ('peer_review_criteria_html',)),
    ('cb_curriculum', 'Course', ('description_html',)),
    ('cb_curriculum', 'Module', ('overview_html',)),
    ('cb_curriculum', 'Unit', ('body_html', 'homework_html')),
)


def backfill(apps, schema_editor):
    using = schema_editor.connection.alias
    for app_label, model_name, fields in TARGETS:
        backfill_heading_ids(
            apps.get_model(app_label, model_name),
            fields,
            using=using,
            label=f'{app_label}.{model_name}',
        )


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0078_backfill_course_self_paced_cohorts'),
        ('cb_curriculum', '0003_unit_body_html_source'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
