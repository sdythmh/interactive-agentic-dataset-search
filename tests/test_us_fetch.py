"""ingest/fetch_catalog_api.py and corpora/us/, offline.

Usage
-----
    python tests/test_us_fetch.py
"""
import base64
import collections
import contextlib
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid

os.environ["CORPUS"] = "us"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests                                    # noqa: E402

import config                                      # noqa: E402
import corpora                                     # noqa: E402
from ingest import fetch_catalog_api as fc         # noqa: E402
from ingest import fetch_ckan                      # noqa: E402

SAMPLE = os.path.join(ROOT, "data/us/probe_sample.jsonl")
PY = sys.executable
FAKE_KEY = "FAKE-KEY-0a1b2c3d4e5f6789"
PREFIX = "https://catalog.data.gov/dataset/"
UK_ELEMENT_FIELDS = ["id", "title", "description", "type", "formats", "tags",
                     "organization", "url", "geometry", "created", "modified"]

# Regression baseline: what the mapping gives over the 266 probe records
# (data/us/probe_sample.jsonl, not part of this repository; these pin the code,
# not the catalogue).
EXPECT_N = 266
EXPECT_WITH_GEOMETRY = 63
EXPECT_ACCEPTED = {"dcat.spatial:bbox": 7, "spatial_shape": 56}
EXPECT_REJECTED = {"dcat.spatial: JSON-like, not GeoJSON": 2,
                   "dcat.spatial: free text": 13,
                   "dcat.spatial: zero area (point or line)": 1}
EXPECT_NO_CANDIDATE = 191
EXPECT_GLOBAL = 11
EXPECT_CREATED_EMPTY = 86
EXPECT_MODIFIED_EMPTY = 21
EXPECT_TYPES = {"dataset": 184, "map": 55, "publication": 15, "software": 10,
                "notebook": 2}

FAILURES = []


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got  %r\n         want %r" % (label, got, want))
        FAILURES.append(label)


# ---- fakes -------------------------------------------------------------------
class FakeResp:
    def __init__(self, status=200, body=None, headers=None, text=None):
        self.status_code = status
        self._body = body
        self.headers = headers if headers is not None else {}
        raw = text if text is not None else (json.dumps(body) if body is not None else "")
        self.text = raw
        self.content = raw.encode("utf-8")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def page(results, after=None, remaining=900, headers=None):
    body = {"sort": "last_harvested_date", "results": results}
    if after:
        body["after"] = after
    h = {"X-RateLimit-Remaining": str(remaining)}
    h.update(headers or {})
    return FakeResp(200, body, h)


def enc(sort_key):
    """A cursor as the portal builds it: base64 of the last record's sort key."""
    return base64.urlsafe_b64encode(
        json.dumps(sort_key, separators=(",", ":")).encode()).decode()


def mkrec(i):
    r = rec("r%03d" % i)
    r["_sort"] = [1790000000000 - i, 1.0, 0, "id-%03d" % i]
    return r


def pages_script(npages, per=2, start=0):
    """A walk of `npages` pages whose cursors are consistent with their records."""
    out, i = [], start
    for k in range(npages):
        rs = [mkrec(i + j) for j in range(per)]
        i += per
        out.append(page(rs, enc(rs[-1]["_sort"]) if k < npages - 1 else None))
    return out


class FakeSession:
    """Plays a script of responses (or exceptions); records every call."""

    def __init__(self, script, time_source=None):
        self.script = list(script)
        self.calls = []
        self.time_source = time_source

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True):
        self.calls.append({"url": url, "params": dict(params or {}),
                           "headers": dict(headers or {}), "timeout": timeout,
                           "allow_redirects": allow_redirects,
                           "at": self.time_source.now if self.time_source else None})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s

    def wall(self):
        return 1.7e9 + self.now


def make_client(script, out_dir, key=FAKE_KEY):
    ft = FakeTime()
    sess = FakeSession(script, ft)
    client = fc.Client(key, out_dir, session=sess, clock=ft.clock, sleep=ft.sleep,
                       wall=ft.wall)
    return client, sess, ft


def rec(slug, org="Org A", **kw):
    r = {"slug": slug, "title": "Title " + slug, "description": "d " + slug,
         "identifier": "ident-" + slug, "keyword": ["a", "b"], "theme": [],
         "organization": {"name": org, "slug": org.lower().replace(" ", "-")},
         "spatial_shape": None, "has_spatial": False,
         "dcat": {"issued": "2020-01-01", "modified": "2021-02-03T04:05:06Z",
                  "distribution": [{"format": "CSV", "mediaType": "text/csv"}]}}
    r.update(kw)
    return r


def raw_file(out_dir, slug, records):
    d = os.path.join(out_dir, "raw")
    os.makedirs(d, exist_ok=True)
    with gzip.open(os.path.join(d, slug + ".jsonl.gz"), "wt", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(out_dir, ".harvest_done"), "a") as fh:
        fh.write(slug + "\n")


