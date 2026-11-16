"""Member graph intents under GRAPH_ORG_MATCH and GRAPH_MEMBER_ORDER.

Usage
-----
    python tests/test_graph_members.py [--live]
"""
import argparse
import io
import os
import sys
import types
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

SINK = io.StringIO()
with redirect_stdout(SINK):
    import config
    import retrieval

FAILURES = []


def check(label, condition, detail=""):
    print("  [%s] %s%s" % ("ok" if condition else "FAIL", label,
                           ("  (%s)" % (detail,)) if not condition else ""))
    if not condition:
        FAILURES.append(label)


# ---- a hand-built catalogue ------------------------------------------------
ORGS = {
    "Melbourne Water": 1,
    "Melbourne Water Corporation": 2,
    "Melbourne Water Open Data Hub": 4,
    "Mitchell Shire Council": 3,
    "Surf Coast Shire Council": 2,
    "Moira Shire Council": 2,
    "Maribyrnong City Council": 2,
    "Education": 1,
    "Department of Education": 2,
    "Orphan Registry": 1,
}
ELEMENTS = []
for org, count in ORGS.items():
    for k in range(count):
        ELEMENTS.append({"id": "%s-%d" % (org.lower().replace(" ", "-"), k),
                         "title": "%s dataset %d" % (org, k),
                         "description": "Real description %d of %s." % (k, org),
                         "type": "dataset",
                         "tags": ["t%d" % j for j in range(9)],
                         "organization": org, "url": "https://x.invalid/%s/%d" % (org, k)})
BY_ID = {e["id"]: e for e in ELEMENTS}
ANCHOR_WITHOUT_ORG = "no-org-anchor"


class Result(list):
    def single(self):
        return self[0] if self else None


class Session:
    def __init__(self, log, anchors):
        self.log, self.anchors = log, anchors

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self.log.append(("neo4j", query, params))
        if "RETURN a.id AS id" in query:
            return Result(self.anchors)
        if query == retrieval.ORGANISATION_NAMES:
            return Result({"name": o} for o in ORGS)
        if query == retrieval.AUTHORSHIP_CONTAINS_ORGS:
            e = params["entity"].lower()
            return Result({"name": o} for o in ORGS if e in o.lower())
        if query == retrieval.ANCHOR_ORGANISATION:
            el = BY_ID.get(params["aid"])
            return Result([{"name": el["organization"]}] if el else [])
        if query == retrieval.AGGREGATION_ORGS_ORDERED:
            ranked = sorted(ORGS, key=lambda o: (-ORGS[o], o))[:5]
            return Result({"name": o, "n": ORGS[o]} for o in ranked)
        # Any element query: storage order is ELEMENTS order.
        rows = [{"e": {k: e[k] for k in ("id", "title", "type", "url", "organization")},
                 "w": e["organization"]} for e in ELEMENTS]
        return Result(rows[:params.get("limit", 10)])


class Driver:
    def __init__(self, log, anchors):
        self.log, self.anchors = log, anchors

    def session(self, **kwargs):
        return Session(self.log, self.anchors)


class OpenSearch:
    """Members of the filtered organisations, reversed element order (so the
    result is visibly not storage order), minus excluded ids, cut to size."""

    def __init__(self, log):
        self.log = log

    def search(self, index=None, body=None, **kwargs):
        self.log.append(("search", body))
        filt = body["query"]["bool"].get("filter", [])
        orgs = set()
        for clause in filt:
            orgs |= set(clause.get("terms", {}).get("organization.raw", []))
        excluded = set()
        for clause in body["query"]["bool"].get("must_not", []):
            excluded |= set(clause.get("ids", {}).get("values", []))
        hits = [e for e in reversed(ELEMENTS)
                if e["organization"] in orgs and e["id"] not in excluded]
        return {"hits": {"hits": [
            {"_id": e["id"], "_source": {k: v for k, v in e.items() if k != "id"}}
            for e in hits[:body["size"]]]}}

    def mget(self, **kwargs):
        self.log.append(("mget", kwargs))
        return {"docs": []}


