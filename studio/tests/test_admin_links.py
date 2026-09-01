# admin-link-guard: policy-definition
"""Prevent routine staff workflows from growing Django-admin links.

The guard scans runtime code, templates, operator documentation, and every
repository test tree. Runtime exceptions are exact source lines. Reviewed
test-only references are pinned by both occurrence count and content digest,
so adding, removing, duplicating, or replacing a reference fails closed.
"""

import json
import re
from collections import Counter
from hashlib import sha256
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
SCANNED_SUFFIXES = frozenset({'.html', '.md', '.py'})
SELF_PATH = Path('studio/tests/test_admin_links.py')
SELF_GUARD_MARKER = '# admin-link-guard: policy-definition'
ADMIN_MOUNT_MARKER = "path('admin/', admin.site.urls)"
FORBIDDEN_MARKERS = (
    '/admin/',
    'admin_change_url',
    'studio-open-in-admin',
    '_admin_link.html',
)
ADMIN_NAMESPACE_PATTERN = re.compile(
    r"(?:reverse|reverse_lazy|redirect)\s*\(\s*['\"]admin:"
    r"|\{%[-]?\s*url\s+['\"]admin:",
)

# These are application-owned roots, not exclusions. Tests and migrations
# below them are scanned alongside runtime modules. Top-level test trees are
# added separately below.
APPLICATION_ROOTS = tuple(Path(name) for name in (
    'accounts',
    'analytics',
    'api',
    'asl_cli',
    'bookclub',
    'comments',
    'community',
    'content',
    'crm',
    'deploy',
    'email_app',
    'events',
    'integrations',
    'jobs',
    'member_api',
    'notifications',
    'payments',
    'plans',
    'questionnaires',
    'scripts',
    'studio',
    'triggers',
    'voting',
    'website',
))
SCANNED_ROOTS = APPLICATION_ROOTS + (
    Path('tests'),
    Path('playwright_tests'),
    Path('templates'),
    Path('email_app/email_templates'),
    Path('README.md'),
    Path('_docs'),
    Path('docs'),
    Path('specs'),
    Path('manage.py'),
)

# Exact reviewed non-test references. Each entry must still exist exactly once,
# must remain in the scan, and must itself contain a guarded marker.
ALLOWED_RUNTIME_LINES = {
    'README.md': {
        'Log in at http://localhost:8000/accounts/login/ with any of these emails. Routine staff work is under `/studio/`; the unlinked low-level Django admin is at `http://localhost:8000/admin/` (use the admin account).',
    },
    '_docs/audits/2026-08-13-test-suite-audit.md': {
        '`wait_for_timeout`-then-assert-nothing patterns in studio, 9 needless `/admin/login/`',
    },
    '_docs/integrations/slack.md': {
        'https://<workspace>.slack.com/admin/invites',
    },
    '_docs/product.md': {
        'Uses Studio at `/studio/` for routine content, event, campaign, subscriber, and community operations, including content-source registration, sync history, and manual syncs. Django admin remains directly available at `/admin/` only as an unlinked low-level framework and forensic surface.',
        '| Django admin | `/admin/` | Unlinked low-level framework and forensic model surface; routine operator actions live in Studio | Superusers/staff | Shipped |',
    },
    'accounts/return_context.py': {
        '- auth/logout/admin/member-only destinations are rejected via the same',
    },
    'analytics/middleware.py': {
        "'/admin/',",
    },
    'content/admin/widgets.py': {
        "'all': ('css/admin/timestamp_editor.css',),",
        "js = ('js/admin/timestamp_editor.js',)",
    },
    'integrations/middleware.py': {
        "SKIP_PREFIXES = ('/admin/', '/accounts/', '/account/', '/onboarding/', '/studio/', '/static/', '/media/')",
    },
    'specs/04-content-articles.md': {
        '- R-ART-6: Routine staff article management uses `/studio/articles/`; authenticated production automation remains under the documented bearer API rather than session-authenticated `/api/admin/*` routes.',
    },
    'specs/05-content-courses.md': {
        '- R-CRS-7: Studio owns course, module, and unit management. Course-scoped module reordering posts to `/studio/courses/{id}/modules/reorder`; the legacy session `/api/admin/modules/reorder` and `/api/admin/units/reorder` routes do not exist.',
    },
    'specs/11-voting.md': {
        '### `/admin/polls`',
        '### `/admin/polls/new`',
        '- R-VOT-6: Admin endpoints: `POST /api/admin/polls` (create), `PUT /api/admin/polls/{id}` (edit/close), `DELETE /api/admin/polls/{id}`, `POST /api/admin/polls/{id}/options` (add option), `DELETE /api/admin/polls/{id}/options/{option_id}`.',
    },
    'triggers/models/subscription.py': {
        '# Model-level enforcement covers scripts/admin/API, not just ModelForms.',
    },
    'website/context_processors.py': {
        '- the request is for /studio/... or /admin/... (banner is public-only).',
        "if path.startswith('/studio/') or path.startswith('/admin/'):",
    },
    'website/urls.py': {
        "path('admin/', admin.site.urls),",
    },
}


