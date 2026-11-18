"""Harvest a CKAN catalogue into the corpus's raw file.

Usage
-----
    python ingest/fetch_ckan.py                  # full harvest, sharded by organisation
    python ingest/fetch_ckan.py --resume         # skip organisations already done
    python ingest/fetch_ckan.py --limit-orgs 5   # smoke test
    python ingest/fetch_ckan.py --topics         # old behaviour: 6 topic queries
    python ingest/fetch_ckan.py --save-raw       # also keep the CKAN packages

For a corpus other than au, select it with CORPUS and keep the raw packages,
because to_element() discards most of the CKAN record and re-deriving any of
it costs a second full harvest:

    CORPUS=uk python ingest/fetch_ckan.py --save-raw
"""
import argparse
import gzip
import json
import math
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta

import requests
from shapely import wkt
from shapely.geometry import shape

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

SEARCH = "/action/package_search"
ORG_LIST = "/action/organization_list"
PACKAGE_LIST = "/action/current_package_list_with_resources"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "interads-harvester/0.3"})

# Topic mode (--topics): harvest by search terms instead of by organisation.
TOPICS = config.HARVEST_TOPICS
TOPIC_ROWS = 100

RETRY_STATUS = {409, 429, 500, 502, 503, 504}

_last_call = [0.0]
_fail_streak = [0]


class QueryRejected(Exception):
    """CKAN said the parameters are invalid. Retrying cannot help."""


class BackendUnavailable(Exception):
    """CKAN's search backend is erroring. Retrying later might help."""


# ---- HTTP -----------------------------------------------------------------
def _throttle():
    """One global minimum interval between calls."""
    gap = time.time() - _last_call[0]
    wait = config.HARVEST_MIN_INTERVAL - gap
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def _error_type(resp):
    try:
        return str((resp.json().get("error") or {}).get("__type") or "")
    except Exception:
        return ""


def _error_message(resp):
    try:
        return str((resp.json().get("error") or {}).get("message") or "")[:300]
    except Exception:
        return resp.text[:200]


def api_get(path, params):
    """GET a CKAN action endpoint, distinguishing the two failure kinds."""
    url = config.CKAN_API + path
    last = None
    for attempt in range(config.HARVEST_RETRIES):
        if _fail_streak[0] >= config.HARVEST_FAIL_STREAK:
            print("    %d failures in a row, pausing %ds to let the portal "
                  "recover" % (_fail_streak[0], config.HARVEST_LONG_COOLDOWN))
            time.sleep(config.HARVEST_LONG_COOLDOWN)
            _fail_streak[0] = 0

        _throttle()
        try:
            resp = SESSION.get(url, params=params, timeout=config.HARVEST_TIMEOUT)
        except requests.RequestException as exc:
            last = exc
            _fail_streak[0] += 1
            wait = min(2 ** attempt, 60)
            print("    [network retry %d/%d] %s (sleep %ds)"
                  % (attempt + 1, config.HARVEST_RETRIES, exc, wait))
            time.sleep(wait)
            continue

        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                _fail_streak[0] = 0
                return data
            last = RuntimeError("success=false: %s" % _error_message(resp))
            _fail_streak[0] += 1
            time.sleep(min(2 ** attempt, 60))
            continue

        etype = _error_type(resp)
        if resp.status_code == 409 and "valid" in etype.lower():
            # A genuine parameter rejection. Surface it instead of hammering.
            raise QueryRejected("%s: %s" % (etype, _error_message(resp)))

        if resp.status_code in RETRY_STATUS:
            _fail_streak[0] += 1
            wait = config.HARVEST_COOLDOWN * (2 ** attempt)
            print("    [backend retry %d/%d] HTTP %d %s -- sleeping %ds"
                  % (attempt + 1, config.HARVEST_RETRIES, resp.status_code,
                     etype or "", wait))
            print("      %s" % _error_message(resp)[:200])
            time.sleep(wait)
            last = BackendUnavailable("HTTP %d %s" % (resp.status_code, etype))
            continue

        resp.raise_for_status()

    raise BackendUnavailable("gave up on %s after %d attempts: %s"
                             % (path, config.HARVEST_RETRIES, last))