def read_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def file_hash(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn(*a, **k)
    return out, buf.getvalue()


# ---- 1. the corpus -------------------------------------------------------------
def test_corpus():
    print("\n1. corpora/us loads and carries what was asked")
    spec = corpora.load("us")
    c = spec.corpus
    check("42 required fields all present",
          [f for f in corpora.REQUIRED_CORPUS_FIELDS if not hasattr(c, f)], [])
    # places.py was added after this test was written (section C of the
    # "ready to index" step): the spatial path is now on, from 51 generated boxes.
    check("places module present with 51 boxes (tools/test_us_places.py checks them)",
          None if spec.places is None else len(spec.places.BBOXES), 51)
    for name, want in (("NAME", "us"), ("INDEX_NAME", "knowledge-elements-us"),
                       ("NEO4J_URI", "bolt://localhost:7689"), ("DATA_DIR", "data/us"),
                       ("EVAL_DIR", "eval/us"), ("DATASET_URL_PREFIX", PREFIX),
                       ("DATASET_URL_TEMPLATE", None), ("SAMPLE_SEED", 42),
                       ("SAMPLE_SIZE", 8000), ("SAMPLE_GEO_FRACTION", -1),
                       ("SAMPLE_FOCUS", ""), ("SAMPLE_FOCUS_N", 0),
                       ("VOCAB_STOP_EXTRA", ("united", "states", "us", "u.s.", "usa")),
                       ("PROMPT_ROUTER_DOMAIN", "a US"), ("PROMPT_REASON_DOMAIN", "a US"),
                       ("PROMPT_GEN_DOMAIN", "a US"), ("PROMPT_REWRITE_DOMAIN", "US"),
                       ("CATALOG_API_BASE", "https://api.gsa.gov/technology/datagov/v4"),
                       ("CATALOG_PER_PAGE", 1000), ("CATALOG_HOURLY_LIMIT", 900)):
        check(name, getattr(c, name), want)
    au = corpora.load("au").corpus
    for name in ("MIN_SHARED_TAGS", "EDGE_WEIGHT", "MAX_TAG_DF", "MAX_EDGES_PER_ELEMENT",
                 "MAX_CANDIDATE_PAIRS"):
        check("graph parameter %s copied from au" % name, getattr(c, name), getattr(au, name))
    for name in ("RAW_FILE", "ELEMENTS_FILE", "HARVEST_DONE_FILE", "VOCAB_INDEX_FILE",
                 "TOKEN_DF_FILE"):
        check("%s is under data/us/" % name, getattr(c, name).startswith("data/us/"), True)


# ---- 2. the mapping over the probe sample ----------------------------------------
def test_mapping_sample():
    print("\n2. record -> element over the probe sample")
    if not os.path.exists(SAMPLE):
        print("  SKIP data/us/probe_sample.jsonl is absent (git-ignored); the pinned "
              "baseline was NOT checked")
        return
    records = [json.loads(x) for x in open(SAMPLE, encoding="utf-8") if x.strip()]
    stats, counts = fc.FootprintStats(), collections.Counter()
    els = [fc.to_element(r, stats, counts) for r in records]
    check("records in the sample", len(records), EXPECT_N)
    check("field list is the uk element's, in order",
          all(list(e) == UK_ELEMENT_FIELDS for e in els), True)
    uk_path = os.path.join(ROOT, "data/uk/elements.jsonl")
    if os.path.exists(uk_path):
        with open(uk_path) as fh:
            check("... and equals the first line of data/uk/elements.jsonl",
                  list(json.loads(fh.readline())), UK_ELEMENT_FIELDS)
    check("id is a UUID string",
          all(str(uuid.UUID(e["id"])) == e["id"] for e in els), True)
    check("id is derived from the slug alone",
          all(e["id"] == str(uuid.uuid5(uuid.NAMESPACE_URL, PREFIX + r["slug"]))
              for e, r in zip(els, records)), True)
    check("ids unique", len({e["id"] for e in els}), EXPECT_N)
    check("id[:8] unique over the sample", len({e["id"][:8] for e in els}), EXPECT_N)
    check("the first 8 characters of every id are hex (pipeline.CITATION_RE)",
          all(re.fullmatch("[0-9a-f]{8}", e["id"][:8]) for e in els), True)
    check("organization is organization.name",
          all(e["organization"] == r["organization"]["name"] for e, r in zip(els, records)), True)
    check("url is prefix + slug",
          all(e["url"] == PREFIX + r["slug"] for e, r in zip(els, records)), True)
    check("title and description come from the top level",
          all(e["title"] == r["title"] and e["description"] == r["description"]
              for e, r in zip(els, records)), True)
    check("tags are the keyword list",
          all(e["tags"] == [t for t in r["keyword"] if isinstance(t, str) and t.strip()]
              for e, r in zip(els, records)), True)
    check("organization never missing in the sample", counts["organization missing"], 0)
    check("formats are sorted, unique, non-empty strings",
          all(e["formats"] == sorted(set(e["formats"])) and all(e["formats"]) for e in els), True)
    check("created empty", sum(1 for e in els if not e["created"]), EXPECT_CREATED_EMPTY)
    check("modified empty", sum(1 for e in els if not e["modified"]), EXPECT_MODIFIED_EMPTY)
    check("dates are cut to <= 19 characters",
          all(len(e["created"]) <= 19 and len(e["modified"]) <= 19 for e in els), True)
    check("type distribution", dict(collections.Counter(e["type"] for e in els)), EXPECT_TYPES)
    check("elements with a geometry", sum(1 for e in els if e["geometry"]), EXPECT_WITH_GEOMETRY)
    check("geometry accepted, by source", dict(stats.accepted), EXPECT_ACCEPTED)
    check("geometry rejected, by source and reason", dict(stats.rejects), EXPECT_REJECTED)
    check("records with no spatial candidate", stats.no_field, EXPECT_NO_CANDIDATE)
    check("global extents kept", stats.global_extent, EXPECT_GLOBAL)
    check("accepted + elements without geometry = records",
          sum(stats.accepted.values()) + sum(1 for e in els if e["geometry"] is None), EXPECT_N)
    ok = True
    for e in els:
        g = e["geometry"]
        if g is None:
            continue
        (a, b), (c, d) = g["coordinates"]
        ok = ok and g["type"] == "envelope" and a <= c and d <= b
    check("every envelope is [[min_lon, max_lat], [max_lon, min_lat]] with min<=max", ok, True)


# ---- 3. geometry, dates, formats on made-up values -----------------------------------
def geo(dcat_spatial=None, shape=None):
    stats = fc.FootprintStats()
    r = {"slug": "x", "spatial_shape": shape, "dcat": {"spatial": dcat_spatial}}
    return fc.get_geometry(r, stats), stats


def test_geometry():
    print("\n3. geometry rules on made-up values")
    g, s = geo("-77.1 38.8 -76.9 38.9")
    check("space-separated bbox parses as W S E N",
          g, {"type": "envelope", "coordinates": [[-77.1, 38.9], [-76.9, 38.8]]})
    check("... counted as an accepted dcat.spatial:bbox", dict(s.accepted), {"dcat.spatial:bbox": 1})
    g, s = geo("-77.1140,38.8134,-76.9099,38.9949")
    check("comma-separated bbox parses", g["coordinates"], [[-77.114, 38.9949], [-76.9099, 38.8134]])
    g, s = geo("-77.1, 38.8 , -76.9,38.9")
    check("mixed separators parse", g is not None, True)
    g, s = geo("10 0 -10 5")
    check("W>E is rejected", g, None)
    check("... and counted as W>E", dict(s.rejects), {"dcat.spatial: W>E": 1})
    g, s = geo("-10 20 10 5")
    check("S>N is rejected and counted", dict(s.rejects), {"dcat.spatial: S>N": 1})
    g, s = geo("-10 -95 10 5")
    check("|lat|>90 is rejected and counted", dict(s.rejects),
          {"dcat.spatial: latitude out of range": 1})
    g, s = geo("-190 0 10 5")
    check("|lon|>180 is rejected and counted", dict(s.rejects),
          {"dcat.spatial: longitude out of range": 1})
    g, s = geo("64 -150 65 -149")
    check("a lat/lon-swapped string (Alaska) fails the latitude test", dict(s.rejects),
          {"dcat.spatial: latitude out of range": 1})
    g, s = geo("-180 -90 180 90")
    check("a global extent is kept", g["coordinates"], [[-180.0, 90.0], [180.0, -90.0]])
    check("... and counted as global", s.global_extent, 1)
    g, s = geo("-100 40 -100 41")
    check("zero-width bbox is rejected as zero area", dict(s.rejects),
          {"dcat.spatial: zero area (point or line)": 1})
    g, s = geo("National: 50 states and District of Columbia")
    check("free text is rejected and counted", dict(s.rejects), {"dcat.spatial: free text": 1})
    g, s = geo('["CARTESIAN", [{"WestBoundingCoordinate": -180.0}]]')
    check("JSON-like but not GeoJSON is counted as such", dict(s.rejects),
          {"dcat.spatial: JSON-like, not GeoJSON": 1})
    g, s = geo("{{extent:computeSpatialProperty}}")
    check("a template placeholder is JSON-like, not GeoJSON", dict(s.rejects),
          {"dcat.spatial: JSON-like, not GeoJSON": 1})
    poly = ('{"type":"Polygon","coordinates":[[[-125,24],[-66,24],[-66,49],[-125,49],[-125,24]]]}')
    g, s = geo(poly)
    check("a GeoJSON string goes through parse_geom", g["coordinates"], [[-125.0, 49.0], [-66.0, 24.0]])
    check("... source dcat.spatial:geojson", dict(s.accepted), {"dcat.spatial:geojson": 1})
    g, s = geo("POLYGON((0 0, 0 2, 2 2, 2 0, 0 0))")
    check("WKT is accepted", g["coordinates"], [[0.0, 2.0], [2.0, 0.0]])
    small = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]}
    g, s = geo("-10 -10 10 10", shape=small)
    check("the largest candidate wins (bbox over a small spatial_shape)",
          g["coordinates"], [[-10.0, 10.0], [10.0, -10.0]])
    g, s = geo("-10 -10 10 10", shape={"type": "Polygon", "coordinates":
                                       [[[-10, -10], [-10, 10], [10, 10], [10, -10], [-10, -10]]]})
    check("on equal area spatial_shape wins", dict(s.accepted), {"spatial_shape": 1})
    g, s = geo("10 0 -10 5", shape=small)
    check("a rejected candidate does not block the other", g is not None, True)
    check("... and the rejection is still counted", dict(s.rejects), {"dcat.spatial: W>E": 1})
    g, s = geo("")
    check("an empty value is no candidate", (g, s.no_field), (None, 1))
    g, s = geo(None, shape={"type": "Point", "coordinates": [1, 2]})
    check("a point spatial_shape is rejected as zero area", dict(s.rejects),
          {"spatial_shape: zero area (point or line)": 1})
    g, s = geo(None, shape={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [200, 1], [0, 0]]]})
    check("a spatial_shape beyond 180 degrees is rejected", dict(s.rejects),
          {"spatial_shape: longitude out of range": 1})

    print("\n   dates")
    for raw, want in (("2020-01-01", "2020-01-01"),
                      ("2021-02-03T04:05:06.789Z", "2021-02-03T04:05:06"),
                      ("2021-02-03T04:05:06+05:30", "2021-02-03T04:05:06"),
                      ("2021-02-03 04:05:06", "2021-02-03 04:05:06"),
                      ("R/P9D", ""), ("R/PT9M", ""), ("2020-02-30", ""), ("", ""),
                      (None, ""), (20200101, ""), ("not a date", "")):
        check("parse_date(%r)" % (raw,), fc.parse_date(raw), want)

    print("\n   formats")
    for raw, want in (("text/csv", "csv"), ("CSV", "csv"), (".CSV", "csv"),
                      ("application/pdf", "pdf"), ("application/zip", "application/zip"),
                      ("ZIP", "zip"), ("text/csv; charset=utf-8", "csv"),
                      ("https://www.iana.org/assignments/media-types/text/csv", "csv"),
                      ("application/vnd.google-earth.kml+xml", "kml"), (None, ""), ("  ", "")):
        check("norm_format(%r)" % (raw,), fc.norm_format(raw), want)
    known = (fetch_ckan.DOC_FORMATS | fetch_ckan.MAP_FORMATS | fetch_ckan.SERVICE_FORMATS
             | fetch_ckan.NOTEBOOK_FORMATS | fetch_ckan.SOFTWARE_FORMATS | {"csv"})
    check("every MIME mapping lands on a name classify() reads (or csv)",
          sorted(set(fc.MIME_NAMES.values()) - known), [])
    r = rec("pub", title="Annual Report 2020",
            dcat={"distribution": [{"mediaType": "application/pdf"}]})
    el = fc.to_element(r, fc.FootprintStats(), collections.Counter())
    check("a PDF-only record titled 'Report' is a publication", el["type"], "publication")

    print("\n   rejects and defaults")
    try:
        fc.to_element(dict(rec("x"), slug=None), fc.FootprintStats(), collections.Counter())
        check("a record with no slug is rejected", "no exception", "Rejected")
    except fc.Rejected:
        check("a record with no slug is rejected", "Rejected", "Rejected")
    c = collections.Counter()
    el = fc.to_element(rec("o", organization=None), fc.FootprintStats(), c)
    check("a missing organization is '' and counted", (el["organization"], c["organization missing"]), ("", 1))
    el = fc.to_element(rec("d", dcat={"modified": "R/P1Y"}), fc.FootprintStats(), collections.Counter())
    check("an unparsable date is the empty string, not None", (el["created"], el["modified"]), ("", ""))