class _TestSnapshot:
    def __init__(self, count, digest):
        self.count = count
        self.digest = digest
        self.reason = (
            'Exact reviewed test-only references: negative no-admin contracts, '
            'retired-route assertions, or direct low-level diagnostic setup.'
        )


# This is an exact occurrence allowlist, not a file exemption. The digest is
# computed from the sorted, stripped matching lines including duplicates.
# Any new, removed, or replaced match invalidates the entry.
ALLOWED_TEST_SNAPSHOTS = {
    'accounts/tests/test_auth.py': _TestSnapshot(2, '30b0eaf358ae5aac495b09e5e761bc7d7153c6e724ab15f9e935b4c5cb981e82'),
    'accounts/tests/test_email_auth.py': _TestSnapshot(1, '029fc63111c72ae18bbb447ad47fb60a8fa172f4638f292902c3f050816336fe'),
    'accounts/tests/test_login_url_tag.py': _TestSnapshot(1, 'b45aa3c452ea101e4c4e484f794b69f97186f48e355390650c0c9e0bd07935f6'),
    'accounts/tests/test_member_api_keys.py': _TestSnapshot(1, '029fc63111c72ae18bbb447ad47fb60a8fa172f4638f292902c3f050816336fe'),
    'accounts/tests/test_privacy.py': _TestSnapshot(2, '81f059aaed521bc1fece90a7c8a75df67259ebe2cef830c31f2a6c0d520bf192'),
    'accounts/tests/test_return_context.py': _TestSnapshot(2, '11ec6e495175e9689421fd135457c9323e52b4718ff250963081097f26ce7ab9'),
    'analytics/tests/test_middleware.py': _TestSnapshot(1, '488dc8587bb40348135db82bcba70f19983ffabc957c8e94373e8abbc5b407f5'),
    'content/tests/test_blog.py': _TestSnapshot(5, '73827bdca2964420abe8b341fe14cfb22128bda48cb7fd96f168d5a6a11d8114'),
    'content/tests/test_cohorts.py': _TestSnapshot(3, '71eb7446fc969db3b2a5201176453d530e1c40e3295d88ad3e2a93a600b3e692'),
    'content/tests/test_course_admin.py': _TestSnapshot(22, '347e453a29ae0dd113939fc23ceb923f1874a1fa38b48f1be0db7cabd4d3d4b3'),
    'content/tests/test_course_purchase.py': _TestSnapshot(1, 'ac11da78dae108fcc9dec351090bdbc24e76c1a5b7462fe260ab1c3c56f1b696'),
    'content/tests/test_curated_links.py': _TestSnapshot(4, 'fa4c1b1629f9211dc0160cfbad96c348874d1351d84fbaf00eb86de1a74e839b'),
    'content/tests/test_downloads.py': _TestSnapshot(5, '86cf3393bc4f039f8eff3cb86b21ddb2c698b5076fce0db1a5d2e2f3eb1ea208'),
    'content/tests/test_projects.py': _TestSnapshot(5, '66f6d5828ee567bb3f2bb55fe7503a1a081ccd1f62883833c8eabb15ec1949b6'),
    'content/tests/test_tags.py': _TestSnapshot(4, 'd90130b42963567306e2d809c45ffa362d8c0826e6e8d03dee62085af1e0772e'),
    'content/tests/test_video_player.py': _TestSnapshot(2, 'b3aacb40249165ae734502fc087297bd2a0b7f10c1574a18ed48ad0b3a1a03a7'),
    'content/tests/test_workshop_materials.py': _TestSnapshot(1, 'c79282e039d20cabd83f8231fd7802512d697edd4286c4c5241267535839290e'),
    'content/tests/test_workshops.py': _TestSnapshot(4, '51955b355f10f0fc5f3c965c7d4bec7800b79d6001e704cf0bb02a6565dff9e6'),
    'crm/tests/test_onboarding_notify.py': _TestSnapshot(4, 'd319143d3621501e9bdde6c95ea98dac1852c1a3b7d856e79c0cef13b1de6206'),
    'email_app/tests/test_campaigns.py': _TestSnapshot(6, '7b041506e361664b0eafe41f254dde85b568218ee98ace63567f955029af82c0'),
    'email_app/tests/test_newsletter.py': _TestSnapshot(4, '61107babc8c02b1de7ea4b9b62c656132a358771b47c05abd3d2824677c7e7bc'),
    'events/tests/test_events.py': _TestSnapshot(2, 'be47e91290d9a0ec91c522d53dbc122705650a94252cb0a3d819411a5c83f395'),
    'integrations/tests/test_announcement_banner.py': _TestSnapshot(1, '5dc3a0215dcd9297d76905e313b53043a9a83a26f46e2445bf539669c86c357f'),
    'integrations/tests/test_github_sync.py': _TestSnapshot(5, 'b82b9b3037d41103303caea9bc2ae89b8dfafd22c7d059329f0eef8fd6e683ce'),
    'integrations/tests/test_middleware.py': _TestSnapshot(1, '779fa209e2cb99b13cc071123a93791513e0ed478bb72a49a66d520bd2ddac48'),
    'integrations/tests/test_zoom.py': _TestSnapshot(5, '3ff816c37e7d6eb40c75363fe972116b4118605f7aa37eb544e365eca02122ee'),
    'payments/tests/test_conversion_attribution.py': _TestSnapshot(4, '193417554ef6e9bdbd18d089819da6639d4e11691efc280c1103ee67ee5cb1fd'),
    'payments/tests/test_tier.py': _TestSnapshot(3, '9dc3c3a23c0d60946ba0c6da96e9413aab02b19fed0aa95411133008f2d1299f'),
    'plans/tests/test_views_admin_link.py': _TestSnapshot(6, '8c4b7c60bed15b24ba7a41aee1de89037f2ce902b368f98542931d8f3dda0f65'),
    'plans/tests/test_views_ask_team.py': _TestSnapshot(4, '16e93a82380ffab7815f846e5b4affb6a92f37a4c39e5aa48b1dd9bae59bc262'),
    'playwright_tests/test_articles_blog.py': _TestSnapshot(2, '6c58ee61763e6d67b37d6afd3a5b76818cd2fb1b4d7b2487db1009b634e90df6'),
    'playwright_tests/test_community_slack.py': _TestSnapshot(3, '6aac05d47515cee586d5023703d637bca3b2e6d764ff488f4079234e8b67d78f'),
    'playwright_tests/test_content_comment_notifications.py': _TestSnapshot(1, '0f999fcd42fa5ce40d30892bf7d75b9666fb421560ec8998d5cd764464391a61'),
    'playwright_tests/test_course_admin.py': _TestSnapshot(1, '0ce849e07a6aca0172be476be49c4162d53878247d0a14e75af0ce8980e0a984'),
    'playwright_tests/test_crm_activity_context_1054.py': _TestSnapshot(1, '728a4e08289a33546a17cf99b716403519e8bb5b3168626471ffeffc5cfc2fa3'),
    'playwright_tests/test_event_url_canonicalization_673.py': _TestSnapshot(1, '6cb8c468d606445e798e455ff6af359a084d744f9f030bc5ad215666c5fd4018'),
    'playwright_tests/test_github_content_sync.py': _TestSnapshot(6, '13913b9d7d3dbdcb387260711417b5061a0978ada80782d57a322a63fb16fc69'),
    'playwright_tests/test_legacy_url_guard_595.py': _TestSnapshot(1, 'e57eed001d0b537e54a0c185ca07870a354b2b4331a69d46854c7a15a601ac17'),
    'playwright_tests/test_onboarding_notification_studio_983.py': _TestSnapshot(7, '7a9610bf1eb3a14a4f92508d1667570e8a6efc6535e67643787176079968c73a'),
    'playwright_tests/test_search_indexing_policy_1379.py': _TestSnapshot(1, 'b9542ca64e5a55c190caff0f0756b6b3394f085652be66fb778863c3ff3b067f'),
    'playwright_tests/test_sprint_member_actions_585.py': _TestSnapshot(1, '2e90a5deaf691064253bfee2f3aa196c80b88b4ef00506c92e3f49c4d493343d'),
    'playwright_tests/test_studio_campaigns.py': _TestSnapshot(1, '9df0b5a4a2b2f01adbda2d2e2e3c2028536184909daf3d70b7efbb9a2e83fe35'),
    'playwright_tests/test_studio_edit_button.py': _TestSnapshot(2, 'a03e06e4c50f385f193a07aa92c0005ac72d8deff33b38e42237429781f23940'),
    'playwright_tests/test_studio_user_activity_public_links_1052.py': _TestSnapshot(1, '728a4e08289a33546a17cf99b716403519e8bb5b3168626471ffeffc5cfc2fa3'),
    'playwright_tests/test_studio_user_crm_overview.py': _TestSnapshot(2, 'c94c38637cea8d445a89a5715dbf4b7ecaec2fac2104297375644bff0c829d99'),
    'playwright_tests/test_studio_user_detail_layout_586.py': _TestSnapshot(4, '158abd4bc59a667eea7d02d6c89c9aeddd737f00be357f36477f6098f13781bf'),
    'playwright_tests/test_studio_user_slack_id.py': _TestSnapshot(1, '8186514d6acee9f8b3e626ae69f7affe6660c5a7440c290df8f497e71dd40c95'),
    'playwright_tests/test_utm_analytics.py': _TestSnapshot(1, '3c3bc3c649f6670f9bf5fc8801100d8d4ce0b6a99875839bd80be2f1104bece9'),
    'playwright_tests/test_video_player.py': _TestSnapshot(1, 'e29d25a3a92f1b7dc81fd2a4c68d429d86912a7ab5debfacfe9e98cb5c067a95'),
    'studio/tests/test_admin_studio_links.py': _TestSnapshot(36, '95e70f0de9444824f96e6bec1256281c97a751fc1a060393f390aac978cf12f6'),
    'studio/tests/test_impersonate.py': _TestSnapshot(1, 'a7cf38f05467927c1a79a7384fc89adddcbc8ac761aeedacfd7395f2408d6f8e'),
    'studio/tests/test_plans.py': _TestSnapshot(2, '667def5e0c6641c3dd21cd7dc6382b9930db8d6eb6a707c1f18c6cda85d4cbb4'),
    'studio/tests/test_ses_events.py': _TestSnapshot(3, '0d97ca463cd38dc37339abec15297976566001c21765d1ff9f3d42ccb8ee1cfe'),
    'studio/tests/test_sprints.py': _TestSnapshot(1, '1f2fe7db66c5309d215485c490eb1b21bdc00a83cf0a27e200f35abecd355541'),
    'studio/tests/test_user_activity_section_853.py': _TestSnapshot(2, '7c3ef065474e7bd03ff48860a06fe79fbfc7ecd8ed39f3cbc501f209e7b74d06'),
    'studio/tests/test_user_crm_overview.py': _TestSnapshot(2, '667def5e0c6641c3dd21cd7dc6382b9930db8d6eb6a707c1f18c6cda85d4cbb4'),
    'studio/tests/test_user_detail_layout_586.py': _TestSnapshot(4, '2fae30ac097bc44cfce004b65079422613b036358814bd7f606d47d1ff1c8cdf'),
    'studio/tests/test_user_slack_id.py': _TestSnapshot(1, '5a08c0236e1c6f4a771dc4334fc103d695f17a52e0d23ef02e7683bb2b09e9d9'),
    'studio/tests/test_utm_analytics.py': _TestSnapshot(1, '50367ce0e4de79c23336ff4df37ebbbeeb98f8a5fffb0689519d96864fce6ed5'),
    'tests/test_search_indexing_policy_1379.py': _TestSnapshot(3, '28f4eb8fa33c194cfdcba6de0c105163e8a8f98bc83ce4ea0887279ee8097176'),
    'triggers/tests/test_studio_views.py': _TestSnapshot(1, 'e95318920e60d71ab0bc3d9ab1b816ab128cff1b4a413ce78bf6ae79847ec588'),
    'voting/tests/test_admin.py': _TestSnapshot(4, '3bc77c9a2720ba6c253fb4e3178ff1e01bf49f48d5aabaab38d83149edba0bc6'),
}