def preflight():
    """Refuse to start a long harvest against a search backend that is down."""
    try:
        total, _ = search(start=0, rows=0)
    except QueryRejected as exc:
        raise SystemExit("the API rejected a trivial query: %s" % exc)
    except Exception as exc:                # noqa: BLE001
        raise SystemExit(
            "package_search is not answering (%s).\n"
            "If the error says 'Search Error' the portal's Solr is down, not "
            "your code. Wait and retry, or use --via-list, which reads the "
            "database instead of the search index." % exc)
    print("preflight: package_search is healthy, %d packages visible" % total)
    return total


def search(fq="", start=0, rows=None, q="*:*"):
    rows = config.HARVEST_ROWS if rows is None else rows
    params = {"q": q, "rows": rows, "start": start}
    if fq:
        params["fq"] = fq
    if rows:
        # stable ordering, otherwise pagination can skip or repeat records
        params["sort"] = "metadata_created asc"
    result = api_get(SEARCH, params)["result"]
    return result["count"], result.get("results", [])


# ---- Type inference -------------------------------------------------------
# Rewritten because the first version over-produced "map": it tested
# `"map" in title`, so "Flood Mapping Guidelines" became a map. Resource
# formats are objective evidence; the title is only a fallback, and now it is
# matched on word boundaries so "mapping" no longer counts as "map".
# Rewritten twice. The first version tested `"map" in title`, so "Flood Mapping
# Guidelines" became a map. The second version trusted resource formats, which
# was worse in a different way: WMS and WFS are *delivery mechanisms*, not
# artifact types, and AODN attaches a WMS layer to nearly every record it
# publishes. That put oceanographic measurement series ("Coffin Bay CTD Data",
# "IMOS Ships of Opportunity Underway Data") into the map class, systematically,
# across the second largest publisher in the catalogue.
#
# So service endpoints are now neutral evidence. A record is a map when its
# title says so, or when its files are genuinely map or imagery artifacts.
DOC_FORMATS = {"pdf", "doc", "docx", "rtf", "odt"}
MAP_FORMATS = {"kml", "kmz", "ecw", "mrsid", "mapinfo", "tab", "map"}
SERVICE_FORMATS = {  # neutral: says how you fetch it, not what it is
    "wms", "wmts", "wfs", "wcs", "arcgis", "arcgis rest", "esri rest",
    "esri rest service", "ogc api", "georss",
}
NOTEBOOK_FORMATS = {"ipynb"}
SOFTWARE_FORMATS = {"api", "rest", "sparql", "graphql", "soap", "wsdl"}

RE_NOTEBOOK = re.compile(r"\b(notebook|notebooks|jupyter|ipynb)\b", re.I)
RE_SOFTWARE = re.compile(
    r"\b(api|apis|sdk|toolkit|source code|codebase|software|library|plugin|"
    r"web service|web services|widget|endpoint)\b", re.I)
RE_PUBLICATION = re.compile(
    r"\b(report|reports|guideline|guidelines|strategy|review|assessment|"
    r"study|studies|paper|papers|manual|handbook|bulletin|statement|"
    r"factsheet|fact sheet|brochure|newsletter|proceedings|thesis)\b", re.I)
RE_MAP = re.compile(
    r"\b(map|maps|atlas|basemap|imagery|orthophoto|topographic|chart|charts)\b",
    re.I)


def norm_format(resource):
    return (resource.get("format") or "").strip().lower().lstrip(".")


def classify(title, formats):
    """Assign a dataset type from the title and the resource formats."""
    formats = {f for f in formats if f}
    real = formats - SERVICE_FORMATS   # what the record actually consists of
    map_title = RE_MAP.search(title)
    pub_title = RE_PUBLICATION.search(title)

    if formats & NOTEBOOK_FORMATS or RE_NOTEBOOK.search(title):
        return "notebook"
    if formats & SOFTWARE_FORMATS or RE_SOFTWARE.search(title):
        return "software"
    # A scanned map sheet ships as a PDF and is still a map, so a clear map
    # title outranks the documents-only rule below -- unless the title also
    # reads like a document, as in "Flood Mapping Guidelines".
    if map_title and not pub_title:
        return "map"
    if real and real <= DOC_FORMATS:
        return "publication"
    if real & DOC_FORMATS and pub_title:
        return "publication"
    if real & MAP_FORMATS or map_title:
        return "map"
    return "dataset"