# ---- 4. the client and the walk --------------------------------------------------------
def test_walk():
    print("\n4. cursor walk, throttle, failures (fake session)")
    with tempfile.TemporaryDirectory() as tmp:
        script = [page([rec("a1"), rec("a2")], after="C1"),
                  page([rec("a3")], after="C2"),
                  page([rec("a4")])]
        client, sess, ft = make_client(script, tmp)
        n, pages, finished = fc.fetch_org(client, "org-a", "last_harvested_date", tmp)
        check("the walk follows `after` until it is absent",
              (n, pages, finished, len(sess.calls)), (4, 3, True, 3))
        check("page 1 has no cursor", "after" in sess.calls[0]["params"], False)
        check("page 2 and 3 send the cursor the previous page returned",
              [c["params"].get("after") for c in sess.calls[1:]], ["C1", "C2"])
        base = {k: v for k, v in sess.calls[0]["params"].items()}
        check("every other parameter is identical across pages",
              all({k: v for k, v in c["params"].items() if k != "after"} == base for c in sess.calls), True)
        check("parameters: empty q, org_slug, per_page 1000, sort", base,
              {"q": "", "org_slug": "org-a", "per_page": 1000, "sort": "last_harvested_date"})
        check("the key is sent in X-Api-Key",
              all(c["headers"] == {"X-Api-Key": FAKE_KEY} for c in sess.calls), True)
        check("the key is not in the URL or the parameters",
              any(FAKE_KEY in (c["url"] + json.dumps(c["params"])) for c in sess.calls), False)
        check("redirects are not followed", all(c["allow_redirects"] is False for c in sess.calls), True)
        gaps = [b["at"] - a["at"] for a, b in zip(sess.calls, sess.calls[1:])]
        check("requests start >= 1 s apart on the injected monotonic clock", min(gaps) >= 1.0, True)
        check("the default clock is time.monotonic, not the wall clock",
              fc.Client.__init__.__defaults__[1] is __import__("time").monotonic, True)
        raw = os.path.join(tmp, "raw")
        check("the .part was renamed away", sorted(os.listdir(raw)), ["org-a.jsonl.gz"])
        check("records are written in cursor order",
              [r["slug"] for r in read_gz(os.path.join(raw, "org-a.jsonl.gz"))],
              ["a1", "a2", "a3", "a4"])
        check("the slug is in the done file only after the rename",
              fc.read_done(os.path.join(tmp, ".harvest_done")), {"org-a"})
        log = [json.loads(x) for x in open(os.path.join(tmp, "harvest_requests.jsonl"))]
        check("one log line per request, with n, bytes, remaining",
              [(r["status"], r["n"], r["remaining"]) for r in log],
              [(200, 2, 900), (200, 1, 900), (200, 1, 900)])
        check("the log carries no key", FAKE_KEY in json.dumps(log), False)

    print("\n   429 stops at once, writes no .jsonl.gz, retries nothing")
    with tempfile.TemporaryDirectory() as tmp:
        script = [page([rec("b1")], after="C1"),
                  FakeResp(429, {"error": "over limit"}, {"X-RateLimit-Remaining": "0"}),
                  page([rec("never")])]
        client, sess, ft = make_client(script, tmp)
        try:
            fc.fetch_org(client, "org-b", "last_harvested_date", tmp)
            got = "no stop"
        except fc.HarvestStop:
            got = "HarvestStop"
        check("HTTP 429 raises HarvestStop", got, "HarvestStop")
        check("... after exactly 2 requests (no retry)", len(sess.calls), 2)
        check("... leaving the .part and its cursor.json, never a .jsonl.gz",
              sorted(os.listdir(os.path.join(tmp, "raw"))),
              ["org-b.cursor.json", "org-b.jsonl.gz.part"])
        cj = json.load(open(os.path.join(tmp, "raw", "org-b.cursor.json")))
        check("... and the cursor says page 1 is saved and C1 is next",
              (cj["next_after"], cj["pages"], cj["records"]), ("C1", 1, 1))
        check("... and the slug is not marked done", fc.read_done(os.path.join(tmp, ".harvest_done")), set())
    with tempfile.TemporaryDirectory() as tmp:
        client, sess, ft = make_client([FakeResp(403, {"error": "forbidden"})], tmp)
        try:
            client.get("/search", {"q": ""})
            got = "no stop"
        except fc.HarvestStop:
            got = "HarvestStop"
        check("HTTP 403 raises HarvestStop too", (got, len(sess.calls)), ("HarvestStop", 1))

    print("\n   5xx and timeouts back off and retry, at most 6 times")
    with tempfile.TemporaryDirectory() as tmp:
        client, sess, ft = make_client([FakeResp(503, None, text="busy"),
                                        requests.exceptions.Timeout("slow " + FAKE_KEY),
                                        page([rec("c1")])], tmp)
        body, meta = client.get("/search", {"q": ""})
        check("two failures then success: 3 requests", len(sess.calls), 3)
        check("the back-off doubles (1 s, 2 s) on top of the 1 s spacing",
              [s for s in ft.sleeps if s in (1, 2)][:2], [1, 2])
        log = open(os.path.join(tmp, "harvest_requests.jsonl")).read()
        check("an exception's message is never logged (only its type)",
              ("Timeout" in log, FAKE_KEY in log), (True, False))
    with tempfile.TemporaryDirectory() as tmp:
        client, sess, ft = make_client([FakeResp(500, None, text="x")] * 8, tmp)
        try:
            client.get("/search", {"q": ""})
            got = "no error"
        except fc.FetchError:
            got = "FetchError"
        check("six failures in a row give up", (got, len(sess.calls)), ("FetchError", 6))
    with tempfile.TemporaryDirectory() as tmp:
        client, sess, ft = make_client([FakeResp(422, {"error": "bad"})], tmp)
        try:
            client.get("/search", {"q": ""})
            got = "no error"
        except fc.FetchError:
            got = "FetchError"
        check("a 4xx other than 429/403 is not retried", (got, len(sess.calls)), ("FetchError", 1))
    with tempfile.TemporaryDirectory() as tmp:
        client, sess, ft = make_client([FakeResp(301, None, {"Location": "https://evil.example/"})], tmp)
        try:
            client.get("/search", {"q": ""})
            got = "no error"
        except fc.FetchError:
            got = "FetchError"
        check("a redirect is an error, not followed", (got, len(sess.calls)), ("FetchError", 1))

    print("\n   quota")
    with tempfile.TemporaryDirectory() as tmp:
        client, sess, ft = make_client([page([], remaining=10), page([], remaining=900)], tmp)
        client.get("/search", {})
        before = len(ft.sleeps)
        client.get("/search", {})
        check("remaining < 50 pauses before the next request",
              fc.cfg("CATALOG_PAUSE_SECONDS") in ft.sleeps[before:], True)
    with tempfile.TemporaryDirectory() as tmp:
        client, sess, ft = make_client([page([]) for _ in range(5)], tmp)
        client.hourly = 3
        for _ in range(4):
            client.get("/search", {})
        check("the 4th request waits for the hourly window to roll (>= 3500 s)",
              max(ft.sleeps) >= 3500, True)
        check("... and starts after the first request is an hour old",
              sess.calls[3]["at"] - sess.calls[0]["at"] >= 3600, True)
    with tempfile.TemporaryDirectory() as tmp:
        c1, s1, t1 = make_client([page([])], tmp)
        c1.get("/search", {})
        ft = FakeTime()
        c2 = fc.Client(FAKE_KEY, tmp, session=FakeSession([]), clock=ft.clock, sleep=ft.sleep, wall=ft.wall)
        # Same wall clock as the first client, a little later: its request is still in the window.
        check("a restart counts the previous run's requests in the last hour", len(c2.window), 1)

    print("\n   sort check")
    with tempfile.TemporaryDirectory() as tmp:
        ms = 1790000000000
        rel = [dict(rec("s%d" % i), popularity=13, last_harvested_date="2026-09-10T18:23:47.313499",
                    _sort=[1.0, 13, "uuid-%d" % i]) for i in range(3)]
        lhd = []
        for i in range(3):
            r = dict(rec("s%d" % i), popularity=13, last_harvested_date="2026-09-10T18:23:47.313499")
            r["_sort"] = [int(fc._ms(r["last_harvested_date"])), 1.0, 13, "uuid-%d" % i]
            lhd.append(r)
        orgs = [{"slug": "big", "dataset_count": 900}, {"slug": "tiny", "dataset_count": 5},
                {"slug": "zero", "dataset_count": 0}]
        client, sess, ft = make_client([page(rel), page(lhd)], tmp)
        chosen = fc.choose_sort(client, orgs, tmp)
        check("relevance's _sort holds popularity, so last_harvested_date is chosen", chosen, "last_harvested_date")
        saved = json.load(open(os.path.join(tmp, "sort_choice.json")))
        check("the composition is recorded",
              saved["_sort_composition"],
              {"relevance": ["score", "popularity", "tie-break id"],
               "last_harvested_date": ["last_harvested_date", "score", "popularity", "tie-break id"]})
        check("2 requests, on the smallest organization with 3-50 datasets",
              (len(sess.calls), {c["params"]["org_slug"] for c in sess.calls}), (2, {"tiny"}))
        check("the choice is reused without a request", fc.choose_sort(client, orgs, tmp), "last_harvested_date")
        check("... still 2 requests", len(sess.calls), 2)
    with tempfile.TemporaryDirectory() as tmp:
        stable = [dict(rec("s"), _sort=["id-%d" % i]) for i in range(2)]
        lhd = [dict(rec("s"), popularity=1, _sort=[1, 1.0, 1, "x"])]
        client, sess, ft = make_client([page(stable), page(lhd)], tmp)
        check("a sort whose _sort holds neither field would be chosen first",
              fc.choose_sort(client, [{"slug": "tiny", "dataset_count": 5}], tmp), "relevance")

    print("\n   organizations.json")
    with tempfile.TemporaryDirectory() as tmp:
        body = {"organizations": [{"slug": "a", "dataset_count": 3}], "total": 1}
        client, sess, ft = make_client([FakeResp(200, body, {"X-RateLimit-Remaining": "999"})], tmp)
        orgs = fc.fetch_organizations(client, tmp)
        saved = json.load(open(os.path.join(tmp, "organizations.json")))
        check("the list comes back and the file holds the response and its time",
              (orgs, saved["response"] == body, "seconds" in saved, saved["status"]),
              (body["organizations"], True, True, 200))
        check("the request path is /organizations", sess.calls[0]["url"].endswith("/v4/organizations"), True)