def _files_below(relative_root):
    absolute_root = ROOT / relative_root
    if absolute_root.is_file():
        if absolute_root.suffix in SCANNED_SUFFIXES:
            yield relative_root, absolute_root
        return
    if not absolute_root.is_dir():
        return
    for path in sorted(absolute_root.rglob('*')):
        if path.is_file() and path.suffix in SCANNED_SUFFIXES:
            yield path.relative_to(ROOT), path


def _scanned_files():
    files = {}
    for root in SCANNED_ROOTS:
        for relative, path in _files_below(root):
            files[relative] = path
    return tuple(sorted(files.items()))


def _is_test_path(relative):
    return relative.parts[0] in {'tests', 'playwright_tests'} or (
        'tests' in relative.parts
    )


def _line_is_guarded(line):
    return (
        any(marker in line for marker in FORBIDDEN_MARKERS)
        or ADMIN_MOUNT_MARKER in line
        or ADMIN_NAMESPACE_PATTERN.search(line) is not None
    )


def _matched_lines(path):
    return sorted(
        line.strip()
        for line in path.read_text(encoding='utf-8').splitlines()
        if _line_is_guarded(line)
    )


def _snapshot_digest(lines):
    payload = json.dumps(
        lines, ensure_ascii=True, separators=(',', ':'),
    ).encode()
    return sha256(payload).hexdigest()


