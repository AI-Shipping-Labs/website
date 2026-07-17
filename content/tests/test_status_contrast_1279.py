"""Source and render contracts for public/member status contrast (#1279)."""

import re
from pathlib import Path

from django.template import Context, Template
from django.test import SimpleTestCase

from content.models import Project
from content.templatetags.member_badges import STATUS_TONES, TONE_CLASSES
from content.views.tags import CONTENT_TYPES

ROOT = Path(__file__).resolve().parents[2]
CANONICAL = {
    'green': 'bg-green-500/15 text-green-800 dark:text-green-400',
    'yellow': 'bg-yellow-500/15 text-yellow-800 dark:text-yellow-400',
    'red': 'bg-red-500/15 text-red-800 dark:text-red-400',
    'blue': 'bg-blue-500/15 text-blue-800 dark:text-blue-400',
    'purple': 'bg-purple-500/15 text-purple-800 dark:text-purple-400',
    'orange': 'bg-orange-500/15 text-orange-800 dark:text-orange-400',
}

IN_SCOPE = {
    'I1': (
        'content/models/project.py',
        'templates/content/_project_card.html',
        'templates/content/project_detail.html',
    ),
    'I2': ('content/templatetags/member_badges.py',),
    'I3': ('content/views/tags.py', 'templates/content/tags_detail.html'),
    'I4': ('templates/content/course_detail.html',),
    'I5': (
        'templates/content/peer_review/dashboard.html',
        'templates/content/peer_review/submit.html',
    ),
    'I6': ('templates/events/event_series.html',),
    'I7': ('templates/plans/_checkpoint_card.html', 'templates/plans/_plan_body.html'),
    'I8': (
        'templates/content/reader/_completion_button.html',
        'templates/content/reader/_scripts.html',
    ),
}

# These are intentional non-badge owners from the groomed X1-X6 sweep. The
# unsafe-recipe scanner may only encounter the explicitly listed files; Studio
# code/templates are excluded wholesale because X6 owns that separate system.
EXCLUDED = {
    'X1': ('content/models/download.py', 'templates/studio/downloads/list.html'),
    'X2': (
        'templates/_partials/messages.html',
        'templates/accounts/account.html',
        'templates/content/peer_review/dashboard.html',
        'templates/content/peer_review/submit.html',
    ),
    'X3': (
        'templates/content/peer_review/certificate.html',
        'templates/content/course_detail.html',
        'templates/events/event_series.html',
    ),
    'X4': (
        'accounts/templatetags/accounts_extras.py',
        'templates/content/course_detail.html',
        'templates/content/reader/_completion_button.html',
    ),
    'X5': (
        'templates/includes/tag_rule_components.html',
        'templates/integrations/admin_sync_history.html',
    ),
    'X6': (
        'templates/integrations/admin_sync.html',
        'templates/integrations/admin_sync_history.html',
        'email_app/ses_explain.py',
        'studio/views/ses_events.py',
        'templates/studio',
    ),
}

UNSAFE_BADGE_RECIPE = re.compile(
    r'bg-(?:green|yellow|red|blue|purple|orange)-500/(?:15|20)'
    r'[^\n]{0,180}(?<!dark:)text-(?:green|yellow|red|blue|purple|orange)-400'
)
UNSAFE_ALLOWLIST = {
    'content/models/download.py',          # X1: Studio-only file-type palette
    'templates/_partials/messages.html',   # X2: flash alert
    'templates/accounts/account.html',     # X2: form-feedback panel
    'templates/integrations/admin_sync.html',  # X6: operator surface
    'email_app/ses_explain.py',            # X6: operator SES explanation
}


def _source(path):
    return (ROOT / path).read_text(encoding='utf-8')


