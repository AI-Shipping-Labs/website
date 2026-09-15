"""Site view over the package immutable checkout.

A2.3 moves content synchronization onto the ``community_base.content_sync``
engine. Parsers never touch the repository working tree: every read goes
through the package ``ImmutableCheckout`` manifest-validated interface.

The moved dispatchers speak the historical helper surface
(``checkout_read_text``, ``checkout_is_file``, ``checkout_walk`` ...). This
module reimplements that surface on top of the package checkout so the parser
bodies stay byte-for-byte compatible while their file access becomes
manifest-checked and symlink-free.

Paths inside parser code are repository-relative POSIX paths, optionally
joined onto :data:`SYNTHETIC_ROOT` by legacy ``os.path.join(repo_dir, rel)``
call sites. Both forms resolve to the same checkout entry.
"""

import os
import re
import sys
import threading
from pathlib import PurePosixPath

from community_base.content_sync.checkout import (
    CheckoutError,
    ImmutableCheckout,
)

from content.sync_parsers.common import GitHubSyncError

SYNTHETIC_ROOT = '/-asl-content-checkout'

_IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico'}
_EXTERNAL_IMAGE_PREFIXES = ('http://', 'https://', 's3://', 'data:', '//')

MAX_IMAGE_SNAPSHOT_BYTES = 25 * 1024 * 1024
_MARKDOWN_IMAGE_RE = re.compile(
    r'!\[[^\]]*\]\((?P<url>[^)\s]+)(?:\s+["\'][^"\']*["\'])?\)'
)
_HTML_IMAGE_RE = re.compile(
    r'<img\b[^>]*\bsrc=["\'](?P<url>[^"\']+)["\']', re.IGNORECASE
)


def extract_authored_image_references(body: str, cover_image: str = '') -> list[str]:
    """Return authored local or external image references in stable order."""
    references = []
    if cover_image:
        references.append(str(cover_image).strip())
    references.extend(
        match.group('url') for match in _MARKDOWN_IMAGE_RE.finditer(body or '')
    )
    references.extend(
        match.group('url') for match in _HTML_IMAGE_RE.finditer(body or '')
    )
    return list(dict.fromkeys(reference for reference in references if reference))


class ContentCheckoutError(GitHubSyncError):
    """A checkout entry violated the no-follow filesystem boundary."""

    def __init__(self, rel_path: str, kind: str, step: str = 'filesystem_boundary'):
        self.rel_path = rel_path
        self.kind = kind
        self.step = step
        super().__init__(f'{rel_path}: {kind}')

    def as_error(self) -> dict:
        return {
            'file': self.rel_path,
            'error': f'{self.rel_path}: {self.kind}',
            'step': self.step,
            'kind': self.kind,
            'filesystem_boundary': True,
            'retryable': False,
        }


def _safe_display(path: str) -> str:
    parts = [part for part in path.replace('\\', '/').split('/') if part not in ('', '.', '..')]
    return '/'.join(parts) or '<invalid-path>'


