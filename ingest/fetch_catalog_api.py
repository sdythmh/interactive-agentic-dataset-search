"""Harvest data.gov through the Catalog API (DCAT-US) into the us corpus files.

Usage
-----
    export DATAGOV_API_KEY=...        # read from the environment only
    CORPUS=us python ingest/fetch_catalog_api.py                 # full harvest
    CORPUS=us python ingest/fetch_catalog_api.py --resume        # after a stop, page by page
    CORPUS=us python ingest/fetch_catalog_api.py --resume --min-interval 3
    CORPUS=us python ingest/fetch_catalog_api.py --merge         # merge only, no network
    CORPUS=us python ingest/fetch_catalog_api.py --out-dir data/us/_smoke \\
        --slugs sba,frtib                                        # small trial

What it writes, all under data/us/ (or --out-dir, which must be inside it):
    organizations.json          GET /organizations, with the response time
    raw/<slug>.jsonl.gz         one organization's search records, in cursor order;
                                one gzip member per page, written to <slug>.jsonl.gz.part
                                and renamed when complete
    raw/<slug>.cursor.json      {next_after, pages, records, sort, updated}: where the walk
                                stands, replaced after every page (removed when complete)
    harvest_resumes.jsonl       one line per continued walk (slug, page, how)
    stops/<UTC>.json            what the portal said at a 4xx: status, whitelisted headers,
                                first 4 KB of the body
    .harvest_done               slugs whose raw file is complete (--resume)
    harvest_requests.jsonl      one line per request, without the key
    sort_choice.json            the sort parameter used and why
    records_raw.jsonl.gz        merged, de-duplicated full search records
    elements_all.jsonl          merged elements (the 11 fields of au / uk)
    harvest_report.json         what the merge saw
"""
import argparse
import base64
import collections
import gzip
import io
import json
import math
import os
import re
import sys
import time
import uuid
import zlib
from datetime import datetime, timezone

import requests
from shapely.geometry import box

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config
from ingest.fetch_ckan import classify, parse_geom


def cfg(name):
    """A us-corpus field that au / uk do not define; read at call time so that
    importing this module under another CORPUS stays harmless."""
    return getattr(config.CORPUS_SPEC.corpus, name)


class HarvestStop(Exception):
    """429 or 403: the portal is telling us to stop. Never retried."""


class FetchError(Exception):
    """A request that cannot be fixed by retrying, or retries exhausted."""


class Rejected(Exception):
    """A record that cannot become an element (counted, not written)."""


# =============================================================================
# Record -> element (rules 1-6 in corpora/us/corpus.py)
# =============================================================================
# ---- rule 1: id -------------------------------------------------------------
def element_id(slug):
    """uuid5 over the dataset URL: derivable from the slug alone, hex in the
    first 8 characters (the citation code needs that), no dependence on an
    undocumented field."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, config.DATASET_URL_PREFIX + slug))


# ---- rule 3: geometry -------------------------------------------------------
_NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
RE_BBOX4 = re.compile(r"^\s*(%s)(?:\s*,\s*|\s+)(%s)(?:\s*,\s*|\s+)(%s)(?:\s*,\s*|\s+)(%s)\s*$"
                      % ((_NUM,) * 4))
RE_WKT = re.compile(r"^\s*(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|"
                    r"MULTIPOLYGON|GEOMETRYCOLLECTION)\b", re.I)
GLOBAL_WIDTH = 359.9


class FootprintStats:
    """Footprint outcomes, by source and by reason. Rejections are counted per
    candidate, so a record whose two candidates are both rejected counts twice."""

    def __init__(self):
        self.no_field = 0                      # records with no candidate at all
        self.accepted = collections.Counter()  # winning candidate's source
        self.rejects = collections.Counter()   # "source: reason"
        self.global_extent = 0                 # kept, width >= GLOBAL_WIDTH

    def reject(self, source, reason):
        self.rejects["%s: %s" % (source, reason)] += 1

    def as_dict(self):
        return {"records_without_any_candidate": self.no_field,
                "accepted_by_source": dict(sorted(self.accepted.items())),
                "rejected_by_source_and_reason": dict(sorted(self.rejects.items())),
                "global_extent_kept": self.global_extent}


def _bbox_candidate(match):
    """Four numbers read as W S E N. Returns (shape, None) or (None, reason).
    The order of the checks is the order the reasons are reported in: range
    first (a lat/lon-swapped string fails there), then the ordering."""
    w, s, e, n = (float(match.group(i)) for i in range(1, 5))
    if not all(math.isfinite(v) for v in (w, s, e, n)):
        return None, "non-numeric coordinates"
    if abs(w) > 180 or abs(e) > 180:
        return None, "longitude out of range"
    if abs(s) > 90 or abs(n) > 90:
        return None, "latitude out of range"
    if w > e:
        return None, "W>E"
    if s > n:
        return None, "S>N"
    return box(w, s, e, n), None


def _dcat_spatial_candidate(value):
    """One dcat.spatial value -> (label, shape, reason). None if blank."""
    if isinstance(value, dict):
        geom = parse_geom(value)
        return (("dcat.spatial", geom, None) if geom is not None
                else ("dcat.spatial", None, "JSON-like, not GeoJSON"))
    if isinstance(value, list):
        return "dcat.spatial", None, "JSON-like, not GeoJSON"
    if not isinstance(value, str):
        return "dcat.spatial", None, "free text"
    text = value.strip()
    if not text:
        return None
    match = RE_BBOX4.match(text)
    if match:
        shape, reason = _bbox_candidate(match)
        return "dcat.spatial:bbox", shape, reason
    if text[0] in "{[":
        geom = parse_geom(text)
        if geom is not None:
            return "dcat.spatial:geojson", geom, None
        return "dcat.spatial", None, "JSON-like, not GeoJSON"
    if RE_WKT.match(text):
        geom = parse_geom(text)
        if geom is not None:
            return "dcat.spatial:wkt", geom, None
        return "dcat.spatial", None, "unparsable WKT"
    return "dcat.spatial", None, "free text"


def _geom_problem(geom):
    """Why a parsed geometry cannot be stored, or None. Same tests, same
    wording, as ingest/fetch_ckan.get_geometry."""
    if geom.is_empty:
        return "parsed but empty geometry"
    bounds = geom.bounds
    if not all(math.isfinite(b) for b in bounds):
        return "non-numeric coordinates"
    min_lon, min_lat, max_lon, max_lat = bounds
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        return "longitude out of range"
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        return "latitude out of range"
    if geom.area == 0:
        return "zero area (point or line)"
    return None


def get_geometry(record, stats):
    """The element's envelope, or None. Candidates: `spatial_shape` (a GeoJSON
    object) and `dcat.spatial`. The largest accepted one wins, as in
    fetch_ckan.get_geometry; ties go to the first (spatial_shape)."""
    raw = []
    shape = record.get("spatial_shape")
    if shape not in (None, "", {}, []):
        if isinstance(shape, dict):
            geom = parse_geom(shape)
            raw.append(("spatial_shape", geom,
                        None if geom is not None else "unparsable GeoJSON"))
        else:
            raw.append(("spatial_shape", None, "not a GeoJSON object"))
    dcat_value = (record.get("dcat") or {}).get("spatial")
    if dcat_value not in (None, "", [], {}):
        candidate = _dcat_spatial_candidate(dcat_value)
        if candidate is not None:
            raw.append(candidate)
    if not raw:
        stats.no_field += 1
        return None

    accepted = []
    for source, geom, reason in raw:
        group = source.split(":")[0]
        if geom is None:
            stats.reject(group, reason)
            continue
        problem = _geom_problem(geom)
        if problem:
            stats.reject(group, problem)
            continue
        accepted.append((source, geom))
    if not accepted:
        return None
    source, best = max(accepted, key=lambda c: c[1].area)   # first of equals
    min_lon, min_lat, max_lon, max_lat = best.bounds
    stats.accepted[source] += 1
    if max_lon - min_lon >= GLOBAL_WIDTH:
        stats.global_extent += 1
    return {"type": "envelope",
            "coordinates": [[min_lon, max_lat], [max_lon, min_lat]]}


# ---- rule 4: dates ----------------------------------------------------------
RE_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})"
                     r"(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?"
                     r"(?:Z|[+-]\d{2}:?\d{2})?)?$")


def parse_date(value):
    """The value cut to 19 characters if it is a date or a date-time, else "".
    DCAT's `modified` also carries ISO repeating durations ("R/P9D") that name a
    refresh interval, not a date; those become ""."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    m = RE_DATE.match(text)
    if not m:
        return ""
    y, mo, d, h, mi, s = (int(g) if g is not None else 0 for g in m.groups())
    try:
        datetime(y, mo, d, h, mi, s)
    except ValueError:
        return ""
    return text[:19]


