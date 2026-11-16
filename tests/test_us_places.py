"""corpora/us/places.py is the generated table, resolves every key it declares, and opens no file.

Usage
-----
    python tests/test_us_places.py
"""
import builtins
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["CORPUS"] = "us"
os.environ.setdefault("PLACE_RESOLVER", "bboxes")
os.environ.setdefault("PLACE_NORMALISE", "off")
os.environ.setdefault("SPATIAL_NAME_FALLBACK", "off")

import config          # noqa: E402
import retrieval       # noqa: E402

JSON = os.path.join(ROOT, "data/us/_dryrun/bboxes.json")
FAILURES = []


def check(label, got, want):
    if got == want:
        print("  [ok] %-62s %s" % (label, got if not isinstance(got, dict) else "(%d)" % len(got)))
    else:
        print("  [FAIL] %-60s %r (expected %r)" % (label, got, want))
        FAILURES.append(label)


def main():
    places = config.PLACES
    boxes = places.BBOXES
    print("corpus=%s, BBOXES: %d keys, GAZETTEER_FILE=%r\n" % (config.CORPUS, len(boxes), places.GAZETTEER_FILE))
    check("51 keys", len(boxes), 51)
    check("no box is wider than 180 degrees", [k for k, b in boxes.items() if b[2] - b[0] > 180], [])
    check("every box is [min_lon, min_lat, max_lon, max_lat] with min < max",
          [k for k, b in boxes.items() if not (b[0] < b[2] and b[1] < b[3])], [])
    check("every key is lower case", [k for k in boxes if k != k.lower()], [])
    check("no key is a two-letter abbreviation", [k for k in boxes if len(k) == 2], [])
    check("the District of Columbia is present", "district of columbia" in boxes, True)
    check("Alaska's box stays on the western side", boxes["alaska"][2] < 0, True)

    if os.path.exists(JSON):
        d = json.load(open(JSON, encoding="utf-8"))
        check("BBOXES == bboxes.json, key for key", boxes, d["bboxes"])
        check("... same key set", sorted(boxes) == sorted(d["bboxes"]), True)
        check("R2 fired for Alaska and only Alaska", sorted(d["r2"]), ["alaska"])
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "places.py")
            subprocess.run([sys.executable, os.path.join(ROOT, "tools", "emit_us_places.py"),
                            "--out", out], check=True, capture_output=True)
            same = open(out, "rb").read() == open(os.path.join(ROOT, "corpora/us/places.py"), "rb").read()
        check("places.py is byte-for-byte what emit_us_places.py writes", same, True)
    else:
        print("  SKIP data/us/_dryrun/bboxes.json is absent (git-ignored); parts 1-2 against the JSON NOT checked")

    check("every key survives normalise_place() unchanged",
          [k for k in boxes if retrieval.normalise_place(k) != k], [])
    for text, want in (("State of Washington", "washington"), ("Washington State", "washington"),
                       ("Commonwealth of Virginia", "virginia"), ("Portland, Oregon", "portland"),
                       ("Washington, D.C.", "washington"), ("the District of Columbia", "district of columbia"),
                       ("Oregon, USA", "oregon"), ("New York State", "new york")):
        check("normalise_place(%r)" % text, retrieval.normalise_place(text), want)

    check("GAZETTEER_FILE is None", places.GAZETTEER_FILE, None)
    check("place resolution is available from BBOXES alone", retrieval.place_resolution_available(), True)
    real_open = builtins.open
    opened = []

    def guarded(file, *a, **k):
        opened.append(str(file))
        raise AssertionError("opened %s" % file)
    builtins.open = guarded
    try:
        for key in boxes:
            retrieval.BBOXES.get(retrieval.normalise_place(key))
        retrieval.place_resolution_available()
    finally:
        builtins.open = real_open
    check("the lookups open no file", opened, [])

    print()
    if FAILURES:
        print("%d check(s) failed: %s" % (len(FAILURES), "; ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