class StatusPaletteContractTest(SimpleTestCase):
    def test_project_difficulty_mapping_and_both_render_owners(self):
        project = Project(slug='contrast')
        cases = {
            'beginner': CANONICAL['green'],
            'intermediate': CANONICAL['yellow'],
            'advanced': CANONICAL['red'],
            '': 'bg-secondary text-muted-foreground',
            'unknown': 'bg-secondary text-muted-foreground',
        }
        for difficulty, expected in cases.items():
            with self.subTest(difficulty=difficulty):
                project.difficulty = difficulty
                self.assertEqual(project.difficulty_color(), expected)

        for path in IN_SCOPE['I1'][1:]:
            source = _source(path)
            self.assertIn('{{ project.difficulty_color }}', source)
            self.assertIn('{{ project.difficulty }}', source)

    def test_shared_tones_change_only_semantic_palette_entries(self):
        expected_semantic = {
            'success': CANONICAL['green'],
            'success_soft': CANONICAL['green'],
            'info': CANONICAL['blue'],
            'danger': CANONICAL['red'],
            'purple': CANONICAL['purple'],
            'warning': CANONICAL['yellow'],
        }
        for tone, expected in expected_semantic.items():
            with self.subTest(tone=tone):
                self.assertEqual(TONE_CLASSES[tone], expected)

        self.assertEqual(TONE_CLASSES['neutral'], 'bg-secondary text-muted-foreground')
        self.assertEqual(TONE_CLASSES['muted'], 'bg-secondary text-muted-foreground')
        self.assertEqual(TONE_CLASSES['accent'], 'bg-accent/10 text-accent')
        self.assertEqual(TONE_CLASSES['accent_strong'], 'bg-accent/20 text-accent')
        self.assertNotIn('orange', TONE_CLASSES, 'No in-scope shared call site needs orange')

    def test_status_map_and_rendered_badges_have_one_semantic_fill_and_foreground(self):
        expected = {
            'submitted': 'warning',
            'pending': 'warning',
            'starting_soon': 'warning',
            'ending_soon': 'warning',
            'ended': 'muted',
            'in_review': 'info',
            'review_complete': 'success',
            'certified': 'purple',
            'cancelled': 'danger',
            'registered': 'success',
        }
        for status, tone in expected.items():
            with self.subTest(status=status):
                self.assertEqual(STATUS_TONES[status], tone)
                html = Template(
                    '{% load member_badges %}'
                    '{% member_status_badge label status=status %}'
                ).render(Context({'label': status, 'status': status}))
                classes = re.search(r'class="([^"]+)"', html).group(1).split()
                self.assertEqual(len([c for c in classes if c.startswith('bg-')]), 1)
                light_text = [c for c in classes if c.startswith('text-')]
                self.assertEqual(len(light_text), 2, classes)  # size + semantic foreground
                dark_text = [c for c in classes if c.startswith('dark:text-')]
                self.assertEqual(len(dark_text), 0 if tone == 'muted' else 1)

    def test_tag_type_table_keeps_five_current_labels_and_canonical_colors(self):
        # Main currently represents learning-path articles through Article;
        # the fifth tag-result owner remains Event, as it did at grooming time.
        actual = {row['type_label']: row['type_color'] for row in CONTENT_TYPES}
        self.assertEqual(
            actual,
            {
                'Article': CANONICAL['blue'],
                'Project': CANONICAL['green'],
                'Course': CANONICAL['orange'],
                'Download': CANONICAL['red'],
                'Event': CANONICAL['yellow'],
            },
        )
        tags_template = _source('templates/content/tags_detail.html')
        self.assertIn('{{ item.type_label }}', tags_template)
        self.assertIn('{{ item.type_color }}', tags_template)
        self.assertIn('href="{{ item.url }}"', tags_template)

    def test_direct_badge_consumers_use_shared_owner_and_preserve_hooks(self):
        def loaded_libraries(source):
            return {
                library
                for load_body in re.findall(
                    r'{%\s*load\s+([^%]+?)\s*%}', source,
                )
                for library in load_body.split()
            }

        for load_tag in (
            '{% load accounts_extras member_badges %}',
            '{% load member_badges accounts_extras %}',
        ):
            with self.subTest(load_tag=load_tag):
                self.assertIn('member_badges', loaded_libraries(load_tag))

        checks = {
            'templates/content/course_detail.html': (
                '{% member_badge "Free"',
                '{% member_badge "Enrolled"',
                'data-testid="continue-button"',
            ),
            'templates/content/peer_review/dashboard.html': (
                'member_status_badge submission.get_status_display',
                'testid="peer-review-status"',
            ),
            'templates/content/peer_review/submit.html': (
                'member_status_badge submission.get_status_display',
                'testid="peer-submission-status"',
            ),
            'templates/events/event_series.html': (
                'testid="series-registered-state"',
                'testid="series-event-state-cancelled"',
                'testid="series-event-state-registered"',
                'extra_class="min-h-[44px] py-1"',
            ),
        }
        for path, fragments in checks.items():
            source = _source(path)
            self.assertIn(
                'member_badges',
                loaded_libraries(source),
                f'{path} must load the shared member_badges owner',
            )
            for fragment in fragments:
                with self.subTest(path=path, fragment=fragment):
                    self.assertIn(fragment, source)

        for path in (
            'templates/plans/sprint_detail.html',
            'templates/content/sprints_index.html',
            'templates/content/dashboard.html',
        ):
            source = _source(path)
            self.assertIn('status=', source)
            self.assertNotIn('extra_class=', source)
            self.assertNotIn('sprint_badge_current.css_class', source)

    def test_plan_markers_and_reader_runtime_are_accessible_and_synchronized(self):
        marker_recipe = 'bg-green-500/15 text-xs text-green-800 dark:text-green-400'
        checkpoint = _source('templates/plans/_checkpoint_card.html')
        plan = _source('templates/plans/_plan_body.html')
        self.assertIn(marker_recipe, checkpoint)
        self.assertEqual(plan.count(marker_recipe), 3)
        self.assertIn('aria-label="Done"', checkpoint)
        self.assertEqual(plan.count('aria-label="Done"'), 3)

        button = _source('templates/content/reader/_completion_button.html')
        scripts = _source('templates/content/reader/_scripts.html')
        runtime_classes = (
            'border-green-500/30', 'bg-green-500/10', 'text-green-800',
            'dark:text-green-400', 'hover:bg-green-500/20',
        )
        for class_name in runtime_classes:
            self.assertIn(class_name, button)
            self.assertEqual(scripts.count(f"'{class_name}'"), 2)
        self.assertIn('focus-visible:ring-2', button)
        self.assertIn('focus-visible:ring-offset-background', button)