# ---- 5. resume ---------------------------------------------------------------------------
def test_resume():
    print("\n5. --resume skips complete organizations and refetches an incomplete one")
    with tempfile.TemporaryDirectory() as tmp:
        raw_file(tmp, "a", [rec("a1")])
        part = os.path.join(tmp, "raw", "b.jsonl.gz.part")
        with gzip.open(part, "wt") as fh:
            fh.write(json.dumps(rec("STALE")) + "\n")
        script = [page([rec("b1")], after="C1"), page([rec("b2")]), page([rec("c1")])]
        client, sess, ft = make_client(script, tmp)
        orgs = [{"slug": s, "dataset_count": 1} for s in ("a", "b", "c")]
        done, out = quiet(fc.harvest, client, orgs, ["a", "b", "c"], "last_harvested_date", tmp, True)
        check("organization a was not requested",
              {c["params"]["org_slug"] for c in sess.calls}, {"b", "c"})
        check("b restarted from its first page (no cursor on its first request)",
              "after" in sess.calls[0]["params"], False)
        check("the stale .part content is gone",
              [r["slug"] for r in read_gz(os.path.join(tmp, "raw", "b.jsonl.gz"))], ["b1", "b2"])
        check("all three are done", done, {"a", "b", "c"})
        check("no .part is left", [n for n in os.listdir(os.path.join(tmp, "raw")) if n.endswith(".part")], [])
    print("\n   a harvest in a directory that already holds one needs --resume or --fresh")
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(ROOT, "data", "us")
        sub = os.path.join(base, "_test_tmp_%d" % os.getpid())
        os.makedirs(sub, exist_ok=True)
        try:
            raw_file(sub, "a", [rec("a1")])
            env = dict(os.environ, DATAGOV_API_KEY=FAKE_KEY)
            r = subprocess.run([PY, os.path.join(ROOT, "ingest", "fetch_catalog_api.py"),
                                "--out-dir", os.path.relpath(sub, ROOT)],
                               capture_output=True, text=True, env=env, cwd=ROOT)
            check("refused without --resume / --fresh (exit status, message)",
                  (r.returncode != 0, "--resume" in (r.stdout + r.stderr)), (True, True))
            check("... and nothing was sent (the key is in no output)", FAKE_KEY in r.stdout + r.stderr, False)
        finally:
            import shutil
            shutil.rmtree(sub, ignore_errors=True)