def package_formats(pkg):
    return sorted({f for f in (norm_format(r) for r in pkg.get("resources") or [])
                   if f})


def guess_type(pkg):
    """Thin wrapper over classify() for a raw CKAN package."""
    return classify(pkg.get("title") or "", package_formats(pkg))


# ---- Geometry -------------------------------------------------------------
def _shape_from_obj(obj):
    """GeoJSON dict -> shapely. Handles Feature and FeatureCollection too."""
    if not isinstance(obj, dict):
        return None
    if obj.get("type") == "Feature":
        return _shape_from_obj(obj.get("geometry"))
    if obj.get("type") == "FeatureCollection":
        geoms = [_shape_from_obj(f) for f in obj.get("features") or []]
        geoms = [g for g in geoms if g is not None and not g.is_empty]
        if not geoms:
            return None
        best = max(geoms, key=lambda g: g.area)
        return best
    try:
        return shape(obj)
    except Exception:
        return None


def parse_geom(raw):
    """Parse one spatial value: GeoJSON string, GeoJSON dict, WKT, or prose."""
    if not raw:
        return None
    if isinstance(raw, dict):
        return _shape_from_obj(raw)
    if not isinstance(raw, str):
        return None
    try:
        return _shape_from_obj(json.loads(raw))
    except Exception:
        pass
    try:
        return wkt.loads(raw)
    except Exception:
        return None


# Why a footprint was rejected, counted over the whole harvest and printed at
# the end.
GEOMETRY_REJECTS = Counter()


def _extras_map(pkg):
    """CKAN's extras list-of-dicts as a plain {key: value} mapping."""
    out = {}
    for item in pkg.get("extras") or []:
        if isinstance(item, dict) and "key" in item:
            out[item["key"]] = item.get("value")
    return out


def spatial_values(pkg):
    """Every raw footprint string this corpus says the package may carry."""
    values = [pkg.get(key) for key in config.CKAN_SPATIAL_KEYS]
    extras_keys = getattr(config, "CKAN_SPATIAL_EXTRAS_KEYS", ()) or ()
    if extras_keys:
        extras = _extras_map(pkg)
        values.extend(extras.get(key) for key in extras_keys)
    return values


def get_geometry(pkg):
    """Read the spatial footprint and store it as a bounding box (envelope)."""
    raw = spatial_values(pkg)
    if not any(v not in (None, "", [], {}) for v in raw):
        GEOMETRY_REJECTS["no spatial field on the package"] += 1
        return None

    candidates = []
    for value in raw:
        if value in (None, "", [], {}):
            continue
        geom = parse_geom(value)
        if geom is None:
            # Unparsable as GeoJSON and as WKT. On data.gov.uk this is the
            # dominant case: the coordinate slots are written out empty.
            GEOMETRY_REJECTS["unparsable (empty or malformed coordinates)"] += 1
            continue
        if geom.is_empty:
            GEOMETRY_REJECTS["parsed but empty geometry"] += 1
            continue
        candidates.append(geom)
    if not candidates:
        return None

    candidates.sort(key=lambda g: g.area, reverse=True)
    best = candidates[0]
    if best.area == 0:
        # Points and lines. Too coarse to filter a bbox query on, so they are
        # dropped rather than stored as a zero-width envelope.
        GEOMETRY_REJECTS["zero area (point or line)"] += 1
        return None

    bounds = best.bounds
    # NaN or infinity that shapely accepted.
    if not all(math.isfinite(b) for b in bounds):
        GEOMETRY_REJECTS["non-numeric coordinates"] += 1
        return None
    min_lon, min_lat, max_lon, max_lat = bounds
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        GEOMETRY_REJECTS["longitude out of range"] += 1
        return None
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        GEOMETRY_REJECTS["latitude out of range"] += 1
        return None

    return {
        "type": "envelope",
        "coordinates": [[min_lon, max_lat], [max_lon, min_lat]],
    }


