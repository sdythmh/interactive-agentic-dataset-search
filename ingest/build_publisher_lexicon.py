"""Build the publisher lexicon of one corpus, used by publisher linking
(ENTITY_RESOLVE=link_path).

    python ingest/build_publisher_lexicon.py --corpus au     # uk, us
"""
import argparse
import gzip
import hashlib
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import corpora  # noqa: E402


def dcat_publishers(raw_path, prefix, ids):
    out = {}
    with gzip.open(raw_path, "rt", encoding="utf-8") as f:
        for line in f:
            if '"slug"' not in line:
                continue
            rec = json.loads(line)
            slug = (rec.get("slug") or "").strip()
            if not slug:
                continue
            i = str(uuid.uuid5(uuid.NAMESPACE_URL, prefix + slug))
            if i not in ids:
                continue
            pb = (rec.get("dcat") or {}).get("publisher")
            name = pb.get("name") if isinstance(pb, dict) else (pb if isinstance(pb, str) else None)
            if isinstance(name, str) and name.strip():
                out[i] = name.strip()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True, choices=("au", "uk", "us"))
    a = ap.parse_args()
    spec = corpora.load(a.corpus).corpus
    source = getattr(spec, "PUBLISHER_SOURCE", "organization")
    elements = [json.loads(l) for l in open(os.path.join(ROOT, spec.ELEMENTS_FILE),
                                            encoding="utf-8") if l.strip()]
    names = {}

    def add(name, i):
        name = (name or "").strip()
        if name:
            names.setdefault(name, set()).add(i)
    for e in elements:
        add(e.get("organization"), e["id"])
    if source == "dcat.publisher.name":
        raw = os.path.join(ROOT, spec.DATA_DIR, "records_raw.jsonl.gz")
        pubs = dcat_publishers(raw, spec.DATASET_URL_PREFIX, {e["id"] for e in elements})
        for i, name in pubs.items():
            add(name, i)
        print("dcat publisher found for %d of %d records" % (len(pubs), len(elements)))
    elif source != "organization":
        raise SystemExit("unknown PUBLISHER_SOURCE %r" % source)
    blob = {"corpus": a.corpus, "source": source, "records": len(elements),
            "names": {n: sorted(ids) for n, ids in sorted(names.items())}}
    text = json.dumps(blob, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    out = os.path.join(ROOT, spec.DATA_DIR, "publisher_lexicon.json")
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print("%s: %d names over %d records -> %s\nsha256 %s" % (
        a.corpus, len(names), len(elements), os.path.relpath(out, ROOT),
        hashlib.sha256(text.encode("utf-8")).hexdigest()))


if __name__ == "__main__":
    main()