# ---- rule 5: formats --------------------------------------------------------
# Only the formats classify() reads (ingest/fetch_ckan.py DOC_FORMATS,
# MAP_FORMATS, SERVICE_FORMATS, NOTEBOOK_FORMATS, SOFTWARE_FORMATS) get a
# MIME -> name entry, plus text/csv -> csv as asked. Anything else is only
# lower-cased and stripped of a leading dot, exactly as fetch_ckan.norm_format
# does. Only registered types are listed; a type I could not confirm is left to
# the fall-through rather than guessed.
MIME_NAMES = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/rtf": "rtf",
    "text/rtf": "rtf",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.google-earth.kml+xml": "kml",
    "application/vnd.google-earth.kmz": "kmz",
    "application/vnd.ogc.wms_xml": "wms",
    "application/x-ipynb+json": "ipynb",
    "application/soap+xml": "soap",
    "application/graphql": "graphql",
    "text/csv": "csv",
}
IANA_PREFIX = "https://www.iana.org/assignments/media-types/"


def norm_format(value):
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    if text.startswith(IANA_PREFIX):
        text = text[len(IANA_PREFIX):]
    text = text.split(";")[0].strip()
    return MIME_NAMES.get(text, text.lstrip("."))


def record_formats(record):
    names = set()
    for dist in (record.get("dcat") or {}).get("distribution") or []:
        if isinstance(dist, dict):
            for key in ("format", "mediaType"):
                name = norm_format(dist.get(key))
                if name:
                    names.add(name)
    return sorted(names)


# ---- the element ------------------------------------------------------------
def to_element(record, stats, counts):
    """One search record -> one element with the 11 fields of the au and uk
    element files. `stats` is a FootprintStats, `counts` a Counter."""
    slug = record.get("slug")
    if not isinstance(slug, str) or not slug.strip():
        raise Rejected("no slug")
    slug = slug.strip()
    dcat = record.get("dcat") or {}
    title = record.get("title") or ""
    formats = record_formats(record)

    org = record.get("organization")
    org_name = org.get("name") if isinstance(org, dict) else None
    if not isinstance(org_name, str) or not org_name.strip():
        org_name = ""
        counts["organization missing"] += 1

    tags = [t for t in (record.get("keyword") or []) if isinstance(t, str) and t.strip()]
    return {
        "id": element_id(slug),
        "title": title,
        "description": record.get("description") or "",
        "type": classify(title, formats),
        "formats": formats,
        "tags": tags,
        "organization": org_name,
        "url": config.DATASET_URL_PREFIX + slug,
        "geometry": get_geometry(record, stats),
        "created": parse_date(dcat.get("issued")),
        "modified": parse_date(dcat.get("modified")),
    }


# =============================================================================
# HTTP client: throttle, quota, retry, request log
# =============================================================================
# Response headers worth keeping, by name (never the whole table: it can carry
# cookies and echoed request data). Exact names, then prefixes; lower case.
HEADER_EXACT = frozenset((
    "retry-after", "content-type", "content-length", "content-encoding", "server",
    "via", "date", "age", "x-cache", "x-cache-hits", "x-request-id", "x-served-by",
    "x-runtime"))
HEADER_PREFIXES = ("x-ratelimit", "ratelimit", "cf-", "x-amz", "x-api-umbrella",
                   "x-envoy")
STOP_BODY_BYTES = 4096
HEADER_SAMPLE_EVERY = 50      # pages 1, 51, 101, ... of each organization


def whitelist_headers(headers, key=None):
    """The whitelisted response headers as a plain dict. A value that contains
    the API key is withheld, whatever the header's name."""
    out = {}
    for name, value in (headers or {}).items():
        low = str(name).lower()
        if low in HEADER_EXACT or low.startswith(HEADER_PREFIXES):
            text = str(value)
            if key and key in text:
                text = "[withheld: contained the API key]"
            out[str(name)] = text[:300]
    return out