def print_geometry_rejects():
    """One block at the end of a harvest saying why footprints were dropped."""
    total = sum(GEOMETRY_REJECTS.values())
    if not total:
        return
    print("\nfootprints not stored, by reason (%d packages):" % total)
    for reason, count in GEOMETRY_REJECTS.most_common():
        print("   %-48s %8d" % (reason, count))


# ---- Normalisation --------------------------------------------------------
def dataset_url(pkg):
    """The user-facing page for one package."""
    template = getattr(config, "DATASET_URL_TEMPLATE", None)
    if template:
        return template.format(id=pkg.get("id") or "", name=pkg.get("name") or "")
    return config.DATASET_URL_PREFIX + (pkg.get("name") or "")


def to_element(pkg):
    org = pkg.get("organization") or {}
    formats = package_formats(pkg)
    return {
        "id": pkg["id"],
        "title": pkg.get("title") or "",
        "description": pkg.get("notes") or "",
        "type": classify(pkg.get("title") or "", formats),
        # Kept so the type rules can be revised over an existing corpus instead
        # of re-harvesting: the first version stored only the derived type, so
        # every rule change cost a full re-download.
        "formats": formats,
        "tags": [t["name"] for t in pkg.get("tags") or [] if t.get("name")],
        "organization": org.get("title") or "",
        "url": dataset_url(pkg),
        "geometry": get_geometry(pkg),
        "created": (pkg.get("metadata_created") or "")[:19] or None,
        "modified": (pkg.get("metadata_modified") or "")[:19] or None,
    }


# Default location of the raw sidecar, relative to the corpus's DATA_DIR.
RAW_PACKAGES_FILE = "packages_raw.jsonl.gz"


class Sink:
    """Append-only JSONL writer that drops duplicate ids."""

    def __init__(self, path, seen=None, raw_path=None):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.handle = open(path, "a")
        self.seen = seen if seen is not None else set()
        self.written = 0
        self.with_geo = 0
        self.raw_path = raw_path
        self.raw_written = 0
        self.raw_handle = None
        if raw_path:
            os.makedirs(os.path.dirname(raw_path) or ".", exist_ok=True)
            # Append mode, gzip: one member per open() rather than one per
            # record, which every gzip reader concatenates transparently.
            self.raw_handle = gzip.open(raw_path, "at", encoding="utf-8")

    def add(self, pkg):
        if not pkg.get("id") or pkg["id"] in self.seen:
            return False
        self.seen.add(pkg["id"])
        try:
            element = to_element(pkg)
        except Exception as exc:            # noqa: BLE001
            print("    [skip] %s: %s" % (pkg.get("id"), exc))
            return False
        self.handle.write(json.dumps(element, ensure_ascii=False) + "\n")
        self.written += 1
        if element["geometry"]:
            self.with_geo += 1
        if self.raw_handle is not None:
            # Written after to_element succeeded, so the two files hold the
            # same set of ids and can be joined on `id` without a filter.
            self.raw_handle.write(json.dumps(pkg, ensure_ascii=False) + "\n")
            self.raw_written += 1
        return True

    def close(self):
        self.handle.close()
        if self.raw_handle is not None:
            self.raw_handle.close()


# ---- Harvest strategies ---------------------------------------------------
def page_through(fq, sink, label):
    """Page a single filter query. Returns the reported total."""
    total, first = search(fq=fq, start=0)
    if total == 0:
        return 0
    fetched = 0
    rows = first
    while rows:
        for pkg in rows:
            sink.add(pkg)
        fetched += len(rows)
        if fetched >= total or fetched >= config.DEEP_PAGE_LIMIT:
            break
        time.sleep(config.HARVEST_SLEEP)
        _, rows = search(fq=fq, start=fetched)
    print("    %s: %d/%d fetched (corpus now %d)"
          % (label, fetched, total, sink.written))
    return total


