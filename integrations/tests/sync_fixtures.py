"""Shared fixtures for content sync integration tests."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import yaml

from integrations.models import ContentSource
from integrations.services.github import sync_content_source


class SyncTestRepo:
    """Temporary content repo with structured writers and TestCase cleanup."""

    def __init__(self, testcase, *, prefix='content-sync-'):
        self._tempdir = tempfile.TemporaryDirectory(prefix=prefix)
        self.path = Path(self._tempdir.name)
        testcase.addCleanup(self.cleanup)

    def cleanup(self):
        self._tempdir.cleanup()

    def __fspath__(self):
        return str(self.path)

    def write_text(self, rel_path, text):
        full_path = self.path / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(text, encoding='utf-8')
        return full_path

    def write_bytes(self, rel_path, data):
        full_path = self.path / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)
        return full_path

    def write_yaml(self, rel_path, data, *, ensure_content_id=False):
        payload = dict(data)
        if ensure_content_id and 'content_id' not in payload:
            payload['content_id'] = str(uuid.uuid4())
        text = yaml.safe_dump(
            payload,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
        return self.write_text(rel_path, text)

    def write_markdown(
        self,
        rel_path,
        frontmatter=None,
        body='',
        *,
        ensure_content_id=True,
    ):
        metadata = dict(frontmatter or {})
        if ensure_content_id and 'content_id' not in metadata:
            metadata['content_id'] = str(uuid.uuid4())
        frontmatter_text = yaml.safe_dump(
            metadata,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
        return self.write_text(
            rel_path,
            f'---\n{frontmatter_text}---\n{body}',
        )

    def remove(self, rel_path):
        (self.path / rel_path).unlink()


def make_content_source(
    repo_name,
    *,
    content_path=None,
    branch=None,
    is_private=False,
    **kwargs,
):
    source_data = {'repo_name': repo_name, 'is_private': is_private, **kwargs}
    source_fields = {field.name for field in ContentSource._meta.fields}
    if content_path is not None and 'content_path' in source_fields:
        source_data['content_path'] = content_path
    if branch is not None and 'branch' in source_fields:
        source_data['branch'] = branch
    return ContentSource.objects.create(**source_data)


def make_sync_repo(testcase, *, repo_name, prefix='content-sync-', **source_kwargs):
    source = make_content_source(repo_name, **source_kwargs)
    repo = SyncTestRepo(testcase, prefix=prefix)
    return source, repo


def sync_repo(source, repo):
    repo_dir = repo.path if isinstance(repo, SyncTestRepo) else repo
    return sync_content_source(source, repo_dir=str(repo_dir))


def write_markdown_file(path, frontmatter, body='', *, ensure_content_id=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    repo = _StandaloneWriter(path.parent)
    return repo.write_markdown(
        path.name,
        frontmatter,
        body,
        ensure_content_id=ensure_content_id,
    )


def write_yaml_file(path, data, *, ensure_content_id=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    repo = _StandaloneWriter(path.parent)
    return repo.write_yaml(path.name, data, ensure_content_id=ensure_content_id)


class _StandaloneWriter:
    """Writer adapter for tests that still pass absolute file paths."""

    def __init__(self, root):
        self.path = Path(root)

    def write_text(self, rel_path, text):
        full_path = self.path / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(text, encoding='utf-8')
        return full_path

    write_yaml = SyncTestRepo.write_yaml
    write_markdown = SyncTestRepo.write_markdown