class Client:
    """Every request goes through get(). Clocks and sleep are injectable so the
    tests run without waiting."""

    def __init__(self, key, out_dir, session=None, clock=time.monotonic,
                 sleep=time.sleep, wall=time.time, min_interval=None):
        self.key = key
        self.out_dir = out_dir
        self.session = session or requests.Session()
        if session is None:
            self.session.headers.update(
                {"User-Agent": "iads-harvester/0.3 (us harvest)"})
        self.clock, self.sleep, self.wall = clock, sleep, wall
        self.base = cfg("CATALOG_API_BASE")
        # The rule is >= 1 s; a larger value slows the harvest down, never speeds it up.
        self.min_interval = max(cfg("CATALOG_MIN_INTERVAL"),
                                min_interval if min_interval is not None else 0)
        self.hourly = cfg("CATALOG_HOURLY_LIMIT")
        self.low = cfg("CATALOG_LOW_QUOTA")
        self.pause = cfg("CATALOG_PAUSE_SECONDS")
        self.timeout = cfg("CATALOG_TIMEOUT")
        self.retries = cfg("CATALOG_RETRIES")
        self.log_path = os.path.join(out_dir, "harvest_requests.jsonl")
        self.last_start = None
        self.window = collections.deque()      # monotonic start times
        self.remaining = None
        self.requests_made = 0
        os.makedirs(out_dir, exist_ok=True)
        self._prefill_window()

    def _prefill_window(self):
        """Requests logged by an earlier run in the last hour still count against
        the hourly limit. The log holds wall-clock times (the only thing that
        survives a restart); they are turned into ages once, here, and the
        rolling window itself is monotonic. A wall clock that stepped back only
        makes an age look smaller, which is the safe direction."""
        if not os.path.exists(self.log_path):
            return
        now_wall, now_mono = self.wall(), self.clock()
        with open(self.log_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    t = json.loads(line)["t"]
                except (ValueError, KeyError):
                    continue
                age = max(0.0, now_wall - t)
                if age < 3600:
                    self.window.append(now_mono - age)

    def _gate(self):
        if self.last_start is not None:
            wait = self.min_interval - (self.clock() - self.last_start)
            if wait > 0:
                self.sleep(wait)
        now = self.clock()
        while self.window and now - self.window[0] >= 3600:
            self.window.popleft()
        if len(self.window) >= self.hourly:
            wait = self.window[0] + 3600 - now
            print("  hourly request limit (%d) reached, waiting %.0f s"
                  % (self.hourly, wait))
            self.sleep(max(wait, 0) + 1)
            now = self.clock()
            while self.window and now - self.window[0] >= 3600:
                self.window.popleft()
        if self.remaining is not None and self.remaining < self.low:
            print("  X-RateLimit-Remaining is %d (< %d), pausing %d s"
                  % (self.remaining, self.low, self.pause))
            self.sleep(self.pause)
            # Unknown until the next response says otherwise.
            self.remaining = None
        self.last_start = self.clock()
        self.window.append(self.last_start)

    def _log(self, wall, path, params, status, n, nbytes, remaining, seconds, attempt,
             headers=None):
        row = {"t": round(wall, 3),
               "time": datetime.fromtimestamp(wall, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "path": path, "params": params, "status": status, "n": n,
               "bytes": nbytes, "remaining": remaining,
               "seconds": round(seconds, 2), "attempt": attempt}
        if headers is not None:
            row["headers"] = headers          # sampled, whitelist only
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _dump_stop(self, path, params, resp, status, headers, attempt):
        """Keep what the portal said when it refused. The status, the
        whitelisted headers and the first 4 KB of the body go to
        stops/<UTC time>.json; Retry-After, Content-Type and the first 300
        characters of the body are printed. The key is scrubbed from the body
        and withheld from any header value; it is never in `params`."""
        body = resp.content[:STOP_BODY_BYTES].decode("utf-8", "replace")
        if self.key:
            body = body.replace(self.key, "[key removed]")
        stamp = datetime.fromtimestamp(self.wall(), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        doc = {"time": stamp, "path": path, "params": params, "status": status,
               "attempt": attempt, "headers": headers,
               "body_bytes_total": len(resp.content), "body_head": body}
        try:
            stops = os.path.join(self.out_dir, "stops")
            os.makedirs(stops, exist_ok=True)
            target, k = os.path.join(stops, stamp + ".json"), 1
            while os.path.exists(target):
                k += 1
                target = os.path.join(stops, "%s-%d.json" % (stamp, k))
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=1)
            where = os.path.relpath(target, ROOT) if target.startswith(ROOT) else target
        except OSError as exc:
            where = "(not saved: %s)" % type(exc).__name__
        low = {k.lower(): v for k, v in headers.items()}
        print("  HTTP %d from %s" % (status, path))
        print("    Retry-After:  %s" % low.get("retry-after", "(absent)"))
        print("    Content-Type: %s" % low.get("content-type", "(absent)"))
        print("    body[:300]:   %r" % body[:300])
        print("    saved to %s" % where)

    def get(self, path, params=None, sample=False):
        """GET base+path. Returns (json body, meta); meta["headers"] is the
        whitelisted response headers, and they are also written to the request
        log when `sample` is true. Any 4xx is saved by _dump_stop(); 429/403
        raise HarvestStop at once; 5xx and timeouts retry with exponential
        back-off; other statuses raise FetchError."""
        url = self.base + path
        last = "no attempt"
        for attempt in range(self.retries):
            self._gate()
            wall, t0 = self.wall(), self.clock()
            self.requests_made += 1
            try:
                resp = self.session.get(url, params=params,
                                        headers={"X-Api-Key": self.key},
                                        timeout=self.timeout, allow_redirects=False)
            except requests.RequestException as exc:
                # Only the exception type: the message can contain the URL.
                last = type(exc).__name__
                self._log(wall, path, params, "EXC:" + last, None, 0, None,
                          self.clock() - t0, attempt + 1)
                self.sleep(min(2 ** attempt, 60))
                continue
            seconds = self.clock() - t0
            try:
                remaining = int(resp.headers.get("X-RateLimit-Remaining"))
            except (TypeError, ValueError):
                remaining = None
            if remaining is not None:
                self.remaining = remaining
            status = resp.status_code
            headers = whitelist_headers(resp.headers, self.key)
            body = None
            if status == 200:
                try:
                    body = resp.json()
                except ValueError:
                    self._log(wall, path, params, status, None, len(resp.content),
                              remaining, seconds, attempt + 1, headers if sample else None)
                    raise FetchError("HTTP 200 with a body that is not JSON: %s" % path)
            n = len(body.get("results") or []) if isinstance(body, dict) and "results" in body else None
            self._log(wall, path, params, status, n, len(resp.content), remaining,
                      seconds, attempt + 1, headers if (sample or status != 200) else None)
            if 400 <= status < 500:
                self._dump_stop(path, params, resp, status, headers, attempt + 1)
                if status in (429, 403):
                    raise HarvestStop("HTTP %d from %s" % (status, path))
                raise FetchError("HTTP %d from %s" % (status, path))
            if status == 200:
                return body, {"seconds": seconds, "bytes": len(resp.content),
                              "remaining": remaining, "status": status,
                              "headers": headers}
            if 300 <= status < 400:
                raise FetchError("HTTP %d redirect from %s (not followed, so the key "
                                 "cannot leave the API host)" % (status, path))
            if status >= 500:
                last = "HTTP %d" % status
                self.sleep(min(2 ** attempt, 60))
                continue
            raise FetchError("HTTP %d from %s" % (status, path))
        raise FetchError("gave up on %s after %d attempts (%s)" % (path, self.retries, last))


# =============================================================================
# Harvest
# =============================================================================
SAFE_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def check_out_dir(out_dir):
    """Every output path is under data/us/ (the corpus's DATA_DIR)."""
    base = os.path.realpath(os.path.join(ROOT, config.DATA_DIR))
    real = os.path.realpath(out_dir)
    if real != base and not real.startswith(base + os.sep):
        raise SystemExit("refusing to write outside %s: %s" % (config.DATA_DIR, out_dir))
    return real


def paths(out_dir):
    return {"orgs": os.path.join(out_dir, "organizations.json"),
            "raw_dir": os.path.join(out_dir, "raw"),
            "done": os.path.join(out_dir, os.path.basename(config.HARVEST_DONE_FILE)),
            "sort": os.path.join(out_dir, "sort_choice.json"),
            "records_raw": os.path.join(out_dir, "records_raw.jsonl.gz"),
            "elements": os.path.join(out_dir, os.path.basename(config.RAW_FILE)),
            "report": os.path.join(out_dir, "harvest_report.json")}


def read_done(path):
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip()}


def fetch_organizations(client, out_dir):
    body, meta = client.get("/organizations", sample=True)
    orgs = body.get("organizations")
    if not isinstance(orgs, list):
        raise FetchError("/organizations returned no `organizations` list")
    with open(paths(out_dir)["orgs"], "w", encoding="utf-8") as fh:
        json.dump({"fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "seconds": round(meta["seconds"], 2), "bytes": meta["bytes"],
                   "status": meta["status"], "response": body},
                  fh, ensure_ascii=False, indent=1)
    return orgs


# ---- the sort check ---------------------------------------------------------
def _ms(iso):
    try:
        return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000
    except (TypeError, ValueError):
        return None


def sort_composition(results):
    """What each position of `_sort` is, judged against the record's own fields."""
    width = max((len(r.get("_sort") or []) for r in results), default=0)
    labels = []
    for j in range(width):
        votes = collections.Counter()
        for r in results:
            sk = r.get("_sort") or []
            if j >= len(sk):
                continue
            v = sk[j]
            if isinstance(v, str):
                votes["tie-break id"] += 1
            elif (isinstance(v, (int, float)) and not isinstance(v, bool)
                  and _ms(r.get("last_harvested_date")) is not None
                  and abs(v - _ms(r["last_harvested_date"])) < 1000):
                votes["last_harvested_date"] += 1
            elif isinstance(v, int) and not isinstance(v, bool) and v == r.get("popularity"):
                votes["popularity"] += 1
            elif isinstance(v, float):
                votes["score"] += 1
            else:
                votes["other"] += 1
        labels.append(votes.most_common(1)[0][0] if votes else "none")
    return labels


MUTABLE = {"popularity", "last_harvested_date"}


def choose_sort(client, orgs, out_dir, forced=None):
    """2 requests on one small organization (one per sort), then the rule: the
    first sort whose `_sort` has neither last_harvested_date nor popularity;
    otherwise last_harvested_date."""
    p = paths(out_dir)["sort"]
    if forced:
        return forced
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)["chosen"]
    small = sorted((o for o in orgs if 3 <= (o.get("dataset_count") or 0) <= 50),
                   key=lambda o: (o["dataset_count"], o["slug"]))
    if not small:
        raise FetchError("no organization with 3-50 datasets to check the sort on")
    slug = small[0]["slug"]
    comps = {}
    for sort in ("relevance", "last_harvested_date"):
        body, _ = client.get("/search", {"q": "", "org_slug": slug, "per_page": 5, "sort": sort})
        comps[sort] = sort_composition(body.get("results") or [])
    chosen = "last_harvested_date"
    for sort in ("relevance", "last_harvested_date"):
        if not MUTABLE & set(comps[sort]):
            chosen = sort
            break
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"chosen": chosen, "checked_on_organization": slug,
                   "per_page": 5, "_sort_composition": comps,
                   "rule": "first sort whose _sort has neither last_harvested_date "
                           "nor popularity; otherwise last_harvested_date"},
                  fh, indent=1)
    return chosen


