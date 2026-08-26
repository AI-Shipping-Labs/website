#!/usr/bin/env python3
"""Map the current git diff to the tests it can plausibly break.

Agent verification used to default to the entire local Django suite
(~14,800 tests) for every issue, which is pure contention with no coverage
benefit: CI already runs the full Django suite on every push to main and
blocks the deploy, and the full Playwright suite runs every 3 hours.

This helper turns ``git diff --name-only`` into a concrete, deterministic
test plan through an ordered rule chain (see ``_docs/testing-guidelines.md``,
section "Affected-tests selection"):

 1. No-test paths (the ``_docs`` / ``docs`` / ``specs`` trees and top-level
    markdown) contribute nothing. A docs-only diff short-circuits to
    ``NO TESTS REQUIRED``. Markdown that lives beside code -- notably
    ``email_app/email_templates/*.md`` -- is NOT a no-test path.
 2. Escalation-trigger paths set the full-Playwright flag and fall through.
 3. Contract paths: CI / build tooling targets the top-level ``tests``
    package, and doc artifacts that a real test reads (``skills/**``,
    ``docs/**``, ``CLAUDE.md``, ``_docs/design-system.md``, ...) target the
    app that reads them. Matching here also exempts a path from rule 1.
 4. ``website/**`` targets ``tests`` + ``website`` + ``make test-core``.
 5. Dependency manifests get a soft ``make test-core`` fallback + a note.
 6. A small curated hub-module map handles known cross-cutting files.
 7. Test files map to their exact dotted test module.
 8. App source files target their app plus a one-hop reverse-import
    expansion (``git grep``), capped at 6 extra app labels.
 9. Templates map to their owning app (+ core Playwright) or, for shared
    fragments, to ``make test-core`` + full Playwright.
10. ``static/**`` runs core Playwright; ``tailwind.config.js`` escalates.
11. Migrations map to their own app; 2+ apps in one diff adds core.
12. Anything unmatched fails closed to ``make test-core`` with a
    ``WARN unmapped:`` line -- never silently dropped.

Stdlib only, and deliberately does NOT import Django: it has to run in well
under a second without loading settings. It never runs tests itself unless
``--run`` is passed (that is what ``make test-affected`` does).

Usage::

    uv run python scripts/affected_tests.py [--base origin/main] [--json]
                                            [--run] [--no-include-untracked]

Exit codes: 0 = plan produced (or, with ``--run``, the worst exit code of the
executed commands), 2 = git failure.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Mapping tables (module-level constants on purpose: tests/test_affected_tests.py
# asserts they still match what is on disk, so the map cannot silently rot).
# ---------------------------------------------------------------------------

#: Project apps from ``INSTALLED_APPS`` (third-party/django apps excluded).
#: ``tests/test_affected_tests.py`` cross-checks this against the live
#: ``INSTALLED_APPS`` so a new app cannot be added without updating the map.
APP_LABELS: tuple[str, ...] = (
    'accounts',
    'analytics',
    'api',
    'bookclub',
    'comments',
    'community',
    'content',
    'crm',
    'email_app',
    'events',
    'integrations',
    'jobs',
    'member_api',
    'notifications',
    'payments',
    'plans',
    'questionnaires',
    'studio',
    'triggers',
    'voting',
)

TESTS_PACKAGE = 'tests'
WEBSITE_PACKAGE = 'website'

CORE_COMMAND = 'make test-core'
PLAYWRIGHT_CORE_COMMAND = 'make test-playwright-core'
PLAYWRIGHT_FULL_COMMAND = 'make test-playwright'
ASL_CLI_COMMAND = 'uv run pytest asl_cli/tests'

DJANGO_COMMAND_PREFIX = 'uv run python manage.py test'
#: ``--parallel 4`` (not bare ``--parallel``): bare spawns one worker per core,
#: which is what melted a 12-core box running several agents at once. CI uses
#: the same bounded value in ``.github/workflows/deploy-dev.yml``.
DJANGO_COMMAND_SUFFIX = '--exclude-tag=visual_regression --exclude-tag=postgres_migration --parallel 4'

#: Rule 1. Directories whose contents need no local test run -- *unless* the
#: path is claimed by ``CONTRACT_PATHS`` below, which always wins.
NO_TEST_DIRS: tuple[str, ...] = ('_docs', 'docs', 'specs')

#: Rule 1 is deliberately narrow: those trees plus top-level markdown, minus
#: everything a real test reads. Markdown that lives anywhere else is treated
#: as code and falls through the rest of the chain, because plenty of it is
#: asserted:
#:   * ``email_app/email_templates/*.md`` -- the shipped email bodies, covered
#:     by email_app tests (double-dash, markdown parity, verification copy);
#:   * ``.claude/**`` -- agent/skill content guards in ``tests/``;
#:   * ``skills/**`` and ``docs/member-api/**`` -- read by
#:     ``member_api/tests/test_usage_docs_1112.py`` and served by
#:     ``member_api/views/docs.py``;
#:   * ``CLAUDE.md`` / ``AGENTS.md`` / ``README.md`` / ``_docs/PROCESS.md`` /
#:     ``_docs/testing-guidelines.md`` -- rot guards in ``tests/``.
PLAYWRIGHT_FILE_COMMAND_PREFIX = 'uv run pytest playwright_tests/'

#: Rule 2. Escalation triggers -> full local Playwright. Supersedes the prose
#: list that used to live in ``_docs/PROCESS.md``.
ESCALATION_TRIGGERS: tuple[tuple[str, str], ...] = (
    ('playwright_tests/conftest.py', 'shared fixtures'),
    ('tests/fixtures.py', 'shared fixtures'),
    ('content/access.py', 'access-control matrix'),
    ('content/tier_config.py', 'access-control matrix'),
    ('accounts/gating.py', 'access-control matrix'),
    ('playwright_tests/test_access_control.py', 'access-control matrix'),
    ('payments/tier_state.py', 'payments wiring'),
    ('payments/stripe_links.py', 'payments wiring'),
    # Webhook handlers live under payments/services/, so the two globs below
    # cover the whole "services, views, tier_state, stripe_links, webhook
    # handlers" set without dragging in payments/tests/*webhook*.py.
    ('payments/services/*', 'payments wiring'),
    ('payments/views/*', 'payments wiring'),
    ('templates/includes/*', 'shared template fragments'),
    ('templates/_partials/*', 'shared template fragments'),
    ('templates/base.html', 'shared template fragments'),
    ('website/*', 'every-request/every-page surface'),
    ('accounts/context_processors.py', 'every-request/every-page surface'),
    ('tailwind.config.js', 'content-purge config, can strip classes on any page'),
    ('integrations/middleware.py', 'every request'),
)

#: Rule 5. Soft triggers: note only, no forced local full Playwright.
DEPENDENCY_MANIFESTS: tuple[str, ...] = ('pyproject.toml', 'uv.lock')

#: Rule 3. Contract surfaces: files that are not app source but that a real
#: test asserts. Each entry maps a glob to the labels that own it, and matching
#: here also exempts the path from rule 1 (so a guarded doc artifact can never
#: be short-circuited away as "just markdown").
#:
#: The doc-artifact half of this table is kept honest by
#: ``tests/test_affected_tests.py``: it scans every Django test module for
#: files it actually reads under ``_docs/``, ``docs/``, ``specs/``, ``skills/``
#: and the top level, and fails if a reader is not covered here. Docs that are
#: merely *mentioned* by a test (a ``docs_url`` string in the settings
#: registry, say) are deliberately not listed -- renaming them cannot break
#: that assertion.
CONTRACT_PATHS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # CI / build / tooling surfaces owned by the top-level ``tests`` package.
    ('.github/*', (TESTS_PACKAGE,)),
    ('scripts/*', (TESTS_PACKAGE,)),
    ('Makefile', (TESTS_PACKAGE,)),
    ('Dockerfile', (TESTS_PACKAGE,)),
    ('docker-compose.yml', (TESTS_PACKAGE,)),
    ('entrypoint.sh', (TESTS_PACKAGE,)),
    ('Procfile.dev', (TESTS_PACKAGE,)),
    ('deploy/*', (TESTS_PACKAGE,)),
    ('package.json', (TESTS_PACKAGE,)),
    ('package-lock.json', (TESTS_PACKAGE,)),
    ('.claude/*', (TESTS_PACKAGE,)),
    ('manage.py', (TESTS_PACKAGE,)),
    # Agent instructions and process docs with rot guards in tests/.
    ('CLAUDE.md', (TESTS_PACKAGE,)),
    ('AGENTS.md', (TESTS_PACKAGE,)),
    ('README.md', (TESTS_PACKAGE,)),
    ('_docs/PROCESS.md', (TESTS_PACKAGE,)),
    ('_docs/testing-guidelines.md', (TESTS_PACKAGE,)),
    # Shipped doc artifacts read by app tests.
    ('_docs/content.md', ('content',)),
    ('_docs/design-system.md', ('accounts', 'content')),
    ('_docs/integrations/*', ('integrations',)),
    ('_docs/member-openapi.json', ('member_api',)),
    ('_docs/openapi.json', ('api',)),
    ('_docs/product.md', ('content',)),
    ('specs/06-content-resources.md', ('content',)),
    ('specs/07-events.md', ('content',)),
    ('specs/README.md', ('content',)),
    # The member-API guide and operator skill are asserted by
    # member_api/tests/test_usage_docs_1112.py (and served by
    # member_api/views/docs.py).
    ('docs/*', ('member_api',)),
    ('skills/*', ('member_api',)),
)

#: Rule 6. Curated hub-module map: (glob, extra django labels, add ``make test-core``).
#: Playwright escalation for these paths comes from ESCALATION_TRIGGERS above.
HUB_MODULE_MAP: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ('integrations/config.py', ('integrations', TESTS_PACKAGE), True),
    ('integrations/settings_registry.py', ('integrations', TESTS_PACKAGE), True),
    ('content/access.py', ('content', 'accounts'), True),
    ('content/tier_config.py', ('content', 'accounts'), True),
    ('accounts/gating.py', ('content', 'accounts'), True),
    ('tests/fixtures.py', (), True),
    ('payments/tier_state.py', ('payments', 'accounts', 'api'), False),
    ('payments/stripe_links.py', ('payments', 'accounts', 'api'), False),
    ('payments/services/*', ('payments', 'accounts', 'api'), False),
    ('payments/views/*', ('payments', 'accounts', 'api'), False),
    ('accounts/models/*', ('accounts',), True),
    ('accounts/auth.py', ('accounts',), True),
    ('accounts/adapters.py', ('accounts',), True),
    ('accounts/signals.py', ('accounts',), True),
)

#: Rule 9. Shared template fragments -> core Django + full Playwright.
SHARED_TEMPLATE_GLOBS: tuple[str, ...] = (
    'templates/includes/*',
    'templates/_partials/*',
    'templates/base.html',
)

#: Rule 8. Cap on the one-hop reverse-import expansion. Above this, substitute
#: ``make test-core`` for the expansion so a hub module cannot silently
#: reconstruct a full-suite-equivalent run.
REVERSE_IMPORT_APP_CAP = 6

#: Rule 8. Where the reverse-import grep looks.
GREP_PATHSPECS: tuple[str, ...] = tuple(
    f'{directory}/*.py' for directory in (*APP_LABELS, TESTS_PACKAGE, 'playwright_tests')
)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    base: str
    files: list[str] = field(default_factory=list)
    django_labels: list[str] = field(default_factory=list)
    django_command: str | None = None
    extra_commands: list[str] = field(default_factory=list)
    playwright: str = 'core'
    escalation_reasons: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def no_tests_required(self) -> bool:
        return self.playwright == 'none' and not self.django_command and not self.extra_commands

    def to_dict(self) -> dict:
        return {
            'base': self.base,
            'files': self.files,
            'django_labels': self.django_labels,
            'django_command': self.django_command,
            'extra_commands': self.extra_commands,
            'playwright': self.playwright,
            'escalation_reasons': self.escalation_reasons,
            'unmapped': self.unmapped,
            'notes': self.notes,
        }

    def commands(self) -> list[str]:
        """Every command the plan asks for, in execution order.

        When the plan escalated to the full Playwright suite, per-file
        ``pytest playwright_tests/test_X.py`` commands are dropped: the full
        run already covers them, and re-running them would boot another
        server + browser per changed file.
        """
        ordered: list[str] = []
        if self.django_command:
            ordered.append(self.django_command)
        ordered.extend(
            command
            for command in self.extra_commands
            if not (self.playwright == 'full' and command.startswith(PLAYWRIGHT_FILE_COMMAND_PREFIX))
        )
        if self.playwright == 'core':
            ordered.append(PLAYWRIGHT_CORE_COMMAND)
        elif self.playwright == 'full':
            ordered.append(PLAYWRIGHT_FULL_COMMAND)
        return ordered


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _matches(path: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, glob) for glob in globs)


def _top_dir(path: str) -> str:
    return path.split('/')[0]


def contract_labels(path: str) -> tuple[str, ...] | None:
    """Rule 3: the labels that own ``path``, or ``None`` if it is not a
    contract surface. First match in ``CONTRACT_PATHS`` wins."""
    for glob, labels in CONTRACT_PATHS:
        if fnmatch.fnmatchcase(path, glob):
            return labels
    return None


def is_no_test_path(path: str) -> bool:
    """Rule 1: the docs trees and top-level markdown need no local test run.

    A path claimed by ``CONTRACT_PATHS`` is never a no-test path, so a doc
    artifact that a real test reads cannot be short-circuited away. Markdown
    beside code is not a no-test path either -- see the note on
    ``NO_TEST_DIRS``.
    """
    if contract_labels(path) is not None:
        return False
    if _top_dir(path) in NO_TEST_DIRS:
        return True
    return path.endswith('.md') and '/' not in path


def dotted_module(path: str) -> str:
    """``accounts/models/user.py`` -> ``accounts.models.user``."""
    without_suffix = path[:-3] if path.endswith('.py') else path
    if without_suffix.endswith('/__init__'):
        without_suffix = without_suffix[: -len('/__init__')]
    return without_suffix.replace('/', '.')


# ---------------------------------------------------------------------------
# Rule 8: one-hop reverse-import expansion
# ---------------------------------------------------------------------------


class GitError(RuntimeError):
    """Raised when a git invocation the helper depends on fails."""


def reverse_import_patterns(module: str, *, repo_root: Path = REPO_ROOT) -> list[str]:
    """Regexes that find one-hop references to ``module``.

    Covers real import statements and quoted dotted paths -- the latter is
    what binds ``mock.patch('integrations.services.zoom.create_meeting')``
    style tests to the module they patch.
    """
    escaped = re.escape(module)
    patterns = [
        rf'(from|import)[ \t]+{escaped}([^A-Za-z0-9_.]|$)',
        rf'["\']{escaped}[."\']',
    ]
    if '.' in module:
        parent, _, leaf = module.rpartition('.')
        escaped_parent = re.escape(parent)
        escaped_leaf = re.escape(leaf)
        # from a.b import c
        patterns.append(rf'from[ \t]+{escaped_parent}[ \t]+import[ \t][^\n]*{escaped_leaf}([^A-Za-z0-9_]|$)')
        if _parent_reexports(parent, leaf, repo_root=repo_root):
            # The package __init__ re-exports this module, so references to
            # the parent package resolve to the changed code too.
            patterns.append(rf'(from|import)[ \t]+{escaped_parent}([^A-Za-z0-9_.]|$)')
            patterns.append(rf'["\']{escaped_parent}[."\']')
    return patterns


def _parent_reexports(parent: str, leaf: str, *, repo_root: Path = REPO_ROOT) -> bool:
    init_path = repo_root / Path(parent.replace('.', '/')) / '__init__.py'
    try:
        text = init_path.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return False
    return bool(re.search(rf'from[ \t]+\.{re.escape(leaf)}[ \t]+import', text))


def git_grep_references(modules: list[str], *, repo_root: Path = REPO_ROOT) -> dict[str, list[str]]:
    """Return ``{module: [referencing files]}`` using a single ``git grep``.

    One grep for the whole diff (attribution happens in Python) keeps the
    helper well under its 1s budget even for multi-file changes.
    """
    if not modules:
        return {}

    per_module = {module: reverse_import_patterns(module, repo_root=repo_root) for module in modules}
    combined = '|'.join(pattern for patterns in per_module.values() for pattern in patterns)

    completed = subprocess.run(
        ['git', 'grep', '-I', '-n', '--untracked', '-E', combined, '--', *GREP_PATHSPECS],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    # git grep exits 1 when nothing matched; anything above that is a real error.
    if completed.returncode > 1:
        raise GitError(completed.stderr.strip() or 'git grep failed')

    compiled = {
        module: re.compile('|'.join(patterns)) for module, patterns in per_module.items()
    }
    references: dict[str, list[str]] = {module: [] for module in modules}
    seen: set[tuple[str, str]] = set()
    for line in completed.stdout.splitlines():
        path, _, rest = line.partition(':')
        _, _, text = rest.partition(':')
        if not path.endswith('.py'):
            continue
        for module, regex in compiled.items():
            if regex.search(text) and (module, path) not in seen:
                seen.add((module, path))
                references[module].append(path)
    return references


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------


def _summarize(items, limit: int = 3) -> str:
    """Render a bounded, deterministic ``a, b, c and N more`` list."""
    ordered = sorted(items)
    if len(ordered) <= limit:
        return ', '.join(ordered)
    return f'{", ".join(ordered[:limit])} and {len(ordered) - limit} more'


def _collapse_labels(labels: set[str]) -> list[str]:
    """Drop submodules already covered by a selected package label."""
    kept = []
    for label in sorted(labels):
        if any(label != other and label.startswith(f'{other}.') for other in labels):
            continue
        kept.append(label)
    return kept


def build_plan(
    files,
    *,
    base: str = 'origin/main',
    expander=None,
    repo_root: Path = REPO_ROOT,
) -> Plan:
    """Apply the ordered rule chain to ``files`` and return a :class:`Plan`."""
    expand = expander
    if expand is None:
        def expand(modules):
            return git_grep_references(modules, repo_root=repo_root)

    ordered_files = sorted({path for path in files if path})
    plan = Plan(base=base, files=ordered_files)

    if not ordered_files:
        plan.playwright = 'none'
        plan.notes.append('No changes detected against the base.')
        return plan

    # Rule 1 -- no-test paths contribute nothing.
    relevant = [path for path in ordered_files if not is_no_test_path(path)]
    if not relevant:
        plan.playwright = 'none'
        plan.notes.append('Docs-only diff (docs / specs / markdown): nothing to run locally.')
        return plan

    labels: set[str] = set()
    extras: list[str] = []
    notes: list[str] = []
    escalations: list[str] = []
    unmapped: list[str] = []
    expansion_modules: list[str] = []
    migration_apps: set[str] = set()

    def add_extra(command: str) -> None:
        if command not in extras:
            extras.append(command)

    def add_note(note: str) -> None:
        if note not in notes:
            notes.append(note)

    # Rule 2 -- escalation triggers (flag only; the file still falls through).
    for path in relevant:
        for glob, reason in ESCALATION_TRIGGERS:
            if fnmatch.fnmatchcase(path, glob):
                entry = f'{path}: {reason}'
                if entry not in escalations:
                    escalations.append(entry)
                break

    for path in relevant:
        top = _top_dir(path)

        # Rule 3 -- contract surfaces (CI/build tooling and guarded doc artifacts).
        contract = contract_labels(path)
        if contract is not None:
            labels.update(contract)
            continue

        # Rule 4 -- project settings / urls / middleware: every-request blast radius.
        if top == WEBSITE_PACKAGE:
            labels.update({TESTS_PACKAGE, WEBSITE_PACKAGE})
            add_extra(CORE_COMMAND)
            continue

        # Rule 5 -- dependency manifests.
        if path in DEPENDENCY_MANIFESTS:
            add_extra(CORE_COMMAND)
            add_note(
                f'NOTE dependency-manifest: {path} changed -- ran {CORE_COMMAND} only; '
                'rely on CI for the full Django suite.'
            )
            continue

        # Rule 6 -- curated hub modules.
        hub_match = next(
            ((glob, hub_labels, add_core) for glob, hub_labels, add_core in HUB_MODULE_MAP
             if fnmatch.fnmatchcase(path, glob)),
            None,
        )
        if hub_match is not None:
            _, hub_labels, add_core = hub_match
            labels.update(hub_labels)
            if add_core:
                add_extra(CORE_COMMAND)
            continue

        # Rule 7 -- test files map to their exact test module.
        if top == 'playwright_tests':
            if path.endswith('.py') and Path(path).name.startswith('test_'):
                add_extra(f'uv run pytest {path} -v')
            continue
        if top == 'asl_cli':
            add_extra(ASL_CLI_COMMAND)
            continue
        if top == TESTS_PACKAGE and path.endswith('.py'):
            labels.add(dotted_module(path))
            continue
        if top in APP_LABELS and f'/{TESTS_PACKAGE}/' in path and path.endswith('.py'):
            name = Path(path).name
            labels.add(dotted_module(path) if name.startswith('test_') else f'{top}.{TESTS_PACKAGE}')
            continue

        # Rule 11 -- migrations map to their own app (no reverse expansion).
        if top in APP_LABELS and '/migrations/' in path:
            labels.add(top)
            migration_apps.add(top)
            continue

        # Rule 8 -- app source: the owning app plus one-hop reverse imports.
        if top in APP_LABELS:
            labels.add(top)
            if path.endswith('.py'):
                expansion_modules.append(path)
            continue

        # Rule 9 -- templates.
        if top == 'templates':
            if _matches(path, SHARED_TEMPLATE_GLOBS):
                labels.add('content')
                add_extra(CORE_COMMAND)
                continue
            template_app = path.split('/')[1] if '/' in path else ''
            if template_app in APP_LABELS:
                # Core Playwright is already the default for every non-docs diff.
                labels.add(template_app)
                continue
            add_extra(CORE_COMMAND)
            add_note(f'NOTE template-fallback: {path} has no owning app -- added {CORE_COMMAND}.')
            continue

        # Rule 10 -- static assets and the Tailwind purge config.
        if top == 'static':
            # No Django target: core Playwright (the default) owns rendering.
            continue
        if path == 'tailwind.config.js':
            # Escalated to full Playwright by rule 2; no Django target.
            continue

        # Rule 12 -- fail closed.
        unmapped.append(path)
        add_extra(CORE_COMMAND)

    # Rule 11 -- migrations across 2+ apps in one diff.
    if len(migration_apps) >= 2:
        add_extra(CORE_COMMAND)
        add_note(
            'NOTE multi-app-migration: migrations touch '
            f'{", ".join(sorted(migration_apps))} -- added {CORE_COMMAND}.'
        )

    # Rule 8 (continued) -- one grep for every changed app module.
    if expansion_modules:
        modules = [dotted_module(path) for path in expansion_modules]
        references = expand(modules)
        for module in modules:
            hits = references.get(module, [])
            owning_app = module.split('.')[0]
            expansion: set[str] = set()
            test_modules: set[str] = set()
            playwright_hits: list[str] = []
            for hit in hits:
                hit_top = _top_dir(hit)
                if hit_top == 'playwright_tests':
                    playwright_hits.append(hit)
                elif hit_top == TESTS_PACKAGE:
                    expansion.add(TESTS_PACKAGE)
                    test_modules.add(dotted_module(hit))
                elif hit_top in APP_LABELS and hit_top != owning_app:
                    expansion.add(hit_top)
            if len(expansion) > REVERSE_IMPORT_APP_CAP:
                add_extra(CORE_COMMAND)
                add_note(
                    f'NOTE broad-impact: {module} is referenced by {len(expansion)} apps '
                    f'({", ".join(sorted(expansion))}) -- substituted {CORE_COMMAND} for the expansion.'
                )
            else:
                labels.update(expansion - {TESTS_PACKAGE})
                labels.update(test_modules)
            if playwright_hits:
                add_note(
                    f'NOTE playwright-refs: {module} is referenced by '
                    f'{_summarize(playwright_hits)} (covered by {PLAYWRIGHT_CORE_COMMAND}).'
                )

    plan.django_labels = _collapse_labels(labels)
    if plan.django_labels:
        plan.django_command = (
            f'{DJANGO_COMMAND_PREFIX} {" ".join(plan.django_labels)} {DJANGO_COMMAND_SUFFIX}'
        )
    plan.playwright = 'full' if escalations else 'core'

    if plan.playwright == 'full':
        # The full suite already runs every Playwright file, so per-file
        # invocations would only re-boot a server and a browser per file.
        superseded = [c for c in extras if c.startswith(PLAYWRIGHT_FILE_COMMAND_PREFIX)]
        if superseded:
            extras = [c for c in extras if c not in superseded]
            add_note(
                f'NOTE playwright-full-supersedes: dropped {len(superseded)} per-file Playwright '
                f'command(s) already covered by {PLAYWRIGHT_FULL_COMMAND}.'
            )

    plan.extra_commands = extras
    plan.escalation_reasons = escalations
    plan.unmapped = unmapped
    plan.notes = notes
    for path in unmapped:
        plan.notes.append(f'WARN unmapped: {path} -- no rule matched, fell back to {CORE_COMMAND}.')
    return plan


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------


def _git(args: list[str], *, repo_root: Path = REPO_ROOT) -> str:
    completed = subprocess.run(
        ['git', *args], cwd=repo_root, capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise GitError(f'git {" ".join(args)}: {completed.stderr.strip()}')
    return completed.stdout


def changed_files(
    base: str = 'origin/main',
    *,
    include_untracked: bool = True,
    repo_root: Path = REPO_ROOT,
) -> tuple[list[str], str]:
    """Files changed vs ``merge-base(base, HEAD)``, plus local uncommitted work.

    SWE work is uncommitted when the tester runs, so unstaged, staged, and
    (by default) untracked files are unioned into the diff.
    """
    merge_base = _git(['merge-base', base, 'HEAD'], repo_root=repo_root).strip()
    collected: set[str] = set()
    collected.update(_git(['diff', '--name-only', f'{merge_base}..HEAD'], repo_root=repo_root).split())
    collected.update(_git(['diff', '--name-only'], repo_root=repo_root).split())
    collected.update(_git(['diff', '--name-only', '--cached'], repo_root=repo_root).split())
    if include_untracked:
        collected.update(
            _git(['ls-files', '--others', '--exclude-standard'], repo_root=repo_root).split()
        )
    return sorted(collected), merge_base


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def render_text(plan: Plan, merge_base: str) -> str:
    lines = [f'Affected-tests plan (base: {plan.base} @ {merge_base[:12]})', '']
    lines.append(f'Changed files ({len(plan.files)}):')
    lines.extend(f'  {path}' for path in plan.files)
    lines.append('')

    for note in plan.notes:
        lines.append(note)
    if plan.notes:
        lines.append('')

    if plan.no_tests_required:
        lines.append('NO TESTS REQUIRED')
        return '\n'.join(lines)

    if plan.escalation_reasons:
        lines.append('Playwright: full (escalated)')
        lines.extend(f'  ESCALATE {reason}' for reason in plan.escalation_reasons)
    else:
        lines.append('Playwright: core')
    lines.append('')

    lines.append('Commands:')
    lines.extend(f'  {command}' for command in plan.commands())
    return '\n'.join(lines)


def run_commands(plan: Plan, *, repo_root: Path = REPO_ROOT) -> int:
    worst = 0
    for command in plan.commands():
        print(f'\n$ {command}', flush=True)
        completed = subprocess.run(command, shell=True, cwd=repo_root)
        worst = max(worst, completed.returncode)
    return worst


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--base', default='origin/main', help='base ref (default: origin/main)')
    parser.add_argument('--json', action='store_true', help='emit the plan as JSON')
    parser.add_argument(
        '--include-untracked',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='union untracked files into the diff (default: enabled)',
    )
    parser.add_argument('--run', action='store_true', help='execute the emitted commands')
    args = parser.parse_args(argv)

    try:
        files, merge_base = changed_files(args.base, include_untracked=args.include_untracked)
        plan = build_plan(files, base=args.base)
    except GitError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(plan.to_dict(), indent=2))
    else:
        print(render_text(plan, merge_base))

    if args.run:
        if plan.no_tests_required:
            print('\nNothing to run.')
            return 0
        return run_commands(plan)
    return 0


if __name__ == '__main__':
    sys.exit(main())
