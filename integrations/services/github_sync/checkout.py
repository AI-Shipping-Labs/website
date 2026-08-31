"""Descriptor-anchored reads for untrusted content-repository checkouts.

Git stores symlinks as ordinary tree entries.  A checked-out content commit is
therefore not a trustworthy filesystem boundary: resolving or opening a path
by name can follow a repository-owned link into the worker filesystem.  This
module is the single authority for repository discovery and reads.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

import frontmatter
import yaml

from integrations.services.github_sync.common import (
    CONTENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    GitHubSyncError,
)

_ACTIVE_CHECKOUT: contextvars.ContextVar[ContentCheckout | None] = (
    contextvars.ContextVar("active_content_checkout", default=None)
)
_SNAPSHOT_CHUNK_SIZE = 1024 * 1024
MAX_IMAGE_SNAPSHOT_BYTES = 25 * 1024 * 1024
_PRELOAD_EXTENSIONS = frozenset(CONTENT_EXTENSIONS | IMAGE_EXTENSIONS | {".html"})
_INCLUDE_REFERENCE_RE = re.compile(
    rb"<!--\s*include:([A-Za-z0-9_./-]+)\s*-->"
)
_MARKDOWN_IMAGE_RE = re.compile(
    r'!\[[^\]]*\]\((?P<url>[^)\s]+)(?:\s+["\'][^"\']*["\'])?\)'
)
_HTML_IMAGE_RE = re.compile(
    r'<img\b[^>]*\bsrc=["\'](?P<url>[^"\']+)["\']', re.IGNORECASE
)
_EXTERNAL_IMAGE_PREFIXES = ('http://', 'https://', 'data:', '//')


class ContentCheckoutError(GitHubSyncError):
    """A checkout entry violated the no-follow filesystem boundary."""

    def __init__(self, rel_path: str, kind: str, step: str = "filesystem_boundary"):
        self.rel_path = rel_path or "<checkout-root>"
        self.kind = kind
        self.step = step
        super().__init__(
            f"content checkout rejected {self.rel_path}: {kind} [{step}]"
        )

    def as_error(self) -> dict:
        return {
            "file": "" if self.rel_path == "<checkout-root>" else self.rel_path,
            "error": str(self),
            "step": self.step,
            "kind": self.kind,
            "filesystem_boundary": True,
            "retryable": False,
        }


@dataclass(frozen=True)
class CheckoutEntry:
    rel_path: str
    kind: str
    identity: tuple


@dataclass(frozen=True)
class CheckoutDirEntry:
    """Small ``os.DirEntry``-compatible value used by existing dispatchers."""

    name: str
    path: str
    _is_dir: bool

    def is_dir(self) -> bool:
        return self._is_dir


def _kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular_file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "character_device"
    if stat.S_ISBLK(mode):
        return "block_device"
    return "non_regular"


def _stable_metadata(metadata: os.stat_result) -> tuple:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _identity(metadata: os.stat_result) -> tuple:
    # Include stability metadata as well as dev/inode/type. Filesystems may
    # immediately reuse an inode after unlink, so dev/inode alone cannot prove
    # that the enumerated repository entry is still the opened object.
    return _stable_metadata(metadata)


def _snapshot_metadata(rel_path: str, payload: bytes) -> Mapping:
    """Parse authored metadata with the same YAML/frontmatter semantics as sync."""
    suffix = PurePosixPath(rel_path).suffix.lower()
    if suffix not in {".yaml", ".yml", ".md"}:
        return {}
    try:
        text = payload.decode("utf-8")
        if suffix in {".yaml", ".yml"}:
            parsed = yaml.safe_load(text)
        elif suffix == ".md":
            parsed = frontmatter.loads(text).metadata
        else:
            return {}
    except (UnicodeDecodeError, ValueError, TypeError, yaml.YAMLError):
        # The owning parser retains responsibility for authored-data errors.
        # Preflight only extracts valid auxiliary paths before side effects.
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


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


class ContentCheckout:
    """Pinned checkout root plus a frozen entry manifest and byte snapshots."""

    def __init__(self, root: str):
        self.root = os.path.abspath(os.fspath(root))
        self._root_fd = -1
        self._root_identity: tuple[int, int, int] | None = None
        self._entries: dict[str, CheckoutEntry] | None = None
        self._snapshots: dict[str, bytes] = {}

    def __enter__(self) -> ContentCheckout:
        if not hasattr(os, "O_NOFOLLOW"):
            raise ContentCheckoutError("", "O_NOFOLLOW unavailable")
        try:
            before = os.lstat(self.root)
        except OSError:
            raise ContentCheckoutError("", "missing_or_unreadable_root") from None
        if not stat.S_ISDIR(before.st_mode):
            raise ContentCheckoutError("", _kind(before.st_mode))

        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            descriptor = os.open(self.root, flags)
        except OSError:
            raise ContentCheckoutError("", "root_open_refused") from None
        try:
            opened = os.fstat(descriptor)
            after = os.lstat(self.root)
            expected = (opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode))
            actual = (after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode))
            initial = (before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode))
        except OSError:
            os.close(descriptor)
            raise ContentCheckoutError("", "root_identity_changed") from None
        if not stat.S_ISDIR(opened.st_mode) or initial != expected or actual != expected:
            os.close(descriptor)
            raise ContentCheckoutError("", "root_identity_changed")
        self._root_fd = descriptor
        self._root_identity = expected
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._root_fd >= 0:
            os.close(self._root_fd)
            self._root_fd = -1

    @contextlib.contextmanager
    def activate(self):
        token = _ACTIVE_CHECKOUT.set(self)
        try:
            yield self
        finally:
            _ACTIVE_CHECKOUT.reset(token)

    def _check_root_identity(self) -> None:
        try:
            current = os.lstat(self.root)
        except OSError:
            raise ContentCheckoutError("", "root_identity_changed") from None
        actual = (current.st_dev, current.st_ino, stat.S_IFMT(current.st_mode))
        if actual != self._root_identity:
            raise ContentCheckoutError("", "root_identity_changed")

    def relative(self, path: str) -> str:
        raw = os.fspath(path)
        if "\x00" in raw:
            raise ContentCheckoutError("<invalid-path>", "nul_path")
        if os.path.isabs(raw):
            candidate = os.path.abspath(raw)
            try:
                raw = os.path.relpath(candidate, self.root)
            except ValueError:
                raise ContentCheckoutError("<invalid-path>", "outside_checkout") from None
        raw = raw.replace(os.sep, "/")
        pure = PurePosixPath(raw)
        if pure.is_absolute() or not pure.parts or pure == PurePosixPath("."):
            raise ContentCheckoutError("<invalid-path>", "outside_checkout")
        if any(part in ("", ".", "..") for part in pure.parts):
            raise ContentCheckoutError(_safe_display(raw), "outside_checkout")
        normalized = pure.as_posix()
        if normalized.startswith("../"):
            raise ContentCheckoutError(_safe_display(raw), "outside_checkout")
        return normalized

    def _open_directory(self, parts: tuple[str, ...]) -> int:
        descriptor = os.dup(self._root_fd)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            for index, part in enumerate(parts):
                try:
                    child = os.open(part, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    raise
                except OSError:
                    rel = "/".join(parts[: index + 1])
                    raise ContentCheckoutError(rel, "symlink_or_non_directory") from None
                rel = "/".join(parts[: index + 1])
                expected = self._entries.get(rel) if self._entries is not None else None
                opened = os.fstat(child)
                if (
                    expected is None
                    or expected.kind != "directory"
                    or _identity(opened) != expected.identity
                ):
                    os.close(child)
                    raise ContentCheckoutError(rel, "directory_identity_changed")
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def snapshot(self, path: str, *, max_bytes: int | None = None) -> bytes:
        rel_path = self.relative(path)
        cached = self._snapshots.get(rel_path)
        if cached is not None:
            if max_bytes is not None and len(cached) > max_bytes:
                raise ContentCheckoutError(rel_path, "size_limit_exceeded", "snapshot")
            return cached

        self._check_root_identity()
        self._build_manifest()
        expected = self._entries.get(rel_path)
        if expected is not None and expected.kind != "regular_file":
            raise ContentCheckoutError(rel_path, expected.kind)
        parts = PurePosixPath(rel_path).parts
        try:
            parent_fd = self._open_directory(parts[:-1])
        except FileNotFoundError:
            if self._entries is not None and rel_path in self._entries:
                raise ContentCheckoutError(rel_path, "removed_after_manifest") from None
            raise
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        try:
            try:
                descriptor = os.open(parts[-1], flags, dir_fd=parent_fd)
            except FileNotFoundError:
                if self._entries is not None and rel_path in self._entries:
                    raise ContentCheckoutError(
                        rel_path, "removed_after_manifest"
                    ) from None
                raise
            except OSError:
                raise ContentCheckoutError(rel_path, "symlink_or_unreadable") from None
        finally:
            os.close(parent_fd)

        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ContentCheckoutError(rel_path, _kind(before.st_mode))
            if expected is not None and _identity(before) != expected.identity:
                raise ContentCheckoutError(rel_path, "entry_identity_changed")
            if max_bytes is not None and before.st_size > max_bytes:
                raise ContentCheckoutError(rel_path, "size_limit_exceeded", "snapshot")
            chunks = []
            total = 0
            while True:
                chunk = os.read(descriptor, _SNAPSHOT_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise ContentCheckoutError(
                        rel_path, "size_limit_exceeded", "snapshot"
                    )
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if _stable_metadata(before) != _stable_metadata(after):
                raise ContentCheckoutError(rel_path, "changed_during_read", "snapshot")
            payload = b"".join(chunks)
            if len(payload) != after.st_size:
                raise ContentCheckoutError(rel_path, "changed_during_read", "snapshot")
        finally:
            os.close(descriptor)
        self._check_root_identity()
        self._snapshots[rel_path] = payload
        return payload

    def text(self, path: str, *, encoding: str = "utf-8") -> str:
        return self.snapshot(path).decode(encoding)

    def _build_manifest(self) -> None:
        if self._entries is not None:
            return
        self._check_root_identity()
        entries: dict[str, CheckoutEntry] = {}

        def scan(descriptor: int, prefix: tuple[str, ...]) -> None:
            try:
                children = sorted(os.scandir(descriptor), key=lambda item: item.name)
            except OSError:
                rel = "/".join(prefix)
                raise ContentCheckoutError(rel, "directory_scan_failed") from None
            for child in children:
                name = child.name
                rel_parts = (*prefix, name)
                rel = "/".join(rel_parts)
                try:
                    metadata = child.stat(follow_symlinks=False)
                except OSError:
                    raise ContentCheckoutError(rel, "entry_stat_failed") from None
                kind = _kind(metadata.st_mode)
                identity = _identity(metadata)
                entries[rel] = CheckoutEntry(rel, kind, identity)
                if kind != "directory" or name.startswith(".git"):
                    continue
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
                try:
                    child_fd = os.open(name, flags, dir_fd=descriptor)
                except OSError:
                    raise ContentCheckoutError(rel, "directory_identity_changed") from None
                try:
                    if _identity(os.fstat(child_fd)) != identity:
                        raise ContentCheckoutError(rel, "directory_identity_changed")
                    scan(child_fd, rel_parts)
                finally:
                    os.close(child_fd)

        scan(self._root_fd, ())
        self._check_root_identity()
        self._entries = entries

    def preload(self) -> None:
        """Freeze the manifest and snapshot every sync-eligible leaf."""
        self._build_manifest()
        for rel_path, entry in self._entries.items():
            suffix = PurePosixPath(rel_path).suffix.lower()
            selectable = suffix in _PRELOAD_EXTENSIONS
            dot_tooling = any(part.startswith(".") for part in PurePosixPath(rel_path).parts)
            if entry.kind == "regular_file":
                if selectable:
                    max_bytes = (
                        MAX_IMAGE_SNAPSHOT_BYTES
                        if suffix in IMAGE_EXTENSIONS
                        else None
                    )
                    self.snapshot(rel_path, max_bytes=max_bytes)
                continue
            if entry.kind == "directory":
                continue
            # Ignore a non-selected tooling link such as the content repo's
            # .claude/skills entry. Any selected entry remains fail-closed.
            if selectable or not dot_tooling:
                raise ContentCheckoutError(rel_path, entry.kind)
        self._preload_references()

    def _preload_references(self) -> None:
        """Snapshot auxiliary files named by already captured content."""
        inspected: set[str] = set()
        while True:
            pending = [
                (rel_path, payload)
                for rel_path, payload in self._snapshots.items()
                if rel_path not in inspected
            ]
            if not pending:
                return
            for rel_path, payload in pending:
                inspected.add(rel_path)
                parent = PurePosixPath(rel_path).parent
                references: list[PurePosixPath] = []
                for match in _INCLUDE_REFERENCE_RE.finditer(payload):
                    authored = match.group(1).decode('utf-8')
                    reference = PurePosixPath(authored)
                    if reference.parts and reference.parts[0] == 'widgets':
                        references.append(reference)
                    else:
                        references.append(parent / reference)
                metadata = _snapshot_metadata(rel_path, payload)
                for key in ("copy_file", "recap_file", "recap-file"):
                    authored = metadata.get(key)
                    if isinstance(authored, str) and authored.strip():
                        references.append(parent / PurePosixPath(authored.strip()))
                for reference in references:
                    self._preload_reference(reference.as_posix())

                body = ''
                if PurePosixPath(rel_path).suffix.lower() == '.md':
                    try:
                        post = frontmatter.loads(payload.decode('utf-8'))
                    except (UnicodeDecodeError, ValueError, TypeError, yaml.YAMLError):
                        continue
                    body = post.content
                cover_image = (
                    metadata.get('cover_image', '')
                    or metadata.get('cover_image_url', '')
                )
                base_dir = parent.as_posix() if parent != PurePosixPath('.') else ''
                for authored in extract_authored_image_references(
                    body, str(cover_image or '').strip(),
                ):
                    image_path = self.authored_image_relative(
                        authored, base_dir=base_dir,
                    )
                    if image_path is not None:
                        self._preload_reference(
                            image_path, max_bytes=MAX_IMAGE_SNAPSHOT_BYTES,
                        )

    def _preload_reference(
        self, rel_path: str, *, max_bytes: int | None = None,
    ) -> None:
        normalized = self.authored_relative(rel_path)
        entry = self._entries.get(normalized)
        if entry is not None:
            if entry.kind == 'regular_file':
                self.snapshot(normalized, max_bytes=max_bytes)
                return
            raise ContentCheckoutError(normalized, entry.kind)
        parts = PurePosixPath(normalized).parts
        prefix = []
        for part in parts[:-1]:
            prefix.append(part)
            ancestor = self._entries.get('/'.join(prefix))
            if ancestor is not None and ancestor.kind != 'directory':
                raise ContentCheckoutError(normalized, ancestor.kind)

    def authored_relative(self, path: str) -> str:
        """Validate an author-controlled path as strictly repository-relative."""
        raw = os.fspath(path)
        if "\x00" in raw:
            raise ContentCheckoutError("<invalid-path>", "nul_path")
        if os.path.isabs(raw) or PurePosixPath(raw).is_absolute():
            raise ContentCheckoutError("<invalid-path>", "absolute_path")
        return self.relative(raw)

    def authored_image_relative(
        self, reference: str, *, base_dir: str = '',
    ) -> str | None:
        """Resolve an authored image reference under the pinned checkout.

        Historical root-relative ``/images/...`` URLs name the repository's
        ``public/images/...`` tree. Other absolute paths are filesystem paths
        and are rejected. External URLs never name a checkout entry.
        """
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

    def subprocess_cwd(self) -> tuple[str, tuple[int, ...]]:
        """Return a process cwd backed by the pinned root descriptor."""
        if self._root_fd < 0:
            raise ContentCheckoutError("", "checkout_not_open")
        return f"/proc/self/fd/{self._root_fd}", (self._root_fd,)

    def walk(self, path: str | None = None):
        self._build_manifest()
        start = (
            ""
            if path is None or os.path.abspath(os.fspath(path)) == self.root
            else self.relative(path)
        )
        directories: dict[str, list[str]] = {}
        files: dict[str, list[str]] = {}
        directories.setdefault(start, [])
        files.setdefault(start, [])
        for rel_path, entry in self._entries.items():
            if start and rel_path != start and not rel_path.startswith(f"{start}/"):
                continue
            parent, name = rel_path.rsplit("/", 1) if "/" in rel_path else ("", rel_path)
            if start and parent == "":
                continue
            if entry.kind == "directory":
                directories.setdefault(parent, []).append(name)
                directories.setdefault(rel_path, [])
                files.setdefault(rel_path, [])
            elif entry.kind != "symlink" or PurePosixPath(rel_path).suffix:
                files.setdefault(parent, []).append(name)
        roots = sorted(set(directories) | set(files), key=lambda value: (value.count("/"), value))
        for rel_root in roots:
            absolute = self.root if not rel_root else os.path.join(self.root, *rel_root.split("/"))
            yield absolute, sorted(directories.get(rel_root, [])), sorted(files.get(rel_root, []))

    def listdir(self, path: str) -> list[str]:
        rel = "" if os.path.abspath(os.fspath(path)) == self.root else self.relative(path)
        self._build_manifest()
        prefix = f"{rel}/" if rel else ""
        names = []
        for candidate in self._entries:
            if candidate.startswith(prefix):
                remainder = candidate[len(prefix):]
                if "/" not in remainder:
                    names.append(remainder)
        return sorted(names)

    def kind(self, path: str) -> str | None:
        rel = self.relative(path)
        self._build_manifest()
        entry = self._entries.get(rel)
        return None if entry is None else entry.kind


def _safe_display(path: str) -> str:
    parts = [part for part in path.replace("\\", "/").split("/") if part not in ("", ".", "..")]
    return "/".join(parts) or "<invalid-path>"


def active_checkout() -> ContentCheckout | None:
    return _ACTIVE_CHECKOUT.get()


@contextlib.contextmanager
def checkout_session(root: str, *, preload: bool = False):
    with ContentCheckout(root) as checkout, checkout.activate():
        if preload:
            checkout.preload()
        yield checkout


@contextlib.contextmanager
def checkout_scope(root: str, *, preload: bool = False):
    """Reuse the active pinned root or establish one session for an operation."""
    current = active_checkout()
    requested = os.path.abspath(os.fspath(root))
    if current is not None:
        if current.root != requested:
            raise ContentCheckoutError("", "checkout_root_mismatch")
        if preload:
            current.preload()
        yield current
        return
    with checkout_session(requested, preload=preload) as checkout:
        yield checkout


@contextlib.contextmanager
def _checkout_for_path(path: str, root: str | None = None):
    current = active_checkout()
    if current is not None:
        yield current
        return
    chosen_root = os.path.abspath(root or os.path.dirname(os.path.abspath(path)))
    with checkout_session(chosen_root) as checkout:
        yield checkout


def checkout_read_bytes(path: str, *, root: str | None = None, max_bytes: int | None = None) -> bytes:
    with _checkout_for_path(path, root) as checkout:
        return checkout.snapshot(path, max_bytes=max_bytes)


def checkout_read_text(path: str, *, root: str | None = None, encoding: str = "utf-8") -> str:
    with _checkout_for_path(path, root) as checkout:
        return checkout.snapshot(path).decode(encoding)


def checkout_walk(root: str):
    current = active_checkout()
    if current is not None:
        return list(current.walk(root))
    with checkout_session(root) as checkout:
        return list(checkout.walk())


def checkout_listdir(path: str) -> list[str]:
    current = active_checkout()
    if current is not None:
        return current.listdir(path)
    with checkout_session(path) as checkout:
        checkout._build_manifest()
        names = []
        for rel_path in checkout._entries:
            if "/" not in rel_path:
                names.append(rel_path)
        return sorted(names)


def checkout_scandir(path: str) -> list[CheckoutDirEntry]:
    names = checkout_listdir(path)
    return [
        CheckoutDirEntry(
            name=name,
            path=os.path.join(path, name),
            _is_dir=checkout_is_dir(os.path.join(path, name)),
        )
        for name in names
    ]


def checkout_kind(path: str) -> str | None:
    current = active_checkout()
    if current is not None:
        return current.kind(path)
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.exists(parent):
        return None
    with checkout_session(parent) as checkout:
        return checkout.kind(os.path.basename(path))


def checkout_is_file(path: str) -> bool:
    kind = checkout_kind(path)
    if kind == "regular_file":
        return True
    if kind in (None, "directory"):
        return False
    raise ContentCheckoutError(_path_for_error(path), kind)


def checkout_is_dir(path: str) -> bool:
    current = active_checkout()
    if current is not None:
        if os.path.abspath(path) == current.root:
            return True
        kind = current.kind(path)
        if kind == "directory":
            return True
        if kind is None:
            return False
        if kind == "symlink":
            return False
        return False
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(metadata.st_mode)


def checkout_exists(path: str) -> bool:
    current = active_checkout()
    if current is not None:
        if os.path.abspath(path) == current.root:
            return True
        return current.kind(path) is not None
    try:
        os.lstat(path)
    except OSError:
        return False
    return True


def _path_for_error(path: str) -> str:
    current = active_checkout()
    if current is not None:
        try:
            return current.relative(path)
        except ContentCheckoutError:
            return "<invalid-path>"
    return os.path.basename(path) or "<invalid-path>"


def raise_if_checkout_error(error: BaseException) -> None:
    if isinstance(error, ContentCheckoutError):
        raise error