# ---- 6. merge -----------------------------------------------------------------------------
def test_merge():
    print("\n6. merge: order, de-duplication, determinism")
    with tempfile.TemporaryDirectory() as tmp:
        # Written out of slug order on purpose: the merge must still go by slug.
        raw_file(tmp, "zeta", [rec("shared", org="Zeta"), rec("z2", org="Zeta"), rec("z2", org="Zeta")])
        raw_file(tmp, "alpha", [rec("a1", org="Alpha"), rec("shared", org="Alpha"),
                                dict(rec("noslug"), slug=None),
                                rec("g", org="Alpha", spatial_shape={"type": "Polygon", "coordinates":
                                    [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]}),
                                rec("w", org="Alpha", dcat={"spatial": "-180 -90 180 90",
                                                            "issued": "R/P1D", "modified": "bad"})])
        with open(os.path.join(tmp, "organizations.json"), "w") as fh:
            json.dump({"response": {"organizations": [
                {"slug": "alpha", "dataset_count": 5}, {"slug": "zeta", "dataset_count": 3}]}}, fh)
        rep, out = quiet(fc.merge, tmp)
        els = [json.loads(x) for x in open(os.path.join(tmp, "elements_all.jsonl"))]
        check("organizations go by slug, records by cursor order, first id wins",
              [e["title"] for e in els], ["Title a1", "Title shared", "Title g", "Title w", "Title z2"])
        check("the kept 'shared' record is alpha's", [e["organization"] for e in els if e["title"] == "Title shared"], ["Alpha"])
        check("records_raw has the same records, in the same order",
              [r["slug"] for r in read_gz(os.path.join(tmp, "records_raw.jsonl.gz"))],
              ["a1", "shared", "g", "w", "z2"])
        check("raw records, kept elements", (rep["raw_records"], rep["elements_written"]), (8, 5))
        check("duplicates: one from another organization, one in the same",
              (rep["duplicate_ids_other_organization"], rep["duplicate_ids_same_organization"]), (1, 1))
        check("a record without a slug is rejected and counted", rep["rejected_no_slug"], 1)
        check("dataset_count differences", (rep["dataset_count_sum_all_organizations"],
                                            rep["elements_minus_dataset_count_all"]), (8, -3))
        check("geometry: one shape, one global bbox",
              (rep["elements_with_geometry"], rep["footprints"]["global_extent_kept"]), (2, 1))
        check("empty dates are counted", (rep["created_empty"], rep["modified_empty"]), (1, 1))
        check("per-organization raw minus dataset_count",
              (rep["per_organization"]["alpha"]["raw_minus_dataset_count"],
               rep["per_organization"]["zeta"]["raw_minus_dataset_count"]), (0, 0))
        check("no id[:8] collision", rep["id_prefix8_colliding_prefixes"], 0)
        check("the summary was printed", "merged 2 organization(s)" in out, True)
        names = ["elements_all.jsonl", "records_raw.jsonl.gz", "harvest_report.json"]
        first = {n: file_hash(os.path.join(tmp, n)) for n in names}
        quiet(fc.merge, tmp)
        second = {n: file_hash(os.path.join(tmp, n)) for n in names}
        check("a second merge is byte-identical (elements, gz, report)", second, first)
        check("no .tmp left", [n for n in os.listdir(tmp) if n.endswith(".tmp")], [])
        check("an incomplete organization is not merged (and is named)",
              quiet(fc.merge, tmp, ["alpha", "missing-one"])[0]["organizations_not_merged"],
              ["missing-one"])


# ---- 7. nothing leaks the key -------------------------------------------------------------
def test_key():
    print("\n7. the key appears nowhere but the request header")
    with tempfile.TemporaryDirectory() as tmp:
        body = {"organizations": [{"slug": "a", "dataset_count": 2}], "total": 1}
        script = [FakeResp(200, body, {"X-RateLimit-Remaining": "999"}),
                  page([rec("a1")], after="C1"), page([rec("a2")])]
        client, sess, ft = make_client(script, tmp)

        def run():
            orgs = fc.fetch_organizations(client, tmp)
            fc.harvest(client, orgs, ["a"], "last_harvested_date", tmp, False)
            return fc.merge(tmp, ["a"])
        _, out = quiet(run)
        leaked = []
        for dirpath, _, files in os.walk(tmp):
            for name in files:
                path = os.path.join(dirpath, name)
                data = open(path, "rb").read()
                if name.endswith(".gz"):
                    data = gzip.decompress(data)
                if FAKE_KEY.encode() in data:
                    leaked.append(name)
        check("no output file or log contains the key", leaked, [])
        check("printed output does not contain the key", FAKE_KEY in out, False)
        check("(the fake session did receive it in the header)",
              all(c["headers"].get("X-Api-Key") == FAKE_KEY for c in sess.calls), True)


