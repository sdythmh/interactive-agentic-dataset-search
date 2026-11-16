"""Turn the full harvest into the corpus we actually index.

Usage
-----
    python ingest/sample_elements.py                  # config defaults
    python ingest/sample_elements.py --n 20000
    python ingest/sample_elements.py --n 0            # keep everything
    python ingest/sample_elements.py --geo-frac 0.5

Why sample at all: the full data.gov.au catalogue has over 100,000 records,
and every extra element costs CPU embedding time at
build and dilutes retrieval at query time. A sample keeps the corpus at a size
where a 7B model on one consumer GPU can still be evaluated.
"""
import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def read_jsonl(path):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def matches_focus(element, terms):
    """Case-insensitive substring match over title, tags and publisher."""
    haystack = " ".join([
        element.get("title") or "",
        " ".join(element.get("tags") or []),
        element.get("organization") or "",
    ]).lower()
    return any(term in haystack for term in terms)


def round_robin(pool, quota, rng):
    """Draw `quota` elements, cycling across organisations for diversity."""
    if quota <= 0 or not pool:
        return []
    buckets = defaultdict(list)
    for element in pool:
        buckets[element.get("organization") or "(no organisation)"].append(element)
    for items in buckets.values():
        rng.shuffle(items)

    order = list(buckets)
    rng.shuffle(order)

    picked = []
    while len(picked) < quota:
        progressed = False
        for key in order:
            if buckets[key]:
                picked.append(buckets[key].pop())
                progressed = True
                if len(picked) >= quota:
                    break
        if not progressed:
            break  # pool exhausted
    return picked


def describe(elements, label):
    types = Counter(e["type"] for e in elements)
    orgs = len({e.get("organization") or "" for e in elements})
    geo = sum(1 for e in elements if e.get("geometry"))
    print("\n%s: %d elements, %d organisations, %d with a footprint (%.1f%%)"
          % (label, len(elements), orgs, geo,
             100.0 * geo / len(elements) if elements else 0.0))
    for name, count in types.most_common():
        print("   %-12s %6d  %5.1f%%"
              % (name, count, 100.0 * count / len(elements)))


def draw_sample(everything, n, geo_frac, focus, focus_n, seed):
    """The sample ingest builds, as a function: population in, sample out."""
    focus_terms = [t.strip().lower() for t in (focus or "").split(",")
                   if t.strip()]
    focus_part = []
    if focus_terms and n > 0 and focus_n > 0:
        rng = random.Random(seed)
        matching = [e for e in everything if matches_focus(e, focus_terms)]
        everything = [e for e in everything if not matches_focus(e, focus_terms)]
        geo_match = [e for e in matching if e.get("geometry")]
        plain_match = [e for e in matching if not e.get("geometry")]
        print("\nfocus terms %s match %d elements (%d with a footprint)"
              % (focus_terms, len(matching), len(geo_match)))
        # Footprints first, deliberately. The point of the focus slice is that
        # the spatial metadata in one region can be checked by hand.
        focus_part = round_robin(geo_match, focus_n, rng)
        if len(focus_part) < focus_n:
            focus_part += round_robin(plain_match,
                                      focus_n - len(focus_part), rng)
        print("focus slice: %d elements (%d with a footprint)"
              % (len(focus_part), sum(1 for e in focus_part if e.get("geometry"))))

    budget = n - len(focus_part)

    if n <= 0 or budget >= len(everything):
        sampled = focus_part + everything
        print("\nkeeping the whole population")
    elif geo_frac < 0:
        # Uniform sampling, no footprint stratification.
        rng = random.Random(seed)
        sampled = focus_part + round_robin(everything, budget, rng)
        rng.shuffle(sampled)
        print("\nuniform sample of the remainder, no footprint oversampling")
    else:
        rng = random.Random(seed)
        with_geo = [e for e in everything if e.get("geometry")]
        without = [e for e in everything if not e.get("geometry")]

        geo_quota = min(len(with_geo), int(round(budget * geo_frac)))
        geo_part = round_robin(with_geo, geo_quota, rng)
        plain_part = round_robin(without, budget - len(geo_part), rng)
        sampled = focus_part + geo_part + plain_part

        if len(geo_part) < geo_quota:
            print("\nonly %d footprints available, asked for %d"
                  % (len(geo_part), geo_quota))
        rng.shuffle(sampled)
    return sampled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="src", default=config.RAW_FILE)
    parser.add_argument("--out", dest="dst", default=config.ELEMENTS_FILE)
    parser.add_argument("--n", type=int, default=config.SAMPLE_SIZE,
                        help="target corpus size, 0 = keep everything")
    parser.add_argument("--geo-frac", type=float,
                        default=config.SAMPLE_GEO_FRACTION,
                        help="target share of elements that have a footprint; "
                             "pass -1 for a uniform sample with no "
                             "oversampling (0 means zero footprints)")
    parser.add_argument("--focus", default=config.SAMPLE_FOCUS,
                        help="comma-separated keywords matched against title, "
                             "tags and publisher; matching elements get their "
                             "own guaranteed slice of the corpus")
    parser.add_argument("--focus-n", type=int, default=config.SAMPLE_FOCUS_N,
                        help="size of that slice, footprints first")
    parser.add_argument("--seed", type=int, default=config.SAMPLE_SEED)
    args = parser.parse_args()

    if not os.path.exists(args.src):
        raise SystemExit("%s not found -- run ingest/fetch_ckan.py first" % args.src)

    everything = list(read_jsonl(args.src))
    describe(everything, "population (%s)" % args.src)

    sampled = draw_sample(everything, args.n, args.geo_frac, args.focus,
                          args.focus_n, args.seed)

    os.makedirs(os.path.dirname(args.dst) or ".", exist_ok=True)
    with open(args.dst, "w") as f:
        for element in sampled:
            f.write(json.dumps(element, ensure_ascii=False) + "\n")

    describe(sampled, "sample (%s)" % args.dst)
    print("\nseed=%d, so the same input gives the same sample" % args.seed)
    print("next: python ingest/build_indexes.py")


if __name__ == "__main__":
    main()
