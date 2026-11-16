"""Scheme A: build the corpus-derived vocabulary index for query expansion.

Usage
-----
    python ingest/build_vocabulary.py
"""
import argparse
import json
import os
import sys
from collections import Counter

import numpy as np
from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


MIN_DF_TAG = 2      # rarer tags are one-off typos or single-org quirks
MIN_DF_ORG = 1      # every publisher matters
MIN_DF_TITLE = 4    # title n-grams need more support to be trusted

# Boundary stopwords: dropped only when they sit at the start or end of a run,
# never used to filter interior tokens.
STOP = set(config.LANGUAGE.VOCAB_BOUNDARY_STOPWORDS) | set(config.VOCAB_STOP_EXTRA)

TOKEN_RE = config.LANGUAGE.VOCAB_TOKEN_RE


def read_jsonl(path):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def title_ngrams(title, n_max=3):
    """1..n_max word runs from a title. No NER, no capitalisation gate."""
    tokens = [t for t in TOKEN_RE.findall(title or "") if len(t) > 1]
    grams = []
    for n in range(1, n_max + 1):
        for i in range(len(tokens) - n + 1):
            run = tokens[i:i + n]
            if run[0].lower() in STOP or run[-1].lower() in STOP:
                continue
            grams.append(" ".join(run))
    return grams


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elements", default=config.ELEMENTS_FILE)
    parser.add_argument("--out", default=config.VOCAB_INDEX_FILE)
    args = parser.parse_args()

    print("scanning corpus %s" % args.elements)
    tag_df, org_df, title_df = Counter(), Counter(), Counter()
    n = 0
    for e in read_jsonl(args.elements):
        n += 1
        for t in set(e.get("tags") or []):
            t = t.strip()
            if t:
                tag_df[t] += 1
        org = (e.get("organization") or "").strip()
        if org:
            org_df[org] += 1
        for g in set(title_ngrams(e.get("title") or "")):
            title_df[g] += 1
    print("  %d elements; raw counts: tags=%d orgs=%d title-ngrams=%d"
          % (n, len(tag_df), len(org_df), len(title_df)))

    keep = []
    for term, df in tag_df.items():
        if df >= MIN_DF_TAG:
            keep.append(("tag", term, df))
    for term, df in org_df.items():
        if df >= MIN_DF_ORG:
            keep.append(("org", term, df))
    for term, df in title_df.items():
        if df >= MIN_DF_TITLE:
            keep.append(("title", term, df))
    print("  kept after df cut: %d terms (tag>=%d, org>=%d, title>=%d)"
          % (len(keep), MIN_DF_TAG, MIN_DF_ORG, MIN_DF_TITLE))

    print("loading %s (%s)" % (config.EMBED_MODEL, config.DEVICE))
    model = SentenceTransformer(config.EMBED_MODEL, device=config.DEVICE)
    texts = [t for _, t, _ in keep]
    print("encoding %d terms" % len(texts))
    vectors = model.encode(texts, normalize_embeddings=True,
                           batch_size=config.EMBED_BATCH,
                           show_progress_bar=True)
    vectors = np.asarray(vectors, dtype=np.float32)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    np.savez_compressed(
        args.out,
        vocab=np.array([t for _, t, _ in keep], dtype=object),
        kind=np.array([k for k, _, _ in keep], dtype=object),
        df=np.array([d for _, _, d in keep], dtype=np.int32),
        vectors=vectors,
    )
    print("wrote %s (%d terms, vectors %s)"
          % (args.out, len(keep), vectors.shape))


if __name__ == "__main__":
    main()