def list_organisations():
    """Every organisation name with its package count and display name."""
    params = {
        "q": "*:*", "rows": 0, "facet": "true",
        "facet.field": json.dumps(["organization"]),
        "facet.limit": -1,
    }
    result = api_get(SEARCH, params)["result"]
    items = ((result.get("search_facets") or {})
             .get("organization", {})
             .get("items") or [])
    triples = [(i["name"], i.get("count") or 0,
                i.get("display_name") or i["name"])
               for i in items if i.get("name")]
    if triples:
        return triples

    print("  facet query returned nothing, falling back to organization_list")
    names = []
    offset = 0
    while True:
        batch = api_get(ORG_LIST, {"limit": 1000, "offset": offset})["result"]
        if not batch:
            break
        for entry in batch:
            names.append(entry if isinstance(entry, str) else entry.get("name"))
        if len(batch) < 1000:
            break
        offset += len(batch)
    return [(n, 0, n) for n in names if n]


def count_for(fq):
    total, _ = search(fq=fq, start=0, rows=0)
    return total


DATE_FMT = "%Y-%m-%dT%H:%M:%SZ"


def window_fq(lo, hi):
    """Half-open Solr range: inclusive lower bound, exclusive upper bound, so
    adjacent windows never double-count a record on the boundary."""
    return "+metadata_created:[%s TO %s}" % (lo.strftime(DATE_FMT),
                                             hi.strftime(DATE_FMT))


