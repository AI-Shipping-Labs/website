"""Focused deletion and ownership contracts for issue #1543."""

from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]


def _deleted_template_paths() -> tuple[Path, ...]:
    timeline_wrappers = tuple(
        Path('templates', 'events', f'_timeline_{variant}_card.html')
        for variant in ('event', 'past', 'series')
    )
    workshop_facets = tuple(
        Path('templates', 'content', f'_workshop_{facet}_facet_body.html')
        for facet in ('topic', 'technology')
    )
    return (
        *timeline_wrappers,
        *workshop_facets,
        Path('templates', 'studio', 'events', '_past_' + 'pager.html'),
        Path('templates', 'studio', 'plans', 'note_' + 'form.html'),
    )


class UnreachableLegacyTemplateDeletionTest(SimpleTestCase):
    def test_deleted_templates_are_absent(self):
        remaining = [
            relative.as_posix()
            for relative in _deleted_template_paths()
            if (REPO_ROOT / relative).exists()
        ]

        self.assertEqual(remaining, [])

    def test_live_owner_templates_keep_canonical_wiring(self):
        timeline = (
            REPO_ROOT / 'templates' / 'events' / '_events_timeline.html'
        ).read_text(encoding='utf-8')
        listing_include = 'events/_timeline_listing_card.html'
        self.assertEqual(timeline.count(listing_include), 3)
        for variant in ('upcoming', 'past', 'series'):
            self.assertIn(f'card_variant="{variant}"', timeline)

        catalog = (
            REPO_ROOT / 'templates' / 'content' / '_workshops_catalog.html'
        ).read_text(encoding='utf-8')
        self.assertIn('workshop-topic-all', catalog)
        self.assertIn('workshop-topic-{{ topic.slug }}', catalog)
        self.assertNotIn('workshop-topic-option-', catalog)
        self.assertNotIn('workshop-technology-option-', catalog)

        studio_events = (
            REPO_ROOT / 'templates' / 'studio' / 'events' / 'list.html'
        ).read_text(encoding='utf-8')
        self.assertIn('studio/includes/list_pager.html', studio_events)
        self.assertIn('pager_testid_prefix="event-past-list-pager"', studio_events)

    def test_deleted_template_names_have_no_live_source_references(self):
        forbidden_references = {
            path.relative_to('templates').as_posix()
            for path in _deleted_template_paths()
        }
        references = []
        for source_path in REPO_ROOT.rglob('*'):
            if source_path.suffix not in {'.html', '.js', '.py'}:
                continue
            relative = source_path.relative_to(REPO_ROOT)
            if any(part in {'.git', '.tmp', '.venv', 'node_modules'} for part in relative.parts):
                continue
            if relative.parts[:2] == ('_docs', 'audits'):
                continue
            source = source_path.read_text(encoding='utf-8')
            for reference in forbidden_references:
                if reference in source:
                    references.append(f'{relative.as_posix()}: {reference}')

        self.assertEqual(references, [])
