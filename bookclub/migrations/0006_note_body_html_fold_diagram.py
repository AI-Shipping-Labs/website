"""Replace ``Note.diagram`` with a rendered ``Note.body_html`` (issue: inline
mermaid in note bodies).

The separate ``diagram`` field and its "Optional diagram (mermaid)" input are
removed. A note is now a single markdown ``body``; a fenced ````mermaid```` code
block renders as a diagram and any other fenced block renders as code. Rendering
happens on ``Note.save()`` into the sanitised ``body_html`` column.

Data preservation: any existing note that has diagram source keeps it by folding
it into the body as a ````mermaid```` fence, so no reader loses a diagram. Then
``body_html`` is populated for every row via the same renderer the model uses.
"""

from django.db import migrations, models


def fold_diagram_and_render(apps, schema_editor):
    from content.utils.markdown import render_description_html

    Note = apps.get_model('bookclub', 'Note')
    for note in Note.objects.all().iterator():
        body = note.body or ''
        diagram = (note.diagram or '').strip()
        if diagram:
            # Append the old diagram source as a fenced mermaid block so it now
            # renders inline from the body, matching the new single-field model.
            fence = f'```mermaid\n{diagram}\n```'
            body = f'{body}\n\n{fence}' if body.strip() else fence
        note.body = body
        note.body_html = render_description_html(body)
        note.save(update_fields=['body', 'body_html'])


def noop_reverse(apps, schema_editor):
    # Irreversible data fold: re-splitting a mermaid fence back into a separate
    # diagram field is lossy and unnecessary, so reverse is a no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('bookclub', '0005_book_summary_book_summary_published_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='note',
            name='body_html',
            field=models.TextField(blank=True, default='', editable=False),
        ),
        migrations.RunPython(fold_diagram_and_render, noop_reverse),
        migrations.RemoveField(
            model_name='note',
            name='diagram',
        ),
        migrations.AlterField(
            model_name='note',
            name='body',
            field=models.TextField(
                help_text='The note text, in markdown. An empty submit '
                          'clears/deletes the note. A fenced ````mermaid```` '
                          'code block renders as a diagram; other fenced code '
                          'blocks render as code.',
            ),
        ),
    ]