def harvest_window(base_fq, lo, hi, sink, label, depth=0):
    """Page a time window, bisecting it whenever it exceeds the paging limit."""
    fq = (base_fq + " " + window_fq(lo, hi)).strip()
    total = count_for(fq)
    if total == 0:
        return
    if total <= config.DEEP_PAGE_LIMIT:
        page_through(fq, sink, "%s [%s .. %s]"
                     % (label, lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")))
        return

    span = (hi - lo).total_seconds()
    if span <= 1 or depth >= 40:
        # Pathological: thousands of records share one timestamp. Page what we
        # can and say so loudly rather than reporting a clean finish.
        print("    WARNING: %d packages inside a %.0fs window for %s; cannot "
              "shard further, keeping the first %d"
              % (total, span, label, config.DEEP_PAGE_LIMIT))
        page_through(fq, sink, label)
        return

    mid = lo + timedelta(seconds=span / 2.0)
    time.sleep(config.HARVEST_SLEEP)
    harvest_window(base_fq, lo, mid, sink, label, depth + 1)
    harvest_window(base_fq, mid, hi, sink, label, depth + 1)


def harvest_filter(base_fq, sink, label, known_total=None):
    """Fetch everything matching one filter query, sharding if it is too large."""
    total = known_total if known_total else count_for(base_fq)
    if total == 0:
        return 0
    if total <= config.DEEP_PAGE_LIMIT:
        page_through(base_fq, sink, label)
        return total
    print("    %s has %d packages, splitting by creation time" % (label, total))
    lo = datetime(2005, 1, 1)
    hi = datetime.utcnow() + timedelta(days=2)
    harvest_window(base_fq, lo, hi, sink, label)
    return total


def harvest_orphans(sink):
    """Packages that belong to no organisation are unreachable by org sharding."""
    fq = "-organization:[* TO *]"
    try:
        total = count_for(fq)
    except Exception as exc:                # noqa: BLE001
        print("orphan query not supported by this CKAN: %s" % exc)
        return
    if total == 0:
        print("no packages without an organisation")
        return
    print("harvesting %d packages with no organisation" % total)
    harvest_filter(fq, sink, "(no organisation)", known_total=total)


def harvest_org(name, sink, known_total=None):
    return harvest_filter('organization:"%s"' % name, sink, name,
                          known_total=known_total)


def harvest_all(sink, limit_orgs=None, resume=False, have=None):
    catalogue_total, _ = search(start=0, rows=0)
    triples = list_organisations()
    triples.sort(key=lambda t: -t[1])
    reachable = sum(c for _, c, _ in triples)
    print("catalogue holds %d packages" % catalogue_total)
    print("found %d organisations covering %d packages (%.1f%%)"
          % (len(triples), reachable,
             100.0 * reachable / catalogue_total if catalogue_total else 0.0))
    if triples and reachable < 0.5 * catalogue_total:
        print("WARNING: the organisation list looks truncated -- check the API "
              "response before spending an hour on a partial harvest")

    done = set()
    if resume and os.path.exists(config.HARVEST_DONE_FILE):
        with open(config.HARVEST_DONE_FILE) as f:
            done = {line.strip() for line in f if line.strip()}
        print("resuming, %d organisations already done" % len(done))

    if limit_orgs:
        triples = triples[:limit_orgs]

    shortfalls = []
    skipped = 0
    with open(config.HARVEST_DONE_FILE, "a") as done_log:
        for i, (name, count, display) in enumerate(triples, 1):
            if name in done:
                continue
            # Resuming a harvest that died partway: the output file already
            # holds whole organisations, so match them by display name against
            # the facet counts and skip the ones that are already complete.
            already = have.get(display, 0) if have else 0
            if count and already >= 0.98 * count:
                skipped += 1
                done_log.write(name + "\n")
                done_log.flush()
                continue
            if already:
                print("[%d/%d] %s (%d packages, %d already held)"
                      % (i, len(triples), name, count, already))
            else:
                print("[%d/%d] %s (%d packages)" % (i, len(triples), name, count))
            before = sink.written
            try:
                harvest_org(name, sink, known_total=count)
            except Exception as exc:        # noqa: BLE001
                print("    FAILED: %s -- continuing" % exc)
                continue
            got = sink.written - before
            # Duplicates across organisations are impossible, so a shortfall
            # here means records were genuinely missed, not deduplicated.
            if count and got < 0.95 * count:
                shortfalls.append((name, count, got))
                print("    SHORT: expected %d, wrote %d" % (count, got))
            done_log.write(name + "\n")
            done_log.flush()
            time.sleep(config.HARVEST_SLEEP)

    if skipped:
        print("\nskipped %d organisations already complete in the output file"
              % skipped)

    if not limit_orgs:
        harvest_orphans(sink)

    print("\ncatalogue reports %d packages, we wrote %d"
          % (catalogue_total, sink.written))
    if shortfalls:
        print("\n%d organisations came up short -- these were NOT fully "
              "harvested:" % len(shortfalls))
        for name, expected, got in sorted(shortfalls,
                                          key=lambda s: s[2] - s[1])[:20]:
            print("   %-50s %6d of %6d" % (name[:50], got, expected))
    elif catalogue_total > sink.written:
        print("no per-organisation shortfall; the remainder is withdrawn or "
              "restricted records")


def harvest_via_list(sink, page=None):
    """Harvest with a flat limit/offset walk over the whole catalogue."""
    page = page or config.LIST_PAGE_SIZE
    offset = 0
    empty = 0
    while True:
        if offset >= config.LIST_MAX_OFFSET:
            print("\nSTOPPING at offset %d: past this depth the portal's search "
                  "backend times out and then errors, and no amount of waiting "
                  "changes that." % offset)
            print("What was written so far is kept. Finish the rest with:")
            print("    python ingest/fetch_ckan.py --resume")
            print("which shards by organisation, so no single query pages deep.")
            return sink.written
        rows = api_get(PACKAGE_LIST, {"limit": page, "offset": offset})["result"]
        if not rows:
            empty += 1
            if empty >= 2:
                break
            offset += page
            continue
        empty = 0
        for pkg in rows:
            sink.add(pkg)
        offset += len(rows)
        print("  offset %d: corpus now %d (%d with a footprint)"
              % (offset, sink.written, sink.with_geo))
        if len(rows) < page:
            break
    return sink.written


def harvest_topics(sink):
    for topic in TOPICS:
        print("fetching %r ..." % topic)
        _, rows = search(q=topic, rows=TOPIC_ROWS)
        for pkg in rows:
            sink.add(pkg)
        print("  total so far: %d" % sink.written)
        time.sleep(1)


# ---- Entry point ----------------------------------------------------------
def load_seen(path):
    """Ids already harvested, plus how many we hold per organisation."""
    seen = set()
    per_org = Counter()
    if not os.path.exists(path):
        return seen, per_org
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                element = json.loads(line)
            except Exception:
                continue
            seen.add(element["id"])
            per_org[element.get("organization") or ""] += 1
    return seen, per_org


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topics", action="store_true",
                        help="old 6-topic mode instead of a full harvest")
    parser.add_argument("--via-list", action="store_true",
                        help="harvest through current_package_list_with_resources, "
                             "which reads the database and does not need Solr")
    parser.add_argument("--resume", action="store_true",
                        help="keep the existing output and skip finished orgs")
    parser.add_argument("--limit-orgs", type=int, default=None,
                        help="only harvest the N largest organisations")
    parser.add_argument("--rows", type=int, default=None,
                        help="rows per request (default %d)" % config.HARVEST_ROWS)
    parser.add_argument("--interval", type=float, default=None,
                        help="minimum seconds between API calls (default %.1f)"
                             % config.HARVEST_MIN_INTERVAL)
    parser.add_argument("--out", default=config.RAW_FILE)
    parser.add_argument("--save-raw", action="store_true",
                        help="also append every CKAN package verbatim to "
                             "<DATA_DIR>/%s. Off by default; to_element() "
                             "discards extras, resources and groups, and "
                             "re-deriving them otherwise means re-harvesting."
                             % RAW_PACKAGES_FILE)
    parser.add_argument("--raw-out", default=None,
                        help="override the sidecar path (implies --save-raw)")
    args = parser.parse_args()

    if not config.CKAN_API:
        raise SystemExit("corpus %r defines no CKAN_API; nothing to harvest"
                         % config.CORPUS)

    if args.rows:
        config.HARVEST_ROWS = args.rows
    if args.interval is not None:
        config.HARVEST_MIN_INTERVAL = args.interval

    raw_path = None
    if args.save_raw or args.raw_out:
        raw_path = args.raw_out or os.path.join(config.DATA_DIR,
                                                RAW_PACKAGES_FILE)

    if not args.resume:
        removable = [args.out, config.HARVEST_DONE_FILE]
        if raw_path:
            # The sidecar is append-only like the main file, so a fresh harvest
            # has to clear it too or the two would disagree about which ids
            # they hold.
            removable.append(raw_path)
        for path in removable:
            if os.path.exists(path):
                os.remove(path)
                print("removed old %s" % path)

    if args.resume:
        seen, per_org = load_seen(args.out)
        if seen:
            print("resuming: %d elements already in %s across %d organisations"
                  % (len(seen), args.out, len(per_org)))
    else:
        seen, per_org = set(), Counter()
    sink = Sink(args.out, seen=seen, raw_path=raw_path)
    if raw_path:
        print("saving raw CKAN packages to %s" % raw_path)
    started = time.time()
    try:
        if args.topics:
            harvest_topics(sink)
        elif args.via_list:
            harvest_via_list(sink)
        else:
            preflight()
            harvest_all(sink, limit_orgs=args.limit_orgs, resume=args.resume,
                        have=per_org)
    except BackendUnavailable as exc:
        print("\nSTOPPED: %s" % exc)
        print("Whatever was written so far is kept. Retry later with --resume, "
              "or use --via-list to bypass the search index.")
    finally:
        sink.close()

    print("\nwrote %d elements to %s (%d with a spatial footprint) in %.1f min"
          % (sink.written, args.out, sink.with_geo, (time.time() - started) / 60))
    if raw_path:
        print("wrote %d raw packages to %s" % (sink.raw_written, raw_path))
    print_geometry_rejects()
    if sink.written and sink.with_geo == 0:
        print("WARNING: no footprints parsed at all -- check the spatial fields")
    print("next: python ingest/sample_elements.py")


if __name__ == "__main__":
    main()
