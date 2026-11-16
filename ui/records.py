"""Read-only access to the index and the graph, for the detail view."""
import html
import re
import threading
from html.parser import HTMLParser

import config

# Cypher the neighbourhood view runs.
CYPHER_PUBLISHER = (
    "MATCH (e:Element {id: $id})-[:CONTRIBUTED_BY]->(o:Organization) "
    "RETURN o.name AS name LIMIT $limit"
)
CYPHER_TAGS = (
    "MATCH (e:Element {id: $id})-[:HAS_TAG]->(t:Tag) "
    "RETURN t.name AS name LIMIT $limit"
)
CYPHER_RELATED = (
    "MATCH (e:Element {id: $id})-[r:RELATED_TO]-(n:Element) "
    "RETURN n.id AS id, n.title AS title, n.type AS type, "
    "n.organization AS organization, r.weight AS weight "
    "ORDER BY weight DESC LIMIT $limit"
)
CYPHER_CENTER = (
    "MATCH (e:Element {id: $id}) "
    "RETURN e.id AS id, e.title AS title, e.type AS type, "
    "e.organization AS organization LIMIT 1"
)
NODE_BUDGET = {"publisher": 3, "tags": 10, "related": 17}   # 30 in all

FIELD_LABELS = {
    "title": "Title", "organization": "Publisher", "type": "Type",
    "url": "Source page", "formats": "Formats", "tags": "Tags",
    "created": "Created", "modified": "Last modified",
    "geometry": "Coverage (west, south, east, north)",
    "description": "Description",
}
# Shown in this order first; any other field follows alphabetically.
FIELD_ORDER = ("title", "organization", "type", "formats", "created",
               "modified", "geometry", "url", "tags")


# ---- text helpers ---------------------------------------------------------
class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")