def _allowlist_errors():
    errors = []
    scanned = dict(_scanned_files())

    self_path = scanned.get(SELF_PATH)
    if self_path is None:
        errors.append(f'guarded policy file is outside the scan: {SELF_PATH}')
    else:
        marker_count = self_path.read_text(encoding='utf-8').splitlines().count(
            SELF_GUARD_MARKER,
        )
        if marker_count != 1:
            errors.append(
                f'guarded policy marker count changed: {SELF_PATH}: '
                f'expected 1, found {marker_count}',
            )

    for relative_name, allowed_lines in sorted(ALLOWED_RUNTIME_LINES.items()):
        relative = Path(relative_name)
        path = scanned.get(relative)
        if path is None:
            errors.append(f'allowlist path is missing or outside scan: {relative_name}')
            continue
        if _is_test_path(relative):
            errors.append(f'runtime allowlist entry points to a test: {relative_name}')
            continue
        current = Counter(
            line.strip()
            for line in path.read_text(encoding='utf-8').splitlines()
        )
        for expected_line in sorted(allowed_lines):
            if not _line_is_guarded(expected_line):
                errors.append(
                    f'allowlist line has no guarded marker: '
                    f'{relative_name}: {expected_line}',
                )
            if current[expected_line] != 1:
                errors.append(
                    f'stale or ambiguous allowlist line: {relative_name}: '
                    f'expected 1, found {current[expected_line]}: {expected_line}',
                )

    for relative_name, snapshot in sorted(ALLOWED_TEST_SNAPSHOTS.items()):
        relative = Path(relative_name)
        path = scanned.get(relative)
        if path is None:
            errors.append(
                f'test snapshot path is missing or outside scan: {relative_name}',
            )
            continue
        if not _is_test_path(relative):
            errors.append(f'test snapshot points to runtime source: {relative_name}')
            continue
        if not snapshot.reason:
            errors.append(f'test snapshot has no review reason: {relative_name}')
        matches = _matched_lines(path)
        digest = _snapshot_digest(matches)
        if len(matches) != snapshot.count or digest != snapshot.digest:
            errors.append(
                f'stale test snapshot: {relative_name}: expected '
                f'{snapshot.count}/{snapshot.digest}, found {len(matches)}/{digest}',
            )
    return errors


