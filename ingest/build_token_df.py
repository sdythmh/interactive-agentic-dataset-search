"""Token document frequencies over the corpus full text.

Usage
-----
    python ingest/build_token_df.py
    python ingest/build_token_df.py --elements data/elements.jsonl
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# Same token pattern rewrite.py uses, so the table is queried exactly as it was
# built.
TOKEN_RE = config.LANGUAGE.QUERY_TOKEN_RE

# The text fields of the OpenSearch mapping. Keeping this list aligned with
# the index is the point: the table should say what BM25 can match on.
TEXT_FIELDS = ("title", "description", "tags", "organization")


def field_text(element, field):
    value = element.get(field)
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elements", default=config.ELEMENTS_FILE)
    parser.add_argument("--out", default=config.TOKEN_DF_FILE)
    args = parser.parse_args()

    if not os.path.exists(args.elements):
        raise SystemExit("no corpus at %s" % args.elements)

    df = Counter()
    total = 0
    with open(args.elements) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            element = json.loads(line)
            total += 1
            # Document frequency, not term frequency: a token counts once per
            # element however often it occurs in it.
            seen = set()
            for field in TEXT_FIELDS:
                for token in TOKEN_RE.findall(field_text(element, field)):
                    seen.add(token.lower())
            df.update(seen)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({"corpus_size": total,
                   "fields": list(TEXT_FIELDS),
                   "df": dict(df)}, f)

    print("%d elements, %d distinct tokens -> %s" % (total, len(df), args.out))
    singletons = sum(1 for n in df.values() if n == 1)
    print("  %d tokens occur in exactly one element (%.1f%%)"
          % (singletons, 100.0 * singletons / max(len(df), 1)))
    print("\n  most frequent: %s"
          % ", ".join("%s(%d)" % (t, n) for t, n in df.most_common(8)))
    probe = ["along", "layout", "mapped", "buyer", "resident", "signals",
             "covering", "densty", "revene", "treament", "mitchel",
             "datsets", "maningham", "infrastucture", "albery", "victora"]
    print("\n  probe (the first seven should be present, the rest absent):")
    for word in probe:
        print("    %-15s df=%d" % (word, df.get(word, 0)))


if __name__ == "__main__":
    main()