class CheckoutView:
    """Read-only, manifest-checked view of one package checkout snapshot."""

    def __init__(self, checkout: ImmutableCheckout, root: str = SYNTHETIC_ROOT):
        self.checkout = checkout
        self.root = root
        self._files: frozenset[str] | None = None
        self._dirs: frozenset[str] | None = None

    def _manifest(self) -> frozenset[str]:
        if self._files is None:
            self._files = frozenset(str(path) for path in self.checkout.files())
        return self._files

    def _directories(self) -> frozenset[str]:
        if self._dirs is None:
            dirs = set()
            for rel in self._manifest():
                parts = rel.split('/')
                for index in range(1, len(parts)):
                    dirs.add('/'.join(parts[:index]))
            self._dirs = frozenset(dirs)
        return self._dirs

    # -- path normalization -------------------------------------------------

    def relative(self, path: str) -> str:
        raw = os.fspath(path)
        if '\x00' in raw:
            raise ContentCheckoutError('<invalid-path>', 'nul_path')
        if os.path.isabs(raw):
            candidate = os.path.abspath(raw)
            prefix = self.root.rstrip(os.sep) + os.sep
            if candidate != self.root and not candidate.startswith(prefix):
                raise ContentCheckoutError('<invalid-path>', 'outside_checkout')
            raw = os.path.relpath(candidate, self.root)
            if raw == '.':
                raise ContentCheckoutError('<invalid-path>', 'outside_checkout')
        raw = raw.replace(os.sep, '/')
        pure = PurePosixPath(raw)
        if pure.is_absolute() or not pure.parts or pure == PurePosixPath('.'):
            raise ContentCheckoutError('<invalid-path>', 'outside_checkout')
        if any(part in ('', '.', '..') for part in pure.parts):
            raise ContentCheckoutError(_safe_display(raw), 'outside_checkout')
        normalized = pure.as_posix()
        if normalized.startswith('../'):
            raise ContentCheckoutError(_safe_display(raw), 'outside_checkout')
        return normalized

    def authored_relative(self, path: str) -> str:
        """Validate an author-controlled path as strictly repository-relative."""
        raw = os.fspath(path)
        if '\x00' in raw:
            raise ContentCheckoutError('<invalid-path>', 'nul_path')
        if os.path.isabs(raw) or PurePosixPath(raw).is_absolute():
            raise ContentCheckoutError('<invalid-path>', 'absolute_path')
        return self.relative(raw)

    def authored_image_relative(self, reference: str, *, base_dir: str = '') -> str | None:
        """Resolve an authored image reference under the pinned checkout."""
        authored = str(reference).strip()
        if authored.startswith(_EXTERNAL_IMAGE_PREFIXES):
            return None
        if authored.startswith('/images/'):
            candidate = PurePosixPath('public') / authored.lstrip('/')
        else:
            if os.path.isabs(authored) or PurePosixPath(authored).is_absolute():
                raise ContentCheckoutError('<invalid-path>', 'absolute_path')
            candidate = PurePosixPath(base_dir) / PurePosixPath(authored)
        return self.authored_relative(candidate.as_posix())

    # -- reads ---------------------------------------------------------------

    def snapshot(self, path: str, *, max_bytes: int | None = None) -> bytes:
        rel_path = self.relative(path)
        kind = self.kind(rel_path)
        if kind is None:
            # Legacy contract: reading an absent file raises an OSError
            # (FileNotFoundError) so the parser families' narrowed
            # ``except (ValueError, OSError)`` guards keep working on the
            # immutable checkout.
            raise FileNotFoundError(rel_path)
        if kind == 'directory':
            raise IsADirectoryError(rel_path)
        try:
            payload = self.checkout.read_bytes(rel_path)
        except CheckoutError as exc:
            raise ContentCheckoutError(rel_path, str(exc)) from exc
        if max_bytes is not None and len(payload) > max_bytes:
            raise ContentCheckoutError(rel_path, 'size_limit_exceeded', 'snapshot')
        return payload

    def text(self, path: str, *, encoding: str = 'utf-8') -> str:
        return self.snapshot(path).decode(encoding)

    def files(self) -> tuple[str, ...]:
        return tuple(sorted(self._manifest()))

    def image_paths(self) -> list[str]:
        return [
            rel for rel in sorted(self._manifest())
            if PurePosixPath(rel).suffix.lower() in _IMAGE_SUFFIXES
        ]

    # -- structure -----------------------------------------------------------

    def kind(self, path: str) -> str | None:
        if os.path.abspath(os.fspath(path)) == os.path.abspath(self.root):
            return 'directory'
        rel = self.relative(path)
        if rel in self._manifest():
            return 'regular_file'
        if rel in self._directories():
            return 'directory'
        return None

    def walk(self, path: str | None = None):
        if path is None or os.path.abspath(os.fspath(path)) == os.path.abspath(self.root):
            start = ''
        else:
            start = self.relative(path)
        if start and start not in self._directories():
            return
        directories: dict[str, list[str]] = {start: []}
        files: dict[str, list[str]] = {start: []}
        prefix = f'{start}/' if start else ''
        for rel in sorted(self._manifest()):
            if prefix and not rel.startswith(prefix):
                continue
            remainder = rel[len(prefix):]
            parent, _, name = remainder.rpartition('/')
            if parent:
                directories.setdefault(f'{prefix}{parent}' if parent else start, [])
                # ensure intermediate directory rows exist
                parts = parent.split('/')
                for index in range(1, len(parts)):
                    directories.setdefault(f'{prefix}{"/".join(parts[:index])}', [])
                directories.setdefault(f'{prefix}{parent}', [])
                files.setdefault(f'{prefix}{parent}', [])
            files.setdefault(f'{prefix}{parent}' if parent else start, []).append(name)
        roots = sorted(set(directories) | set(files), key=lambda value: (value.count('/'), value))
        for rel_root in roots:
            absolute = self.root if not rel_root else os.path.join(self.root, *rel_root.split('/'))
            yield absolute, sorted(directories.get(rel_root, [])), sorted(files.get(rel_root, []))

    def listdir(self, path: str) -> list[str]:
        if path is None or os.path.abspath(os.fspath(path)) == os.path.abspath(self.root):
            rel = ''
        else:
            rel = self.relative(path)
        prefix = f'{rel}/' if rel else ''
        names = set()
        # Directories and files both enumerate, mirroring the legacy
        # checkout's entry manifest (module/course discovery relies on it).
        for candidate in list(self._manifest()) + list(self._directories()):
            if candidate.startswith(prefix):
                remainder = candidate[len(prefix):]
                if '/' not in remainder and remainder:
                    names.add(remainder)
        return sorted(names)


_ACTIVE = threading.local()