def _find_violations():
    violations = []
    for relative, path in _scanned_files():
        if relative == SELF_PATH:
            continue
        matches = _matched_lines(path)
        if not matches:
            continue
        relative_name = relative.as_posix()
        if _is_test_path(relative):
            snapshot = ALLOWED_TEST_SNAPSHOTS.get(relative_name)
            if (
                snapshot is not None
                and len(matches) == snapshot.count
                and _snapshot_digest(matches) == snapshot.digest
            ):
                continue
        else:
            allowed = Counter(ALLOWED_RUNTIME_LINES.get(relative_name, set()))
            remaining = []
            for line in matches:
                if allowed[line]:
                    allowed[line] -= 1
                else:
                    remaining.append(line)
            matches = remaining
        for line in matches:
            violations.append(f'{relative_name}: {line}')
    return violations


class StaffAdminLinkGuardTest(SimpleTestCase):
    def test_generated_admin_namespace_builders_are_guarded(self):
        self.assertTrue(_line_is_guarded("reverse('admin:app_model_change')"))
        self.assertTrue(_line_is_guarded("{% url 'admin:app_model_change' pk %}"))

    def test_allowlist_has_no_stale_or_ambiguous_entries(self):
        self.assertEqual(_allowlist_errors(), [])

    def test_repository_staff_surfaces_do_not_link_to_django_admin(self):
        violations = _allowlist_errors() + _find_violations()
        self.assertEqual(
            violations,
            [],
            'Routine staff surfaces must stay in Studio:\n' + '\n'.join(violations),
        )
