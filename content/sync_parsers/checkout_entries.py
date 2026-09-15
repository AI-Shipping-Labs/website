"""Directory-entry value types shared by the checkout view."""

import os


class CheckoutEntry:
    def __init__(self, rel_path: str, kind: str, identity: tuple):
        self.rel_path = rel_path
        self.kind = kind
        self.identity = identity

    def __repr__(self):  # pragma: no cover - debugging aid
        return f'CheckoutEntry({self.rel_path!r}, {self.kind!r})'


class CheckoutDirEntry:
    def __init__(self, name: str, path: str, _is_dir: bool):
        self.name = name
        self.path = path
        self._is_dir = _is_dir

    def is_dir(self) -> bool:
        return self._is_dir

    def __repr__(self):  # pragma: no cover - debugging aid
        return f'CheckoutDirEntry({self.name!r})'


def entry_identity(rel_path: str) -> tuple:
    return (rel_path, os.path.normcase(rel_path))