def setup(order="storage", match="contains", hydrate=False, gqr=False,
          anchors=None, topn=1):
    log = []
    retrieval.neo4j = Driver(log, anchors or [{"id": "mitchell-shire-council-0",
                                               "title": "Mitchell Shire Council dataset 0"}])
    retrieval.os_client = OpenSearch(log)
    retrieval._ORG_INDEX = None
    config.GRAPH_MEMBER_ORDER = order
    config.GRAPH_ORG_MATCH = match
    config.HYDRATE_CANDIDATES = hydrate
    config.GRAPH_QUERY_REWRITE = gqr
    config.GRAPH_ANCHOR_TOPN = topn
    config.RERANK_MODE = "off"
    config.CANDIDATE_POOL = 0
    return log


def searches(log):
    return [entry[1] for entry in log if entry[0] == "search"]


def cyphers(log):
    return [(entry[1], entry[2]) for entry in log if entry[0] == "neo4j"]


def graph(intent, entity, query="", **kw):
    with redirect_stdout(SINK):
        return retrieval.graph_search(intent, entity, size=10, query=query)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    question = "water datasets published by Melbourne Water"

    print("1. defaults")
    log = setup()
    graph("AUTHORSHIP", "Melbourne Water", query=question)
    check("AUTHORSHIP issues the default Cypher with the default parameters",
          cyphers(log) == [(retrieval.CYPHER_CATALOG["AUTHORSHIP"],
                            {"entity": "Melbourne Water", "limit": 10})], cyphers(log))
    log = setup()
    graph("CO_CONTRIBUTION", "Mitchell", query=question)
    check("CO_CONTRIBUTION issues the default anchored Cypher",
          [q for q, _ in cyphers(log)][1:] == [retrieval.ANCHORED_CATALOG["CO_CONTRIBUTION"]])
    log = setup()
    graph("AGGREGATION", "", query=question)
    check("AGGREGATION issues the default Cypher",
          [q for q, _ in cyphers(log)] == [retrieval.CYPHER_CATALOG["AGGREGATION"]])
    check("no OpenSearch request under the defaults", not searches(log))

    print("2. family rule")
    setup(match="family")
    with redirect_stdout(SINK):
        index = retrieval._org_index()
    check("CAP = largest own-name family (Melbourne Water -> 3)", index["cap"] == 3,
          index["cap"])
    names, _ = retrieval.authorship_organisations("Melbourne Water")
    check("name variants all selected", names == ["Melbourne Water",
          "Melbourne Water Corporation", "Melbourne Water Open Data Hub"], names)
    names, _ = retrieval.authorship_organisations("melbourne water")
    check("case does not matter", len(names) == 3, names)
    names, _ = retrieval.authorship_organisations("Mitchell Shire Council")
    check("a specific name selects exactly one", names == ["Mitchell Shire Council"])
    names, reason = retrieval.authorship_organisations("council")
    check("an over-general entity is rejected (4 > CAP 3)",
          names == [] and "more than" in reason, (names, reason))
    names, reason = retrieval.authorship_organisations("Nonexistent Body")
    check("no family -> nothing", names == [] and "no organisation" in reason)
    log = setup(match="family")
    out = io.StringIO()
    with redirect_stdout(out):
        docs = retrieval.graph_search("AUTHORSHIP", "council", size=10, query=question)
    check("graph_search returns [] for it, logs one line, sends no member request",
          docs == [] and out.getvalue().count("not resolved") == 1
          and not searches(log)
          and retrieval.AUTHORSHIP_BY_NAMES not in [q for q, _ in cyphers(log)])
    log = setup(match="family")
    docs = graph("AUTHORSHIP", "Melbourne Water", query=question)
    call = [p for q, p in cyphers(log) if q == retrieval.AUTHORSHIP_BY_NAMES]
    check("family + storage asks Cypher for exactly the family",
          call and call[0]["names"] == ["Melbourne Water",
                                        "Melbourne Water Corporation",
                                        "Melbourne Water Open Data Hub"], call)

    print("3. AUTHORSHIP + query")
    for match, expected in (("contains", ["Melbourne Water", "Melbourne Water Corporation",
                                          "Melbourne Water Open Data Hub"]),
                            ("family", ["Melbourne Water", "Melbourne Water Corporation",
                                        "Melbourne Water Open Data Hub"])):
        log = setup(order="query", match=match)
        docs = graph("AUTHORSHIP", "Melbourne Water", query=question)
        body = searches(log)[0] if searches(log) else {}
        b = body.get("query", {}).get("bool", {})
        check("%s: terms filter = %d organisations" % (match, len(expected)),
              b.get("filter") == [{"terms": {"organization.raw": expected}}], b.get("filter"))
    check("BM25 on the question, organisation not scored",
          b.get("should") == [retrieval._main_match(question, include_org=False)])
    check("sort [_score, _id asc], size = fetch_size(10)",
          body.get("sort") == ["_score", {"_id": "asc"}]
          and body.get("size") == retrieval.fetch_size(10))
    expected_order = [e["id"] for e in reversed(ELEMENTS)
                      if e["organization"].startswith("Melbourne Water")]
    check("output order is OpenSearch's order", [d["id"] for d in docs] == expected_order)
    check("relation wording unchanged",
          docs[0]["description"] == "graph evidence: intent=AUTHORSHIP, %s"
          % docs[0]["organization"])
    log = setup(order="query")
    graph("AUTHORSHIP", "Melbourne Water", query="")
    check("an empty query ranks by the entity",
          searches(log)[0]["query"]["bool"]["should"]
          == [retrieval._main_match("Melbourne Water", include_org=False)])

    print("4. CO_CONTRIBUTION + query")
    anchors = [{"id": "melbourne-water-open-data-hub-0", "title": "Hub dataset 0"},
               {"id": "melbourne-water-corporation-0", "title": "Corporation dataset 0"}]
    log = setup(order="query", anchors=anchors, topn=2, gqr=True)
    docs = graph("CO_CONTRIBUTION", "Melbourne Water", query=question)
    aids = [p.get("aid") for q, p in cyphers(log) if q == retrieval.ANCHOR_ORGANISATION]
    check("anchor -> organisation looked up for both anchors", aids ==
          ["melbourne-water-open-data-hub-0", "melbourne-water-corporation-0"], aids)
    must_not = [s["query"]["bool"].get("must_not") for s in searches(log)]
    check("each anchor excluded from its own member query", must_not == [
        [{"ids": {"values": ["melbourne-water-open-data-hub-0"]}}],
        [{"ids": {"values": ["melbourne-water-corporation-0"]}}]], must_not)
    ids = [d["id"] for d in docs]
    check("round-robin merge, no duplicates, anchors absent",
          len(ids) == len(set(ids)) and not set(ids) & {a["id"] for a in anchors}
          and ids[:2] == ["melbourne-water-open-data-hub-3",
                          "melbourne-water-corporation-1"], ids)
    check("graph_anchor set under B2", all(d.get("graph_anchor") for d in docs))
    check("relation wording unchanged",
          docs[0]["description"] == "graph evidence: same contributor '%s' as '%s'"
          % ("Melbourne Water Open Data Hub", "Hub dataset 0"), docs[0]["description"])
    log = setup(order="query", anchors=[{"id": ANCHOR_WITHOUT_ORG, "title": "Orphan"},
                                        anchors[0]], topn=2)
    out = io.StringIO()
    with redirect_stdout(out):
        docs = retrieval.graph_search("CO_CONTRIBUTION", "x", size=10, query=question)
    check("an anchor with no organisation contributes nothing and does not raise",
          docs and all(d["organization"] == "Melbourne Water Open Data Hub" for d in docs)
          and "has no organisation" in out.getvalue())

    print("5. AGGREGATION + query")
    log = setup(order="query")
    docs = graph("AGGREGATION", "", query="which organisation publishes the most")
    check("name tie-break query issued",
          [q for q, _ in cyphers(log)] == [retrieval.AGGREGATION_ORGS_ORDERED])
    top5 = sorted(ORGS, key=lambda o: (-ORGS[o], o))[:5]
    check("terms filter = the five organisations",
          searches(log)[0]["query"]["bool"]["filter"]
          == [{"terms": {"organization.raw": sorted(top5)}}])
    check("default relation wording",
          docs[0]["description"] == "graph evidence: intent=AGGREGATION, %s publishes %d elements"
          % (docs[0]["organization"], ORGS[docs[0]["organization"]]), docs[0]["description"])

    print("6. presentation follows HYDRATE_CANDIDATES")
    log = setup(order="query", hydrate=False)
    doc = graph("AUTHORSHIP", "Melbourne Water", query=question)[0]
    check("off: relation in description, tags empty (node_to_doc projection)",
          doc["description"].startswith("graph evidence:") and doc["tags"] == []
          and "graph_note" not in doc and "hydration" not in doc)
    log = setup(order="query", hydrate=True)
    docs = graph("AUTHORSHIP", "Melbourne Water", query=question)
    doc = docs[0]
    real = BY_ID[doc["id"]]
    check("on: real description, tags cut to 6, relation in graph_note, hydrated",
          doc["description"] == real["description"] and doc["tags"] == real["tags"][:6]
          and doc["graph_note"].startswith("graph evidence:")
          and doc["hydration"] == "done")
    with redirect_stdout(SINK):
        retrieval.rrf_fuse({"graph": docs})
    check("rrf_fuse sends no mget for them", not [e for e in log if e[0] == "mget"])

    config.GRAPH_MEMBER_ORDER = "storage"
    config.GRAPH_ORG_MATCH = "contains"
    config.HYDRATE_CANDIDATES = False
    config.GRAPH_QUERY_REWRITE = False
    config.GRAPH_ANCHOR_TOPN = 1

    if args.live:
        live()

    if FAILURES:
        print("\n%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("\nall checks passed")


def live():
    import importlib
    from neo4j import GraphDatabase
    from opensearchpy import OpenSearch as RealOpenSearch
    retrieval.neo4j = GraphDatabase.driver(
        config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))
    retrieval.os_client = RealOpenSearch(hosts=[config.OPENSEARCH_URL], timeout=60)
    retrieval._ORG_INDEX = None
    config.CANDIDATE_POOL = 50
    config.GRAPH_MEMBER_ORDER = "query"

    print("7. live CO_CONTRIBUTION")
    entity = "Manningham Traffic Signals"
    with redirect_stdout(SINK):
        anchors = retrieval.find_anchors(entity)
    aid, title = anchors[0]
    with retrieval.neo4j.session() as s:
        org = s.run(retrieval.ANCHOR_ORGANISATION, aid=aid).single()["name"]
        members = s.run("MATCH (o:Organization {name: $o})<-[:CONTRIBUTED_BY]-(e) "
                        "RETURN count(e) AS n", o=org).single()["n"]
    docs = graph("CO_CONTRIBUTION", entity, query="datasets from the same publisher "
                 "as Manningham Traffic Signals")
    check("anchor %r (%s): all %d results from that organisation, anchor excluded"
          % (title[:40], org, len(docs)),
          docs and all(d["organization"] == org for d in docs)
          and aid not in {d["id"] for d in docs})
    check("count = min(members - 1, 50) = %d" % min(members - 1, 50),
          len(docs) == min(members - 1, 50), len(docs))

    print("8. live AGGREGATION")
    with retrieval.neo4j.session() as s:
        frozen = [r["name"] for r in s.run(
            "MATCH (o:Organization)<-[:CONTRIBUTED_BY]-(:Element) "
            "WITH o, count(*) AS n ORDER BY n DESC LIMIT 5 RETURN o.name AS name")]
        ordered = [r["name"] for r in s.run(retrieval.AGGREGATION_ORGS_ORDERED)]
    check("the name tie-break selects the same five organisations as the default "
          "query", sorted(frozen) == sorted(ordered), (frozen, ordered))
    docs = graph("AGGREGATION", "", query="which organisation publishes the most datasets")
    check("%d members, all from those five" % len(docs),
          len(docs) == 50 and all(d["organization"] in ordered for d in docs))
    config.GRAPH_MEMBER_ORDER = "storage"
    config.CANDIDATE_POOL = 0


if __name__ == "__main__":
    main()