# ---- one organization -------------------------------------------------------
# A page is one gzip member appended to <slug>.jsonl.gz.part and fsynced; then
# raw/<slug>.cursor.json is replaced atomically with where the walk stands.
# Concatenated members are a valid gzip stream, so a finished file reads like any
# other. A crash or a 429 therefore leaves a .part that ends on a page boundary
# (or on one truncated member, which is dropped), and the cursor to continue from.
def cursor_file(out_dir, slug):
    return os.path.join(paths(out_dir)["raw_dir"], slug + ".cursor.json")


def resumes_file(out_dir):
    return os.path.join(out_dir, "harvest_resumes.jsonl")


def decode_cursor(after):
    """The cursor is base64 of the JSON sort key of the last record on the page
    before it (probe A4). Only used to CHECK a cursor against the record it
    should follow, never to build one."""
    try:
        return json.loads(base64.urlsafe_b64decode(after + "=" * (-len(after) % 4)))
    except (ValueError, TypeError, AttributeError):
        return None


def write_cursor(path, next_after, pages, records, sort, wall):
    doc = {"next_after": next_after, "pages": pages, "records": records, "sort": sort,
           "updated": datetime.fromtimestamp(wall(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_cursor(path):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    keys = ("next_after", "pages", "records", "sort")
    if not isinstance(doc, dict) or any(k not in doc for k in keys):
        return None
    return doc


def scan_part(path):
    """Walk the gzip members of a .part without trusting its tail."""
    bounds = {0: (0, None)}
    size = os.path.getsize(path)
    cum = 0
    d = zlib.decompressobj(31)
    rows, carry, last = 0, b"", None
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 18)
            if not chunk:
                break
            data = chunk
            while data:
                try:
                    out = d.decompress(data)
                except zlib.error:
                    return {"bounds": bounds, "total": cum, "size": size, "clean": False}
                if out:
                    parts = (carry + out).split(b"\n")
                    carry = parts[-1]
                    if len(parts) > 1:
                        rows += len(parts) - 1
                        last = parts[-2]
                if not d.eof:
                    data = b""
                    break
                end = fh.tell() - len(d.unused_data)
                if carry:                              # a last line with no newline
                    return {"bounds": bounds, "total": cum, "size": size, "clean": False}
                cum += rows
                try:
                    last_sort = json.loads(last)["_sort"] if last else None
                except (ValueError, KeyError, TypeError):
                    last_sort = None
                bounds[cum] = (end, last_sort)
                data = d.unused_data
                d = zlib.decompressobj(31)
                rows, carry, last = 0, b"", None
    # an unfinished member at the end of the file is dropped
    end_of_last = bounds[cum][0]
    clean = (end_of_last == size)
    return {"bounds": bounds, "total": cum, "size": size, "clean": clean}


def _log_rows(out_dir, slug, sort):
    path = os.path.join(out_dir, "harvest_requests.jsonl")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            pr = r.get("params") or {}
            if r.get("path") == "/search" and pr.get("org_slug") == slug and pr.get("sort") == sort:
                rows.append(r)
    return rows


def _pages_for(rows, records):
    """How many pages the log says add up to `records` (informational)."""
    cum = pages = 0
    for r in rows:
        if "after" not in (r.get("params") or {}):
            cum = pages = 0                         # a walk restarted from page 1
        if r.get("status") != 200 or r.get("n") is None:
            continue
        cum += r["n"]
        pages += 1
        if cum == records:
            return pages
    return None


def prepare_resume(out_dir, slug, sort, force_log=False):
    """Decide how to continue an organization whose walk did not finish."""
    p = paths(out_dir)
    part = os.path.join(p["raw_dir"], slug + ".jsonl.gz.part")
    if not os.path.exists(part):
        return None, "no .part file"
    scan = scan_part(part)
    cursor = read_cursor(cursor_file(out_dir, slug))
    why = []

    def cut(end):
        if end < scan["size"]:
            os.truncate(part, end)
        return scan["size"] - end

    if cursor is not None and not force_log:
        rec = cursor["records"]
        if cursor["sort"] != sort:
            why.append("cursor.json was written for sort %r, not %r" % (cursor["sort"], sort))
        elif rec not in scan["bounds"]:
            why.append(".part has no page boundary at %d rows (cursor.json records)" % rec)
        else:
            end, last_sort = scan["bounds"][rec]
            after = cursor["next_after"]
            if rec == 0 or after is None or decode_cursor(after) == last_sort:
                dropped = cut(end)
                return {"after": after, "pages": cursor["pages"], "records": rec,
                        "how": "cursor.json", "dropped_bytes": dropped,
                        "finished": after is None and rec > 0}, ""
            why.append("cursor.json does not match the last row at its boundary")
    elif cursor is None:
        why.append("no usable cursor.json")

    # 2. the request log
    logged = {}
    rows = _log_rows(out_dir, slug, sort)
    for r in rows:
        after = (r.get("params") or {}).get("after")
        dec = decode_cursor(after) if after else None
        if dec is not None:
            logged[json.dumps(dec)] = after
    for rec in sorted((k for k in scan["bounds"] if k > 0), reverse=True):
        end, last_sort = scan["bounds"][rec]
        if last_sort is not None and json.dumps(last_sort) in logged:
            dropped = cut(end)
            pages = _pages_for(rows, rec)
            if pages is None:
                pages = -(-rec // cfg("CATALOG_PER_PAGE"))
            return {"after": logged[json.dumps(last_sort)], "pages": pages, "records": rec,
                    "how": "request log", "dropped_bytes": dropped, "finished": False}, ""
    why.append("no logged cursor matches the end of any complete page in the .part")
    return None, "; ".join(why)


def record_resume(out_dir, slug, state, wall):
    with open(resumes_file(out_dir), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "slug": slug, "at_page": state["pages"], "records": state["records"],
            "how": state["how"], "dropped_bytes": state.get("dropped_bytes", 0),
            "time": datetime.fromtimestamp(wall(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n")


def _finish_org(out_dir, slug, part, final, cursor):
    os.replace(part, final)
    with open(paths(out_dir)["done"], "a", encoding="utf-8") as done:
        done.write(slug + "\n")
    if os.path.exists(cursor):
        os.remove(cursor)


def fetch_org(client, slug, sort, out_dir, state=None, max_pages=None):
    """Walk one organization's cursor. Returns (records, pages, finished)."""
    if not SAFE_SLUG.match(slug):
        raise FetchError("unsafe organization slug %r" % slug)
    p = paths(out_dir)
    os.makedirs(p["raw_dir"], exist_ok=True)
    final = os.path.join(p["raw_dir"], slug + ".jsonl.gz")
    part = final + ".part"
    cur = cursor_file(out_dir, slug)
    if state is None:
        open(part, "wb").close()                       # no cursor is kept across a restart
        if os.path.exists(cur):
            os.remove(cur)
        after, pages, n = None, 0, 0
    else:
        after, pages, n = state["after"], state["pages"], state["records"]
        record_resume(out_dir, slug, state, client.wall)
        if state.get("finished"):
            _finish_org(out_dir, slug, part, final, cur)
            return n, pages, True
    cursors = {after} if after else set()
    this_call = 0
    fh = open(part, "ab")
    try:
        while True:
            params = {"q": "", "org_slug": slug, "per_page": cfg("CATALOG_PER_PAGE"),
                      "sort": sort}
            if after:
                params["after"] = after
            # Headers: the first request of this call, then every 50th page.
            sample = this_call == 0 or pages % HEADER_SAMPLE_EVERY == 0
            body, _ = client.get("/search", params, sample=sample)
            results = body.get("results") or []
            data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results)
            fh.write(gzip.compress(data.encode("utf-8"), compresslevel=6, mtime=0))
            fh.flush()
            os.fsync(fh.fileno())
            n += len(results)
            pages += 1
            this_call += 1
            after = body.get("after")
            finished = not after or not results
            write_cursor(cur, None if finished else after, pages, n, sort, client.wall)
            if finished:
                break
            if after in cursors:
                raise FetchError("cursor repeated for %s" % slug)
            cursors.add(after)
            if max_pages is not None and this_call >= max_pages:
                return n, pages, False
    finally:
        fh.close()
    _finish_org(out_dir, slug, part, final, cur)
    return n, pages, True


def harvest(client, orgs, slugs, sort, out_dir, resume, log=print, max_pages=None):
    """Walk the organizations in slug order. Returns the slugs now complete;
    stops early when max_pages (a total over organizations) is used up."""
    p = paths(out_dir)
    done = read_done(p["done"]) if resume else set()
    count = {o["slug"]: o.get("dataset_count") for o in orgs}
    budget = max_pages
    for i, slug in enumerate(slugs, 1):
        if slug in done:
            log("[%d/%d] %s: already complete, skipped" % (i, len(slugs), slug))
            continue
        if budget is not None and budget <= 0:
            break
        state = None
        if resume:
            state, why = prepare_resume(out_dir, slug, sort)
            if state is not None:
                log("[%d/%d] %s: continuing after page %d (%d records) from %s%s"
                    % (i, len(slugs), slug, state["pages"], state["records"], state["how"],
                       ", dropped %d bytes of unfinished tail" % state["dropped_bytes"]
                       if state["dropped_bytes"] else ""))
            elif why != "no .part file":
                log("[%d/%d] %s: starting from page 1 again: %s" % (i, len(slugs), slug, why))
        n, pages, finished = fetch_org(client, slug, sort, out_dir, state, max_pages=budget)
        if budget is not None:
            budget -= max(0, pages - (state["pages"] if state else 0))
        if not finished:
            log("[%d/%d] %s: stopped after %d records, %d page(s) (--max-pages); dataset_count %s"
                % (i, len(slugs), slug, n, pages, count.get(slug)))
            break
        done.add(slug)
        log("[%d/%d] %s: %d records in %d page(s), dataset_count %s (remaining quota %s)"
            % (i, len(slugs), slug, n, pages, count.get(slug), client.remaining))
    return done


# =============================================================================
# Merge
# =============================================================================
def _open_gz_deterministic(path):
    """gzip with no filename and mtime 0, so two merges are byte-identical."""
    raw = open(path, "wb")
    gz = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)
    return io.TextIOWrapper(gz, encoding="utf-8", newline="\n"), gz, raw


def merge(out_dir, slugs=None, log=print):
    """raw/*.jsonl.gz -> records_raw.jsonl.gz, elements_all.jsonl, harvest_report.json.
    Order: organizations by slug, records in cursor order; first occurrence of an
    id wins."""
    p = paths(out_dir)
    done = read_done(p["done"])
    wanted = sorted(slugs) if slugs is not None else sorted(done)
    present = [s for s in wanted if s in done and
               os.path.exists(os.path.join(p["raw_dir"], s + ".jsonl.gz"))]
    missing = [s for s in wanted if s not in present]
    org_counts, expected_all = {}, None
    if os.path.exists(p["orgs"]):
        with open(p["orgs"], encoding="utf-8") as fh:
            orgs = json.load(fh)["response"]["organizations"]
        org_counts = {o["slug"]: o.get("dataset_count") for o in orgs}
        expected_all = sum(o.get("dataset_count") or 0 for o in orgs)

    resumed = {}                     # B5: organizations whose walk was continued
    if os.path.exists(resumes_file(out_dir)):
        with open(resumes_file(out_dir), encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                e = resumed.setdefault(ev["slug"], {"count": 0, "at_pages": []})
                e["count"] += 1
                e["at_pages"].append(ev["at_page"])

    fp = FootprintStats()
    counts = collections.Counter()
    first_org = {}                   # id -> index of the organization that wrote it
    ident_seen = set()
    types, formats = collections.Counter(), collections.Counter()
    per_org = {}
    written = 0
    tmp_el, tmp_raw = p["elements"] + ".tmp", p["records_raw"] + ".tmp"
    el_fh = open(tmp_el, "w", encoding="utf-8")
    raw_text, raw_gz, raw_file = _open_gz_deterministic(tmp_raw)
    try:
        for idx, slug in enumerate(present):
            lines = uniq = 0
            with gzip.open(os.path.join(p["raw_dir"], slug + ".jsonl.gz"), "rt",
                           encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    lines += 1
                    rec = json.loads(line)
                    slug_v = rec.get("slug")
                    if not isinstance(slug_v, str) or not slug_v.strip():
                        counts["rejected: no slug"] += 1
                        continue
                    eid = element_id(slug_v.strip())
                    if eid in first_org:
                        counts["duplicate id, other organization"
                               if first_org[eid] != idx else
                               "duplicate id, same organization"] += 1
                        continue
                    try:
                        el = to_element(rec, fp, counts)
                    except Rejected as exc:
                        counts["rejected: %s" % exc] += 1
                        continue
                    first_org[eid] = idx
                    uniq += 1
                    written += 1
                    if isinstance(rec.get("identifier"), str):
                        if rec["identifier"] in ident_seen:
                            counts["repeated `identifier` among kept records"] += 1
                        ident_seen.add(rec["identifier"])
                    types[el["type"]] += 1
                    formats.update(el["formats"])
                    if not el["created"]:
                        counts["created empty"] += 1
                    if not el["modified"]:
                        counts["modified empty"] += 1
                    el_fh.write(json.dumps(el, ensure_ascii=False) + "\n")
                    raw_text.write(json.dumps(rec, ensure_ascii=False) + "\n")
            per_org[slug] = {"raw_records": lines, "kept": uniq,
                             "resumed": resumed.get(slug),
                             "dataset_count": org_counts.get(slug),
                             "raw_minus_dataset_count":
                                 (lines - org_counts[slug]) if org_counts.get(slug) is not None else None}
    except BaseException:
        # No half-written merge is left behind under the final names.
        el_fh.close()
        raw_text.close()
        raw_file.close()
        for path in (tmp_el, tmp_raw):
            if os.path.exists(path):
                os.remove(path)
        raise
    el_fh.close()
    raw_text.close()                  # flushes and closes the GzipFile, not the file
    raw_gz.close()
    raw_file.close()
    os.replace(tmp_el, p["elements"])
    os.replace(tmp_raw, p["records_raw"])

    # id[:8] collisions among the kept elements
    prefixes = collections.Counter(i[:8] for i in first_org)
    colliding = {k: v for k, v in prefixes.items() if v > 1}
    expected_merged = sum(v["dataset_count"] or 0 for v in per_org.values())
    report = {
        "organizations_merged": len(present),
        "organizations_not_merged": missing,
        "organizations_resumed": {s: resumed[s] for s in present if s in resumed},
        "raw_records": sum(v["raw_records"] for v in per_org.values()),
        "elements_written": written,
        "dataset_count_sum_merged_organizations": expected_merged,
        "dataset_count_sum_all_organizations": expected_all,
        "elements_minus_dataset_count_merged": written - expected_merged,
        "elements_minus_dataset_count_all": (written - expected_all) if expected_all is not None else None,
        "duplicate_ids_other_organization": counts["duplicate id, other organization"],
        "duplicate_ids_same_organization": counts["duplicate id, same organization"],
        "duplicate_slugs": (counts["duplicate id, other organization"]
                            + counts["duplicate id, same organization"]),
        "id_prefix8_colliding_prefixes": len(colliding),
        "id_prefix8_elements_in_a_collision": sum(colliding.values()),
        "footprints": fp.as_dict(),
        "elements_with_geometry": sum(fp.accepted.values()),
        "created_empty": counts["created empty"],
        "modified_empty": counts["modified empty"],
        "organization_missing": counts["organization missing"],
        "rejected_no_slug": counts["rejected: no slug"],
        "repeated_identifier_among_kept": counts["repeated `identifier` among kept records"],
        "type_distribution": dict(types.most_common()),
        "formats_top30": formats.most_common(30),
        "per_organization": per_org,
    }
    with open(p["report"], "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, sort_keys=True)
    print_summary(report, log)
    return report


def print_summary(r, log=print):
    log("")
    log("merged %d organization(s): %d raw records -> %d elements"
        % (r["organizations_merged"], r["raw_records"], r["elements_written"]))
    if r["organizations_not_merged"]:
        log("  NOT merged (not complete): %s" % ", ".join(r["organizations_not_merged"]))
    log("  dataset_count sum (merged organizations): %s; elements minus it: %+d"
        % (r["dataset_count_sum_merged_organizations"], r["elements_minus_dataset_count_merged"]))
    if r["dataset_count_sum_all_organizations"] is not None:
        log("  dataset_count sum (all organizations): %d; elements minus it: %+d"
            % (r["dataset_count_sum_all_organizations"], r["elements_minus_dataset_count_all"]))
    for s, v in r.get("organizations_resumed", {}).items():
        log("  resumed: %s, %d time(s), continued after page(s) %s"
            % (s, v["count"], ", ".join(str(x) for x in v["at_pages"])))
    off = [(s, v) for s, v in r["per_organization"].items()
           if v["raw_minus_dataset_count"]]
    log("  organizations whose raw count differs from dataset_count: %d of %d"
        % (len(off), len(r["per_organization"])))
    for s, v in sorted(off, key=lambda kv: -abs(kv[1]["raw_minus_dataset_count"]))[:15]:
        log("     %-30s raw %7d  dataset_count %7s  diff %+d"
            % (s, v["raw_records"], v["dataset_count"], v["raw_minus_dataset_count"]))
    log("  duplicate ids dropped: %d other organization, %d same organization (= duplicate slugs %d)"
        % (r["duplicate_ids_other_organization"], r["duplicate_ids_same_organization"],
           r["duplicate_slugs"]))
    log("  id[:8] collisions: %d prefixes covering %d elements"
        % (r["id_prefix8_colliding_prefixes"], r["id_prefix8_elements_in_a_collision"]))
    f = r["footprints"]
    log("  footprints: %d elements with geometry (%d global extent); %d records had no spatial field"
        % (r["elements_with_geometry"], f["global_extent_kept"], f["records_without_any_candidate"]))
    for k, v in f["accepted_by_source"].items():
        log("     accepted  %-24s %d" % (k, v))
    for k, v in f["rejected_by_source_and_reason"].items():
        log("     rejected  %-52s %d" % (k, v))
    log("  created empty %d, modified empty %d, organization missing %d, rejected (no slug) %d"
        % (r["created_empty"], r["modified_empty"], r["organization_missing"], r["rejected_no_slug"]))
    log("  types: %s" % ", ".join("%s %d" % kv for kv in r["type_distribution"].items()))
    log("  formats (top 30): %s" % ", ".join("%s %d" % tuple(kv) for kv in r["formats_top30"]))


# =============================================================================
# Entry point
# =============================================================================
def resume_command(args):
    cmd = "CORPUS=us python ingest/fetch_catalog_api.py --resume"
    if args.out_dir != config.DATA_DIR:
        cmd += " --out-dir %s" % args.out_dir
    if args.slugs:
        cmd += " --slugs %s" % args.slugs
    if args.min_interval != 1.0:
        cmd += " --min-interval %g" % args.min_interval
    return cmd


def ping(client, out_dir):
    """One GET /organizations, nothing harvested and organizations.json left as
    it is: the status, seconds, bytes and every whitelisted response header go
    to probes/<UTC time>.json and are printed."""
    body, meta = client.get("/organizations", sample=True)
    stamp = datetime.fromtimestamp(client.wall(), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    os.makedirs(os.path.join(out_dir, "probes"), exist_ok=True)
    target = os.path.join(out_dir, "probes", stamp + ".json")
    doc = {"time": stamp, "path": "/organizations", "status": meta["status"],
           "seconds": round(meta["seconds"], 2), "bytes": meta["bytes"],
           "headers": meta["headers"],
           "organizations": len(body.get("organizations") or []), "total": body.get("total")}
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    print("HTTP %s, %.2f s, %d bytes, %s organizations" % (
        meta["status"], meta["seconds"], meta["bytes"], doc["organizations"]))
    for name, value in meta["headers"].items():
        print("  %s: %s" % (name, value))
    print("saved to %s" % os.path.relpath(target, ROOT))
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resume", action="store_true",
                    help="skip organizations already complete; continue an incomplete one "
                         "after its last saved page (raw/<slug>.cursor.json), or from page 1 "
                         "if that cannot be aligned")
    ap.add_argument("--fresh", action="store_true",
                    help="discard an existing harvest in --out-dir and start over")
    ap.add_argument("--merge", action="store_true",
                    help="only merge the raw files already harvested (no network)")
    ap.add_argument("--out-dir", default=config.DATA_DIR,
                    help="must be inside %s (default: %s)" % (config.DATA_DIR, config.DATA_DIR))
    ap.add_argument("--slugs", default="",
                    help="comma-separated organization slugs to harvest (default: all)")
    ap.add_argument("--sort", default="auto",
                    choices=("auto", "relevance", "last_harvested_date"),
                    help="auto = sort_choice.json, or a 2-request check on a small organization")
    ap.add_argument("--min-interval", type=float, default=1.0,
                    help="seconds between request starts, at least 1 (default 1)")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="stop after this many pages in total, leaving a resumable state")
    ap.add_argument("--reuse-organizations", action="store_true",
                    help="read organizations.json instead of requesting /organizations")
    ap.add_argument("--rebuild-cursor", metavar="SLUG", default="",
                    help="no network: write raw/<slug>.cursor.json for an existing .part from "
                         "the request log (cutting an unfinished tail if there is one)")
    ap.add_argument("--ping", action="store_true",
                    help="one GET /organizations; save and print its whitelisted headers")
    args = ap.parse_args(argv)
    if args.min_interval < 1.0:
        ap.error("--min-interval must be at least 1 (the rule is >= 1 s between requests)")

    if config.CORPUS != "us":
        raise SystemExit("this fetcher is for CORPUS=us (CORPUS=%s)" % config.CORPUS)
    out_dir = check_out_dir(os.path.join(ROOT, args.out_dir)
                            if not os.path.isabs(args.out_dir) else args.out_dir)
    slugs_arg = [s for s in args.slugs.split(",") if s] or None
    key = os.environ.get("DATAGOV_API_KEY")
    offline = args.merge or bool(args.rebuild_cursor)
    if not offline and not key:
        raise SystemExit("DATAGOV_API_KEY is not set; stopping.")
    os.makedirs(out_dir, exist_ok=True)
    p = paths(out_dir)

    if args.merge:
        merge(out_dir, slugs_arg)
        return 0

    if args.rebuild_cursor:
        slug = args.rebuild_cursor
        if args.sort != "auto":
            sort = args.sort
        elif os.path.exists(p["sort"]):
            with open(p["sort"], encoding="utf-8") as fh:
                sort = json.load(fh)["chosen"]
        else:
            raise SystemExit("no sort_choice.json; pass --sort")
        state, why = prepare_resume(out_dir, slug, sort, force_log=True)
        if state is None:
            print("cannot align %s: %s" % (slug, why))
            return 1
        write_cursor(cursor_file(out_dir, slug), state["after"], state["pages"],
                     state["records"], sort, time.time)
        print("%s: cursor.json written from the %s: after page %d, %d records, %d bytes of "
              "unfinished tail cut" % (slug, state["how"], state["pages"], state["records"],
                                       state["dropped_bytes"]))
        return 0

    if args.ping:
        client = Client(key, out_dir, min_interval=args.min_interval)
        try:
            ping(client, out_dir)
        except (HarvestStop, FetchError) as exc:
            print("ping failed: %s" % exc)
            return 3
        return 0

    if args.fresh:
        for f in (p["done"], p["sort"], resumes_file(out_dir)):
            if os.path.exists(f):
                os.remove(f)
        if os.path.isdir(p["raw_dir"]):
            for name in os.listdir(p["raw_dir"]):
                os.remove(os.path.join(p["raw_dir"], name))
    elif not args.resume and read_done(p["done"]):
        raise SystemExit("%s already holds a harvest (%d organizations done). Use --resume to "
                         "continue it or --fresh to discard it." % (out_dir, len(read_done(p["done"]))))

    client = Client(key, out_dir, min_interval=args.min_interval)
    try:
        if args.reuse_organizations:
            with open(p["orgs"], encoding="utf-8") as fh:
                orgs = json.load(fh)["response"]["organizations"]
        else:
            orgs = fetch_organizations(client, out_dir)
        all_slugs = sorted(o["slug"] for o in orgs if o.get("slug"))
        if slugs_arg:
            unknown = [s for s in slugs_arg if s not in all_slugs]
            if unknown:
                raise SystemExit("unknown organization slug(s): %s" % ", ".join(unknown))
            slugs = sorted(slugs_arg)
        else:
            slugs = all_slugs
        sort = choose_sort(client, orgs, out_dir,
                           None if args.sort == "auto" else args.sort)
        print("organizations: %d (walking %d); sort=%s; min interval %g s"
              % (len(orgs), len(slugs), sort, client.min_interval))
        done = harvest(client, orgs, slugs, sort, out_dir, args.resume,
                       max_pages=args.max_pages)
    except HarvestStop as exc:
        print("\nSTOPPED: %s after %d request(s). Nothing was retried." % (exc, client.requests_made))
        print("Every page already fetched is saved (raw/<slug>.jsonl.gz.part and "
              "raw/<slug>.cursor.json); the refusal is saved under stops/.")
        print("Resume with:\n    %s" % resume_command(args))
        return 3
    except FetchError as exc:
        print("\nFAILED: %s (%d request(s) made)" % (exc, client.requests_made))
        print("Resume with:\n    %s" % resume_command(args))
        return 2

    print("\n%d request(s) made" % client.requests_made)
    if all(s in done for s in slugs):
        merge(out_dir, slugs)
    else:
        print("not merged: %d of %d organizations complete" % (sum(s in done for s in slugs), len(slugs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