# ---- 8. guards ----------------------------------------------------------------------------
def test_guards():
    print("\n8. guards")
    base = os.path.join(ROOT, "data", "us")
    check("data/us is accepted", fc.check_out_dir(base), os.path.realpath(base))
    check("data/us/_smoke is accepted", fc.check_out_dir(os.path.join(base, "_smoke")) .endswith("_smoke"), True)
    for bad in (os.path.join(ROOT, "data"), os.path.join(ROOT, "data", "uk"), "/tmp/x",
                os.path.join(base, "..", "uk")):
        try:
            fc.check_out_dir(bad)
            got = "accepted"
        except SystemExit:
            got = "refused"
        check("output outside data/us/ is refused: %s" % os.path.relpath(bad, ROOT), got, "refused")
    r = subprocess.run([PY, os.path.join(ROOT, "ingest", "fetch_catalog_api.py"), "--merge"],
                       capture_output=True, text=True, cwd=ROOT,
                       env={k: v for k, v in os.environ.items() if k != "CORPUS"} | {"CORPUS": "au"})
    check("CORPUS=au: the fetcher exits with a message", (r.returncode != 0, "CORPUS=us" in r.stdout + r.stderr),
          (True, True))
    r = subprocess.run([PY, os.path.join(ROOT, "ingest", "fetch_catalog_api.py"),
                        "--out-dir", "data/us/_no_key_test"],
                       capture_output=True, text=True, cwd=ROOT,
                       env={k: v for k, v in os.environ.items() if k != "DATAGOV_API_KEY"})
    check("no DATAGOV_API_KEY: stops at once", (r.returncode != 0, "DATAGOV_API_KEY is not set" in r.stdout + r.stderr),
          (True, True))
    check("... and made no directory for the run", os.path.exists(os.path.join(base, "_no_key_test")), False)
    r = subprocess.run([PY, "-c", "import ingest.fetch_catalog_api"], capture_output=True, text=True,
                       cwd=ROOT, env=dict(os.environ, CORPUS="au"))
    check("importing the module under CORPUS=au is harmless", r.returncode, 0)


# ---- 9. what the portal said at a 4xx ---------------------------------------------------
def test_stop_dump():
    print("\n9. B1: a 4xx is saved where it happened (key-free), and still stops at once")
    headers = {"Retry-After": "3600", "Content-Type": "application/json", "Server": "nginx",
               "Via": "1.1 abc", "Date": "Mon, 05 Oct 2026 14:40:32 GMT",
               "X-RateLimit-Remaining": "753", "X-RateLimit-Limit": "1000",
               "X-Cache": "MISS", "CF-Ray": "ray-1", "X-Amz-Cf-Id": "amz-1",
               "X-Request-Id": "req-" + FAKE_KEY,            # contains the key: withheld
               "Set-Cookie": "session=secret", "X-Forwarded-For": "10.0.0.1",
               "X-Api-Key": FAKE_KEY, "Authorization": "Bearer " + FAKE_KEY}
    body_text = ("rate limited " + FAKE_KEY + " ") + "x" * 6000
    params = {"q": "", "org_slug": "census", "after": "CUR"}
    with tempfile.TemporaryDirectory() as tmp:
        client, sess, ft = make_client([FakeResp(429, None, headers, text=body_text),
                                        page([rec("never")])], tmp)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                client.get("/search", params)
            got = "no stop"
        except fc.HarvestStop:
            got = "HarvestStop"
        printed = buf.getvalue()
        check("HTTP 429 still raises HarvestStop after exactly 1 request", (got, len(sess.calls)),
              ("HarvestStop", 1))
        files = sorted(os.listdir(os.path.join(tmp, "stops")))
        check("one file under stops/, named by UTC time", (len(files), bool(re.fullmatch(r"\d{8}T\d{6}Z\.json", files[0]))), (1, True))
        raw = open(os.path.join(tmp, "stops", files[0]), encoding="utf-8").read()
        doc = json.loads(raw)
        check("status, path and parameters are saved (the cursor included)",
              (doc["status"], doc["path"], doc["params"]), (429, "/search", params))
        check("the headers are the whitelist, by name, not the whole table",
              sorted(doc["headers"]),
              sorted(["Retry-After", "Content-Type", "Server", "Via", "Date", "X-RateLimit-Remaining",
                      "X-RateLimit-Limit", "X-Cache", "CF-Ray", "X-Amz-Cf-Id", "X-Request-Id"]))
        check("a header value that held the key is withheld", doc["headers"]["X-Request-Id"].startswith("[withheld"), True)
        check("Set-Cookie, X-Forwarded-For, X-Api-Key and Authorization are not saved",
              any(k in doc["headers"] for k in ("Set-Cookie", "X-Forwarded-For", "X-Api-Key", "Authorization")), False)
        check("the body is cut at 4 KB and its total size recorded",
              (len(doc["body_head"]) <= 4096, doc["body_bytes_total"]), (True, len(body_text)))
        check("the key is in nothing that was saved", FAKE_KEY in raw, False)
        check("Retry-After, Content-Type and the start of the body are printed",
              ("3600" in printed, "application/json" in printed, "rate limited" in printed), (True, True, True))
        check("the key is not printed", FAKE_KEY in printed, False)
        log = open(os.path.join(tmp, "harvest_requests.jsonl")).read()
        check("the 429's log line carries the whitelisted headers, not the key",
              ('"Retry-After": "3600"' in log, FAKE_KEY in log), (True, False))
    for status in (403, 404, 422):
        with tempfile.TemporaryDirectory() as tmp:
            client, sess, ft = make_client([FakeResp(status, {"e": 1}, {"Server": "x"})], tmp)
            try:
                quiet(client.get, "/search", {})
            except (fc.HarvestStop, fc.FetchError):
                pass
            check("HTTP %d is saved too" % status, len(os.listdir(os.path.join(tmp, "stops"))), 1)
    with tempfile.TemporaryDirectory() as tmp:
        client, sess, ft = make_client([page([rec("ok")])], tmp)
        client.get("/search", {})
        check("a 200 saves nothing under stops/", os.path.exists(os.path.join(tmp, "stops")), False)
    with tempfile.TemporaryDirectory() as tmp:
        client, sess, ft = make_client([FakeResp(503, None, text="b")] * 6, tmp)
        try:
            client.get("/search", {})
        except fc.FetchError:
            pass
        check("a 5xx is not a stop (retried, nothing saved)", os.path.exists(os.path.join(tmp, "stops")), False)


# ---- 10. header sampling -----------------------------------------------------------------
def test_header_sampling():
    print("\n10. B2: whitelisted headers on the first page and every 50th page")
    with tempfile.TemporaryDirectory() as tmp:
        script = pages_script(101, per=1)
        for r in script:
            r.headers.update({"Server": "nginx", "Set-Cookie": "no", "CF-Ray": "r"})
        client, sess, ft = make_client(script, tmp)
        fc.fetch_org(client, "big", "last_harvested_date", tmp)
        log = [json.loads(x) for x in open(os.path.join(tmp, "harvest_requests.jsonl"))]
        sampled = [i + 1 for i, r in enumerate(log) if "headers" in r]
        check("pages 1, 51 and 101 carry headers in the request log", sampled, [1, 51, 101])
        check("the sample is the whitelist (no Set-Cookie)",
              sorted(log[0]["headers"]), ["CF-Ray", "Server", "X-RateLimit-Remaining"])
        check("a page that is not sampled has no headers field", "headers" in log[1], False)


# ---- 11. pages as gzip members, cursor.json, resuming -------------------------------------
def lines_of(path):
    return [json.loads(x) for x in gzip.open(path, "rt", encoding="utf-8") if x.strip()]