class ExhaustiveStatusSweepTest(SimpleTestCase):
    def test_i_and_x_owner_inventory_exists(self):
        for classification in (IN_SCOPE, EXCLUDED):
            for scope_id, paths in classification.items():
                for path in paths:
                    with self.subTest(scope=scope_id, path=path):
                        self.assertTrue((ROOT / path).exists(), f'{scope_id} owner disappeared: {path}')

    def test_public_python_and_template_scan_rejects_unclassified_unsafe_recipes(self):
        roots = ('content', 'templates', 'events', 'plans', 'accounts', 'email_app')
        violations = []
        for root_name in roots:
            for path in (ROOT / root_name).rglob('*'):
                if path.suffix not in {'.py', '.html'}:
                    continue
                rel = path.relative_to(ROOT).as_posix()
                if '/tests/' in rel or rel.startswith('templates/studio/'):
                    continue
                for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                    if UNSAFE_BADGE_RECIPE.search(line) and rel not in UNSAFE_ALLOWLIST:
                        violations.append(f'{rel}:{line_number}: {line.strip()}')
        self.assertEqual(
            violations,
            [],
            'Unclassified light-unsafe compact recipe(s):\n' + '\n'.join(violations),
        )

    def test_x1_studio_file_type_palette_never_reaches_public_templates(self):
        public_templates = [
            path for path in (ROOT / 'templates').rglob('*.html')
            if 'studio' not in path.parts
        ]
        consumers = [
            path.relative_to(ROOT).as_posix()
            for path in public_templates
            if 'file_type_color' in path.read_text(encoding='utf-8')
        ]
        self.assertEqual(consumers, [])

    def test_design_system_locks_recipe_evidence_and_component_boundary(self):
        docs = _source('_docs/design-system.md')
        for fragment in (
            'bg-<color>-500/15 text-<color>-800 dark:text-<color>-400',
            'at least 4.5:1',
            'browser-computed, alpha-composited',
            'A raw `text-<color>-600` class is not',
            'sufficient contrast evidence',
            'alerts,\nbuttons, links, standalone headings, and large decorative/status icons',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, docs)