def plain_text(value):
    """Descriptions in some catalogues carry HTML. The UI shows text."""
    text = value or ""
    if "<" in text and ">" in text:
        parser = _Stripper()
        try:
            parser.feed(text)
            parser.close()
            text = "".join(parser.parts)
        except Exception:                                     # noqa: BLE001
            text = re.sub(r"<[^>]*>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def short_text(value, limit=300):
    text = re.sub(r"\s+", " ", plain_text(value))
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


# ---- geometry -------------------------------------------------------------
def _flatten(coords, out):
    if coords and isinstance(coords[0], (int, float)):
        out.append(coords)
        return
    for item in coords or []:
        _flatten(item, out)


def geometry_view(geometry):
    """{bbox: [w, s, e, n], center: [lon, lat], geojson: <GeoJSON>, kind}."""
    if not isinstance(geometry, dict):
        return None
    kind = str(geometry.get("type") or "").lower()
    coords = geometry.get("coordinates")
    try:
        if kind == "envelope":
            (west, north), (east, south) = coords
            ring = [[west, south], [east, south], [east, north],
                    [west, north], [west, south]]
            geojson = {"type": "Polygon", "coordinates": [ring]}
            bbox = [west, south, east, north]
        else:
            points = []
            _flatten(coords, points)
            if not points:
                return None
            lons = [p[0] for p in points]
            lats = [p[1] for p in points]
            bbox = [min(lons), min(lats), max(lons), max(lats)]
            names = {"point": "Point", "linestring": "LineString",
                     "polygon": "Polygon", "multipoint": "MultiPoint",
                     "multilinestring": "MultiLineString",
                     "multipolygon": "MultiPolygon"}
            if kind not in names:
                return None
            geojson = {"type": names[kind], "coordinates": coords}
    except (TypeError, ValueError):
        return None
    west, south, east, north = bbox
    return {"kind": kind, "bbox": [west, south, east, north],
            "center": [(west + east) / 2.0, (south + north) / 2.0],
            "geojson": geojson}


def _pretty_value(key, value):
    if key == "geometry":
        view = geometry_view(value)
        if view:
            return ", ".join("%.4f" % v for v in view["bbox"])
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if value is None:
        return ""
    return str(value)


def metadata_fields(source):
    """Every field of the record as (label, value) rows for a two-column
    grid. The embedding vector is never fetched. Description is shown
    separately, so it is left out of the grid."""
    keys = [k for k in FIELD_ORDER if k in source]
    keys += sorted(k for k in source
                   if k not in FIELD_ORDER and k not in ("description",
                                                         "embedding"))
    rows = []
    for key in keys:
        value = _pretty_value(key, source.get(key))
        if value == "":
            continue
        label = FIELD_LABELS.get(key) or key.replace("_", " ").capitalize()
        rows.append({"key": key, "label": label, "value": value})
    return rows


# ---- the reader -----------------------------------------------------------
class Records:
    def __init__(self):
        self._lock = threading.Lock()
        self._os = None
        self._driver = None

    # Clients are built lazily so that importing this module opens nothing.
    def _client(self):
        with self._lock:
            if self._os is None:
                from opensearchpy import OpenSearch
                self._os = OpenSearch(hosts=[config.OPENSEARCH_URL], timeout=15)
            return self._os

    def _graph(self):
        with self._lock:
            if self._driver is None:
                from neo4j import GraphDatabase
                self._driver = GraphDatabase.driver(
                    config.NEO4J_URI,
                    auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))
            return self._driver

    def close(self):
        with self._lock:
            if self._driver is not None:
                self._driver.close()
            self._driver = None
            self._os = None

    # -- health probes (read-only) --
    def index_count(self):
        return self._client().count(index=config.INDEX_NAME)["count"]

    def graph_ping(self):
        with self._graph().session() as session:
            row = session.run("MATCH (e:Element) RETURN count(e) AS n").single()
            return int(row["n"])

    # -- one record --
    def get(self, record_id):
        from opensearchpy.exceptions import NotFoundError
        try:
            res = self._client().get(index=config.INDEX_NAME, id=record_id,
                                     _source_excludes="embedding")
        except NotFoundError:
            return None
        source = res.get("_source") or {}
        view = geometry_view(source.get("geometry"))
        url = source.get("url") or ""
        portal = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        return {
            "id": res.get("_id", record_id),
            "title": source.get("title", ""),
            "organization": source.get("organization", ""),
            "type": source.get("type", ""),
            "url": url,
            "portal": portal,
            "description": plain_text(source.get("description", "")),
            "tags": list(source.get("tags") or []),
            "formats": list(source.get("formats") or []),
            "created": source.get("created") or "",
            "modified": source.get("modified") or "",
            "geometry": view,
            "fields": metadata_fields(source),
        }

    def exists(self, ids):
        """Which of `ids` are documents in the index (one mget, no _source)."""
        ids = [i for i in dict.fromkeys(ids) if i]
        if not ids:
            return set()
        res = self._client().mget(index=config.INDEX_NAME, body={"ids": ids},
                                  _source=False)
        return {d["_id"] for d in res.get("docs", []) if d.get("found")}

    # -- graph neighbourhood --
    def neighbours(self, record_id, budget=None):
        budget = dict(NODE_BUDGET, **(budget or {}))
        nodes, edges = [], []
        with self._graph().session() as session:
            center = session.run(CYPHER_CENTER, id=record_id).single()
            if center is None:
                return None
            cid = "e:%s" % center["id"]
            nodes.append({"id": cid, "kind": "dataset", "label": center["title"],
                          "record_id": center["id"], "center": True,
                          "type": center["type"],
                          "organization": center["organization"]})
            for row in session.run(CYPHER_PUBLISHER, id=record_id,
                                   limit=budget["publisher"]):
                nid = "o:%s" % row["name"]
                nodes.append({"id": nid, "kind": "publisher",
                              "label": row["name"]})
                edges.append({"source": cid, "target": nid,
                              "label": "published by"})
            for row in session.run(CYPHER_TAGS, id=record_id,
                                   limit=budget["tags"]):
                nid = "t:%s" % row["name"]
                nodes.append({"id": nid, "kind": "tag", "label": row["name"]})
                edges.append({"source": cid, "target": nid, "label": "tagged"})
            related = []
            for row in session.run(CYPHER_RELATED, id=record_id,
                                   limit=budget["related"]):
                related.append(row)
        present = self.exists([r["id"] for r in related]) if related else set()
        for row in related:
            nid = "e:%s" % row["id"]
            nodes.append({"id": nid, "kind": "dataset", "label": row["title"],
                          "record_id": row["id"], "type": row["type"],
                          "organization": row["organization"],
                          "in_index": row["id"] in present})
            edges.append({"source": cid, "target": nid,
                          "label": "related dataset", "weight": row["weight"]})
        return {"nodes": nodes, "edges": edges, "node_limit": sum(budget.values())}

    # -- the box a place name resolves to --
    @staticmethod
    def query_area(place):
        """The area retrieval.spatial_search() would filter on for `place`:
        the same lookup, in the same order (the corpus place list, then the
        gazetteer when that is switched on), without running a search.
        None when the name resolves to nothing."""
        import expansion
        import retrieval
        if not place or not retrieval.place_resolution_available():
            return None
        key = retrieval.normalise_place(place)
        bbox = retrieval.BBOXES.get(key)
        source = "BBOXES"
        name = key
        if bbox is None:
            hit = expansion.scheme_b_place(place)
            if hit and hit.get("bbox"):
                bbox = hit["bbox"]
                source = "gazetteer/%s" % hit.get("kind")
                name = hit.get("name") or key
        if bbox is None:
            return None
        west, south, east, north = bbox
        return {"name": name, "source": source,
                "bbox": [west, south, east, north],
                "geojson": {"type": "Polygon", "coordinates": [[
                    [west, south], [east, south], [east, north],
                    [west, north], [west, south]]]}}