def test_resume_pages():
    print("\n11. B3: resuming continues exactly where the walk stopped")
    sort = "last_harvested_date"
    with tempfile.TemporaryDirectory() as ref_dir:
        client, sess, ft = make_client(pages_script(5), ref_dir)
        fc.fetch_org(client, "a", sort, ref_dir)
        ref = lines_of(os.path.join(ref_dir, "raw", "a.jsonl.gz"))
        check("reference walk: 5 pages x 2 records", len(ref), 10)

    with tempfile.TemporaryDirectory() as tmp:
        full = pages_script(5)
        client, sess, ft = make_client(full[:2] + [FakeResp(429, {"e": "x"}, {"Retry-After": "60"})], tmp)
        try:
            quiet(fc.fetch_org, client, "a", sort, tmp)
        except fc.HarvestStop:
            pass
        part = os.path.join(tmp, "raw", "a.jsonl.gz.part")
        scan = fc.scan_part(part)
        check("each page is one gzip member (boundaries at 0, 2, 4 rows)", sorted(scan["bounds"]), [0, 2, 4])
        check("the file ends exactly on the last member", scan["clean"], True)
        cj = json.load(open(os.path.join(tmp, "raw", "a.cursor.json")))
        check("cursor.json: next_after, pages, records, sort, updated",
              (cj["next_after"], cj["pages"], cj["records"], cj["sort"], "updated" in cj),
              (full[1]._body["after"], 2, 4, sort, True))
        check("the 429'd page is not in the file", len(lines_of(part)), 4)

        # a new process: new client, the same directory
        client2, sess2, ft2 = make_client(pages_script(5)[2:], tmp)
        orgs = [{"slug": "a", "dataset_count": 10}]
        done, out = quiet(fc.harvest, client2, orgs, ["a"], sort, tmp, True)
        check("the first request after the stop carries the cursor of page 2",
              sess2.calls[0]["params"].get("after"), full[1]._body["after"])
        check("... and only the 3 missing pages were requested", len(sess2.calls), 3)
        got = lines_of(os.path.join(tmp, "raw", "a.jsonl.gz"))
        check("resumed record sequence == the one-pass sequence, record for record", got, ref)
        check("no .part and no cursor.json left", sorted(os.listdir(os.path.join(tmp, "raw"))), ["a.jsonl.gz"])
        check("a is done", done, {"a"})
        check("the multi-member file reads as one gzip stream (gzip.open)", len(got), 10)
        log = [json.loads(x) for x in open(os.path.join(tmp, "harvest_requests.jsonl"))]
        check("the first request of a resumed call has its headers logged",
              "headers" in [r for r in log if r["status"] == 200][2], True)
        events = [json.loads(x) for x in open(os.path.join(tmp, "harvest_resumes.jsonl"))]
        check("one resume event, after page 2, via cursor.json",
              [(e["slug"], e["at_page"], e["records"], e["how"]) for e in events], [("a", 2, 4, "cursor.json")])
        check("the output says where it continued", "continuing after page 2" in out, True)
        # B5: merge marks it
        rep, mout = quiet(fc.merge, tmp, ["a"])
        check("merge: elements are the same 10 records", rep["elements_written"], 10)
        check("merge report marks the resumed organization (count, break page)",
              rep["organizations_resumed"], {"a": {"count": 1, "at_pages": [2]}})
        check("... also under per_organization", rep["per_organization"]["a"]["resumed"],
              {"count": 1, "at_pages": [2]})
        check("... and the summary prints it", "resumed: a, 1 time(s), continued after page(s) 2" in mout, True)

    print("\n   a truncated tail member is dropped and the walk is aligned to the page boundary")
    def partial(tmp, pages=3):
        full = pages_script(5)
        client, sess, ft = make_client(full[:pages], tmp)
        fc.fetch_org(client, "a", sort, tmp, max_pages=pages)
        return full
    with tempfile.TemporaryDirectory() as tmp:
        full = partial(tmp)
        part = os.path.join(tmp, "raw", "a.jsonl.gz.part")
        good = os.path.getsize(part)
        member = gzip.compress(("".join(json.dumps(mkrec(90 + i)) + "\n" for i in range(2))).encode(), mtime=0)
        with open(part, "ab") as fh:
            fh.write(member[:len(member) // 2])
        scan = fc.scan_part(part)
        check("the scan stops at the last complete member and says the file is not clean",
              (max(scan["bounds"]), scan["clean"], scan["size"] > good), (6, False, True))
        state, why = fc.prepare_resume(tmp, "a", sort)
        check("resume cuts the partial member and continues from cursor.json",
              (state["how"], state["pages"], state["records"], state["after"], state["dropped_bytes"] > 0),
              ("cursor.json", 3, 6, full[2]._body["after"], True))
        check("the .part is back to the page boundary, byte for byte", os.path.getsize(part), good)
    with tempfile.TemporaryDirectory() as tmp:
        full = partial(tmp)
        part = os.path.join(tmp, "raw", "a.jsonl.gz.part")
        # a page written but not recorded: cursor.json lags one page behind the file
        cur = os.path.join(tmp, "raw", "a.cursor.json")
        fc.write_cursor(cur, full[1]._body["after"], 2, 4, sort, time.time)
        state, why = fc.prepare_resume(tmp, "a", sort)
        check("a page the cursor does not know about is cut (resume from the cursor's page)",
              (state["how"], state["pages"], state["records"]), ("cursor.json", 2, 4))
        check("... leaving 4 complete rows", len(lines_of(part)), 4)
    with tempfile.TemporaryDirectory() as tmp:
        full = partial(tmp)
        os.remove(os.path.join(tmp, "raw", "a.cursor.json"))
        state, why = fc.prepare_resume(tmp, "a", sort)
        check("no cursor.json: aligned through the request log to the last logged cursor",
              (state["how"], state["pages"], state["records"], state["after"]),
              ("request log", 2, 4, full[1]._body["after"]))
        check("... the page after that boundary was cut", len(lines_of(os.path.join(tmp, "raw", "a.jsonl.gz.part"))), 4)
    with tempfile.TemporaryDirectory() as tmp:
        full = partial(tmp)
        fc.write_cursor(os.path.join(tmp, "raw", "a.cursor.json"), "TAMPERED", 3, 6, sort, time.time)
        state, why = fc.prepare_resume(tmp, "a", sort)
        check("a cursor.json whose cursor does not match the record falls back to the log",
              state["how"], "request log")
    with tempfile.TemporaryDirectory() as tmp:
        partial(tmp)
        fc.write_cursor(os.path.join(tmp, "raw", "a.cursor.json"), "x", 3, 5, sort, time.time)
        state, why = fc.prepare_resume(tmp, "a", sort)
        check("records with no page boundary in the file also falls back to the log", state["how"], "request log")
    with tempfile.TemporaryDirectory() as tmp:
        partial(tmp)
        os.remove(os.path.join(tmp, "raw", "a.cursor.json"))
        os.remove(os.path.join(tmp, "harvest_requests.jsonl"))
        state, why = fc.prepare_resume(tmp, "a", sort)
        check("nothing to align with: start over, with the reason", (state, "no logged cursor" in why), (None, True))
        client, sess, ft = make_client(pages_script(5), tmp)
        done, out = quiet(fc.harvest, client, [{"slug": "a", "dataset_count": 10}], ["a"], sort, tmp, True)
        check("harvest then restarts from page 1 and says why",
              ("after" in sess.calls[0]["params"], "starting from page 1 again" in out, len(lines_of(os.path.join(tmp, "raw", "a.jsonl.gz")))),
              (False, True, 10))
    with tempfile.TemporaryDirectory() as tmp:
        partial(tmp)
        fc.write_cursor(os.path.join(tmp, "raw", "a.cursor.json"), "x", 3, 6, "relevance", time.time)
        state, why = fc.prepare_resume(tmp, "a", sort)
        check("a cursor written for another sort is not trusted (the log still aligns)", state["how"], "request log")

    print("\n   a single-stream .part from before this change (the census case)")
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "raw"))
        full = pages_script(5)
        rows = [r for pg in full[:3] for r in pg._body["results"]]
        part = os.path.join(tmp, "raw", "legacy.jsonl.gz.part")
        with gzip.open(part, "wt", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        before = file_hash(part)
        with open(os.path.join(tmp, "harvest_requests.jsonl"), "w") as fh:
            prev = None
            for k in range(4):
                params = {"q": "", "org_slug": "legacy", "per_page": 1000, "sort": sort}
                if prev:
                    params["after"] = prev
                status = 200 if k < 3 else 429
                fh.write(json.dumps({"t": 1.0 + k, "path": "/search", "params": params, "status": status,
                                     "n": 2 if k < 3 else None}) + "\n")
                prev = full[k]._body.get("after")
        state, why = fc.prepare_resume(tmp, "legacy", sort, force_log=True)
        check("the whole single member is one boundary: aligned to the 429's cursor",
              (state["how"], state["pages"], state["records"], state["after"], state["dropped_bytes"]),
              ("request log", 3, 6, full[2]._body["after"], 0))
        check("the .part is untouched (sha256 equal)", file_hash(part), before)
        fc.write_cursor(os.path.join(tmp, "raw", "legacy.cursor.json"), state["after"], state["pages"],
                        state["records"], sort, time.time)
        client, sess, ft = make_client(full[3:], tmp)
        done, out = quiet(fc.harvest, client, [{"slug": "legacy", "dataset_count": 10}], ["legacy"], sort, tmp, True)
        check("the next page is appended as a new member and the walk finishes",
              ([r["slug"] for r in lines_of(os.path.join(tmp, "raw", "legacy.jsonl.gz"))],
               sess.calls[0]["params"]["after"]),
              ([mkrec(i)["slug"] for i in range(10)], full[2]._body["after"]))
    print("\n   --rebuild-cursor, offline, from the command line")
    base = os.path.join(ROOT, "data", "us")
    sub = os.path.join(base, "_test_rebuild_%d" % os.getpid())
    try:
        os.makedirs(os.path.join(sub, "raw"))
        full = pages_script(3)
        with gzip.open(os.path.join(sub, "raw", "legacy.jsonl.gz.part"), "wt") as fh:
            for pg in full[:2]:
                for r in pg._body["results"]:
                    fh.write(json.dumps(r) + "\n")
        with open(os.path.join(sub, "harvest_requests.jsonl"), "w") as fh:
            for k, st in enumerate((200, 200, 429)):
                params = {"q": "", "org_slug": "legacy", "per_page": 1000, "sort": sort}
                if k:
                    params["after"] = full[k - 1]._body["after"]
                fh.write(json.dumps({"t": 1.0, "path": "/search", "params": params, "status": st, "n": 2}) + "\n")
        json.dump({"chosen": sort}, open(os.path.join(sub, "sort_choice.json"), "w"))
        env = {k: v for k, v in os.environ.items() if k != "DATAGOV_API_KEY"}
        r = subprocess.run([PY, os.path.join(ROOT, "ingest", "fetch_catalog_api.py"), "--rebuild-cursor",
                            "legacy", "--out-dir", os.path.relpath(sub, ROOT)],
                           capture_output=True, text=True, env=env, cwd=ROOT)
        cj = json.load(open(os.path.join(sub, "raw", "legacy.cursor.json")))
        check("--rebuild-cursor needs no key and writes cursor.json",
              (r.returncode, cj["next_after"], cj["pages"], cj["records"], cj["sort"]),
              (0, full[1]._body["after"], 2, 4, sort))
    finally:
        import shutil
        shutil.rmtree(sub, ignore_errors=True)


def test_max_pages_and_interval():
    print("\n12. --max-pages, --min-interval, --ping")
    sort = "last_harvested_date"
    with tempfile.TemporaryDirectory() as tmp:
        client, sess, ft = make_client(pages_script(3), tmp)
        orgs = [{"slug": "a", "dataset_count": 6}, {"slug": "b", "dataset_count": 1}]
        done, out = quiet(fc.harvest, client, orgs, ["a", "b"], sort, tmp, False, max_pages=2)
        check("--max-pages 2 stops after 2 pages, a is not done", (len(sess.calls), done), (2, set()))
        check("... and the state is resumable", json.load(open(os.path.join(tmp, "raw", "a.cursor.json")))["pages"], 2)
        client2, sess2, ft2 = make_client(pages_script(3)[2:] + [page([rec("b1")])], tmp)
        done, out = quiet(fc.harvest, client2, orgs, ["a", "b"], sort, tmp, True)
        check("resuming finishes a and b", done, {"a", "b"})
    with tempfile.TemporaryDirectory() as tmp:
        ft = FakeTime()
        sess = FakeSession([page([]), page([]), page([])], ft)
        c = fc.Client(FAKE_KEY, tmp, session=sess, clock=ft.clock, sleep=ft.sleep, wall=ft.wall, min_interval=3)
        for _ in range(3):
            c.get("/search", {})
        gaps = [b["at"] - a["at"] for a, b in zip(sess.calls, sess.calls[1:])]
        check("--min-interval 3 spaces request starts >= 3 s", min(gaps) >= 3.0, True)
        sess = FakeSession([page([]), page([])], ft)
        c = fc.Client(FAKE_KEY, tmp, session=sess, clock=ft.clock, sleep=ft.sleep, wall=ft.wall, min_interval=0.2)
        check("a value below 1 s cannot lower the floor", c.min_interval, 1.0)
        c = fc.Client(FAKE_KEY, tmp, session=FakeSession([]), clock=ft.clock, sleep=ft.sleep, wall=ft.wall)
        check("the default is 1.0, as before", c.min_interval, 1.0)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            fc.main(["--min-interval", "0.5"])
        got = "accepted"
    except SystemExit as exc:
        got = exc.code
    check("main() rejects --min-interval below 1 (argparse error, exit 2)", got, 2)
    with tempfile.TemporaryDirectory() as tmp:
        sentinel = os.path.join(tmp, "organizations.json")
        open(sentinel, "w").write('{"keep": "me"}')
        hdr = {"X-RateLimit-Remaining": "999", "X-RateLimit-Limit": "1000", "Server": "nginx", "Set-Cookie": "x=1"}
        client, sess, ft = make_client([FakeResp(200, {"organizations": [{"slug": "a"}], "total": 1}, hdr)], tmp)
        doc, out = quiet(fc.ping, client, tmp)
        saved = [f for f in os.listdir(os.path.join(tmp, "probes"))]
        check("ping saves the whitelisted headers under probes/ and prints them",
              (len(saved), sorted(doc["headers"]), "X-RateLimit-Limit: 1000" in out),
              (1, ["Server", "X-RateLimit-Limit", "X-RateLimit-Remaining"], True))
        check("ping leaves organizations.json alone", open(sentinel).read(), '{"keep": "me"}')
        check("ping made one request", len(sess.calls), 1)
        allbytes = b"".join(open(os.path.join(dp, f), "rb").read() for dp, _, fs in os.walk(tmp) for f in fs)
        check("no key in anything ping wrote", FAKE_KEY.encode() in allbytes, False)


def main():
    test_corpus()
    test_mapping_sample()
    test_geometry()
    test_walk()
    test_resume()
    test_merge()
    test_key()
    test_guards()
    test_stop_dump()
    test_header_sampling()
    test_resume_pages()
    test_max_pages_and_interval()
    print()
    if FAILURES:
        print("%d check(s) failed: %s" % (len(FAILURES), "; ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