def active_checkout() -> CheckoutView | None:
    return getattr(_ACTIVE, 'view', None)


class _ViewScope:
    def __init__(self, view):
        self.view = view

    def __enter__(self):
        self.token = getattr(_ACTIVE, 'view', None)
        _ACTIVE.view = self.view
        return self.view

    def __exit__(self, exc_type, exc, traceback):
        _ACTIVE.view = self.token
        return False


def activate_view(view: CheckoutView):
    """Bind ``view`` as the active checkout for the current thread."""
    return _ViewScope(view)


def view_for(checkout: ImmutableCheckout) -> CheckoutView:
    return CheckoutView(checkout)


def checkout_scope(root: str, *, preload: bool = False):
    """Reuse the active view when ``root`` matches; open one over a real dir.

    Inside a sync run the orchestration-owned view is already active and its
    synthetic root must match exactly. Outside a sync run (backfill commands,
    studio utilities) callers historically passed a plain on-disk folder;
    those keep working through a local ``ImmutableCheckout`` snapshot so the
    manifest-checked, no-symlink boundary still applies.
    """
    current = active_checkout()
    requested = os.path.abspath(os.fspath(root))
    if current is not None:
        if os.path.abspath(current.root) != requested:
            raise ContentCheckoutError('', 'checkout_root_mismatch')
        return activate_view(current)
    if os.path.isdir(requested):
        return _local_checkout_scope(requested, preload=preload)
    raise ContentCheckoutError(_safe_display(root), 'missing_checkout_session')


# Out-of-sync utility scopes never had a file-count limit; the snapshot cap
# only guards against pathological directories.
UTILITY_CHECKOUT_MAX_FILES = 50_000


class _LocalCheckoutScope:
    """Snapshot ``root`` through the package checkout for the scope body."""

    def __init__(self, root: str):
        self.root = root
        self._checkout = None
        self._view_scope = None

    def __enter__(self) -> CheckoutView:
        from community_base.content_sync.checkout import git_commit_sha

        self._checkout = ImmutableCheckout(
            self.root,
            commit_sha=git_commit_sha(self.root),
            max_files=UTILITY_CHECKOUT_MAX_FILES,
        )
        self._checkout.__enter__()
        try:
            self._view_scope = activate_view(
                CheckoutView(self._checkout, root=self.root),
            )
            return self._view_scope.__enter__()
        except BaseException:
            self._checkout.__exit__(*sys.exc_info())
            raise

    def __exit__(self, exc_type, exc, traceback):
        try:
            return self._view_scope.__exit__(exc_type, exc, traceback)
        finally:
            self._checkout.__exit__(exc_type, exc, traceback)


def _local_checkout_scope(root: str, *, preload: bool = False):
    del preload  # A local snapshot materialises the whole manifest anyway.
    return _LocalCheckoutScope(root)


def _view() -> CheckoutView:
    view = active_checkout()
    if view is None:
        raise ContentCheckoutError('', 'missing_checkout_session')
    return view


def checkout_read_bytes(path: str, *, root: str | None = None, max_bytes: int | None = None) -> bytes:
    return _view().snapshot(path, max_bytes=max_bytes)


def checkout_read_text(path: str, *, root: str | None = None, encoding: str = 'utf-8') -> str:
    return _view().snapshot(path).decode(encoding)


def checkout_walk(root: str):
    return list(_view().walk(root))


def checkout_listdir(path: str) -> list[str]:
    return _view().listdir(path)


def checkout_kind(path: str) -> str | None:
    return _view().kind(path)


def checkout_scandir(path: str):
    from content.sync_parsers.checkout_entries import CheckoutDirEntry

    names = checkout_listdir(path)
    return [
        CheckoutDirEntry(
            name=name,
            path=os.path.join(path, name),
            _is_dir=checkout_is_dir(os.path.join(path, name)),
        )
        for name in names
    ]


def checkout_is_file(path: str) -> bool:
    kind = checkout_kind(path)
    if kind == 'regular_file':
        return True
    if kind in (None, 'directory'):
        return False
    raise ContentCheckoutError(_path_for_error(path), kind)


def checkout_is_dir(path: str) -> bool:
    view = _view()
    try:
        rel = view.relative(path)
    except ContentCheckoutError:
        return False
    if rel in view._directories():
        return True
    return False


def checkout_exists(path: str) -> bool:
    view = _view()
    try:
        rel = view.relative(path)
    except ContentCheckoutError:
        return False
    return rel in view._manifest() or rel in view._directories()


def _path_for_error(path: str) -> str:
    view = active_checkout()
    if view is not None:
        try:
            return view.relative(path)
        except ContentCheckoutError:
            return '<invalid-path>'
    return os.path.basename(path) or '<invalid-path>'


def raise_if_checkout_error(error: BaseException) -> None:
    if isinstance(error, ContentCheckoutError):
        raise error
