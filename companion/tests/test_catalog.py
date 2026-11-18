from __future__ import annotations

from astroml_companion.catalog import Catalog

ENTRIES = {"M42": [83.8187, -5.3897, "Great Orion Nebula"], "NGC 6888": [303.03, 38.35,
           "Crescent Nebula"], "Sh2-155": [344.475, 62.518, "Cave Nebula"]}


def test_designations_and_names():
    catalog = Catalog(ENTRIES)
    assert catalog.lookup("m 42")[0] == "M42"
    assert catalog.lookup("ngc6888")[1:3] == (303.03, 38.35)
    assert catalog.lookup("crescent nebula")[0] == "NGC 6888"
    assert catalog.lookup("SH2 155")[0] == "Sh2-155"
    assert catalog.lookup("nothing") is None


def test_packaged_catalogue_when_generated():
    catalog = Catalog.load()
    if len(catalog):
        assert catalog.lookup("M31")[0] == "M31"
