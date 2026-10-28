"""Directory-entry value types shared by the checkout view."""



class CheckoutDirEntry:
    def __init__(self, name: str, path: str, _is_dir: bool):
        self.name = name
        self.path = path
        self._is_dir = _is_dir

    def is_dir(self) -> bool:
        return self._is_dir

    def __repr__(self):  # pragma: no cover - debugging aid
        return f'CheckoutDirEntry({self.name!r})'


