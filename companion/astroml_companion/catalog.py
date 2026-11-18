"""Target names (M42, NGC 7000, "Crescent Nebula") to RA/Dec, from a JSON file exported by
`astroml data companion-catalog` out of AstroML's deep-sky data."""
from __future__ import annotations

import json
import pathlib
import re
from importlib import resources

PACKAGED = "catalog.json"


def normalise(name: str) -> str:
    """'ngc 7000', 'NGC7000' and 'Ngc-7000' all become 'NGC7000'; names lose spaces and case."""
    return re.sub(r"[\s\-_]+", "", name).upper()


class Catalog:
    def __init__(self, entries: dict[str, list]):
        self.entries = entries  # designation -> [ra, dec, common name]
        self.index: dict[str, str] = {}
        for designation, (_ra, _dec, common) in entries.items():
            self.index[normalise(designation)] = designation
            if common:
                self.index.setdefault(normalise(common), designation)

    @classmethod
    def load(cls, path: pathlib.Path | None = None) -> "Catalog":
        if path is None:
            packaged = resources.files(__package__).joinpath(PACKAGED)
            if not packaged.is_file():
                return cls({})
            return cls(json.loads(packaged.read_text()))
        return cls(json.loads(path.read_text()))

    def __len__(self) -> int:
        return len(self.entries)

    def lookup(self, name: str) -> tuple[str, float, float, str] | None:
        """(designation, ra, dec, common name), or None."""
        designation = self.index.get(normalise(name))
        if designation is None:
            return None
        ra, dec, common = self.entries[designation]
        return designation, ra, dec, common
