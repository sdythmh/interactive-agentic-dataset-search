"""corpora/uk/places.py resolves every key it declares, and opens no file.

Usage
-----
    python tests/test_uk_places.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["CORPUS"] = "uk"
# The evaluated switch combination, set before config is imported.
os.environ.setdefault("PLACE_RESOLVER", "bboxes")
os.environ.setdefault("PLACE_NORMALISE", "off")
os.environ.setdefault("SPATIAL_NAME_FALLBACK", "off")

import config          # noqa: E402
import expansion       # noqa: E402
import retrieval       # noqa: E402

FAILURES = []


def check(label, got, want):
    if got == want:
        print("  [ok] %-56s %s" % (label, got))
    else:
        print("  [FAIL] %-54s %r (expected %r)" % (label, got, want))
        FAILURES.append(label)


def main():
    config.EXPANSION_MODE = "c1"
    print("corpus=%s PLACE_RESOLVER=%s PLACE_NORMALISE=%s EXPANSION_MODE=%s"
          % (config.CORPUS, config.PLACE_RESOLVER, config.PLACE_NORMALISE,
             config.EXPANSION_MODE))
    print("BBOXES: %d keys, GAZETTEER_FILE=%r\n"
          % (len(retrieval.BBOXES), config.GAZETTEER_FILE))

    print("1. every declared key normalises to itself and is found")
    bad_norm, bad_lookup, bad_box = [], [], []
    for key in retrieval.BBOXES:
        norm = retrieval.normalise_place(key)
        if norm != key:
            bad_norm.append((key, norm))
        box = retrieval.BBOXES.get(norm)
        if box is None:
            bad_lookup.append((key, norm))
            continue
        min_lon, min_lat, max_lon, max_lat = box
        if not (-180 <= min_lon < max_lon <= 180
                and -90 <= min_lat < max_lat <= 90):
            bad_box.append((key, box))
    check("keys surviving normalise_place unchanged",
          len(retrieval.BBOXES) - len(bad_norm), len(retrieval.BBOXES))
    for key, norm in bad_norm:
        print("        %r -> %r" % (key, norm))
    check("keys found after normalisation",
          len(retrieval.BBOXES) - len(bad_lookup), len(retrieval.BBOXES))
    check("boxes that are well-formed and non-degenerate",
          len(retrieval.BBOXES) - len(bad_box), len(retrieval.BBOXES))
    for key, box in bad_box:
        print("        %r %s" % (key, box))

    print("\n2. GAZETTEER_FILE is None and nothing opens a gazetteer")
    check("GAZETTEER_FILE", config.GAZETTEER_FILE, None)
    check("place_resolution_available() from BBOXES alone",
          retrieval.place_resolution_available(), True)

    import builtins
    real_open = builtins.open
    opened = []

    def watched(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)

    builtins.open = watched
    try:
        expansion._gazetteer_cache = None
        gaz = expansion._load_gazetteer()
        for key in list(retrieval.BBOXES)[:5]:
            retrieval.normalise_place(key)
            retrieval.BBOXES.get(key)
            expansion.scheme_b_place(key)
    finally:
        builtins.open = real_open
    check("files opened while resolving places", opened, [])
    check("gazetteer tables are empty, not missing",
          sorted(gaz), ["features", "lgas", "states", "suburbs"])
    check("scheme_b_place returns None under PLACE_RESOLVER=bboxes",
          expansion.scheme_b_place("london"), None)

    print("\n3. five realistic router outputs (reported, not asserted)")
    print("   %-28s %-26s %s" % ("input", "normalise_place", "box found?"))
    for text in ("Greater London", "London Borough of Camden",
                 "City of Edinburgh", "East of England",
                 "North East England"):
        norm = retrieval.normalise_place(text)
        box = retrieval.BBOXES.get(norm)
        print("   %-28s %-26s %s"
              % (repr(text), repr(norm),
                 ("yes %s" % box) if box else "NO"))

    print()
    if FAILURES:
        print("%d check(s) failed: %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
