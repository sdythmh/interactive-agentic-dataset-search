"""Build the OpenSearch index and the Neo4j graph.

Usage
-----
    python ingest/build_indexes.py                 # both backends
    python ingest/build_indexes.py --skip-neo4j
    python ingest/build_indexes.py --skip-opensearch
    python ingest/build_indexes.py --force        # rebuild a graph that belongs
                                                  # to another corpus

OpenSearch gets one index with three field families: BM25 text, a 384-dim k-NN
vector, and a geo_shape footprint. Neo4j gets Element / Organization / Tag nodes
with CONTRIBUTED_BY, HAS_TAG and derived RELATED_TO edges.
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from itertools import combinations

from neo4j import GraphDatabase
from opensearchpy import OpenSearch, helpers
from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def build_mapping(english_subfields=False):
    """The index mapping."""
    def text_field(extra=None):
        field = {"type": "text"}
        subfields = dict(extra or {})
        if english_subfields:
            subfields["en"] = {"type": "text", "analyzer": "english"}
        if subfields:
            field["fields"] = subfields
        return field

    return {
        "settings": {
            "index": {
                "knn": True,
                "number_of_shards": 1,
                "number_of_replicas": 0,  # single node, avoids a yellow cluster
            }
        },
        "mappings": {
            # Which corpus built this index.
            "_meta": {"corpus": config.CORPUS},
            "properties": {
                "title": text_field(),
                "description": text_field(),
                "type": {"type": "keyword"},
                "formats": {"type": "keyword"},
                "tags": text_field({"raw": {"type": "keyword"}}),
                "organization": text_field({"raw": {"type": "keyword"}}),
                "url": {"type": "keyword"},
                "geometry": {"type": "geo_shape"},
                "created": {"type": "date",
                            "format": "strict_date_optional_time||epoch_millis",
                            "ignore_malformed": True},
                "modified": {"type": "date",
                             "format": "strict_date_optional_time||epoch_millis",
                             "ignore_malformed": True},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": 384,
                    "method": {"name": "hnsw", "engine": "lucene",
                               "space_type": "cosinesimil"},
                },
            }
        },
    }


# Kept so anything importing the old constant still works.
MAPPING = build_mapping(False)


def embed_text(element, mode="title_desc"):
    """What goes into the 384-dim vector for one element."""
    title = (element.get("title") or "").strip()
    description = (element.get("description") or "")[:1000]
    if mode == "title_desc":
        return "%s. %s" % (element.get("title"), description)
    if mode != "full":
        raise ValueError("unknown embed-fields mode %r" % mode)

    parts = []
    if title:
        parts.append(title + ".")
    org = (element.get("organization") or "").strip()
    if org:
        parts.append("Published by %s." % org)
    tags = [t for t in (element.get("tags") or []) if t][:8]
    if tags:
        parts.append("Tags: %s." % ", ".join(tags))
    if description.strip():
        parts.append(description.strip())
    return " ".join(parts) or "(no metadata)"


# ---- Corpus IO ------------------------------------------------------------
def iter_elements(path):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def count_lines(path):
    with open(path) as f:
        return sum(1 for line in f if line.strip())


# ---- OpenSearch ----------------------------------------------------------
def encode_batch(batch, model, embed_mode="title_desc"):
    texts = [embed_text(e, embed_mode) for e in batch]
    vectors = model.encode(texts, normalize_embeddings=True,
                           batch_size=config.EMBED_BATCH)
    for element, vector in zip(batch, vectors):
        doc = dict(element)
        doc.pop("id")                       # id lives in _id, not in _source
        doc["embedding"] = vector.tolist()
        if doc.get("geometry") is None:
            doc.pop("geometry", None)       # never index a null geo_shape
        for field in ("created", "modified"):
            if not doc.get(field):
                doc.pop(field, None)
        yield {"_index": config.INDEX_NAME, "_id": element["id"], "_source": doc}


def action_stream(path, model, total, embed_mode="title_desc"):
    batch = []
    done = 0
    for element in iter_elements(path):
        batch.append(element)
        if len(batch) >= config.EMBED_BATCH:
            for action in encode_batch(batch, model, embed_mode):
                yield action
            done += len(batch)
            batch = []
            if done % (config.EMBED_BATCH * 20) == 0:
                print("  embedded %d/%d" % (done, total))
    if batch:
        for action in encode_batch(batch, model, embed_mode):
            yield action


def build_opensearch(path, english_subfields=False, embed_mode="title_desc"):
    client = OpenSearch(hosts=[config.OPENSEARCH_URL], timeout=120)
    if client.indices.exists(index=config.INDEX_NAME):
        print("deleting old index")
        client.indices.delete(index=config.INDEX_NAME)
    client.indices.create(index=config.INDEX_NAME,
                          body=build_mapping(english_subfields))

    total = count_lines(path)
    print("indexing %d elements into %s (embeddings on %s)"
          % (total, config.INDEX_NAME, config.DEVICE))
    print("  english subfields: %s | embedding text: %s"
          % ("yes" if english_subfields else "no", embed_mode))
    model = SentenceTransformer(config.EMBED_MODEL, device=config.DEVICE)

    ok = 0
    failures = []
    for success, info in helpers.streaming_bulk(
            client,
            action_stream(path, model, total, embed_mode),
            chunk_size=config.OS_BULK_CHUNK,
            raise_on_error=False,
            max_retries=2,
            request_timeout=120):
        if success:
            ok += 1
        else:
            failures.append(info)

    client.indices.refresh(index=config.INDEX_NAME)
    print("indexed %d documents, %d errors" % (ok, len(failures)))
    for failure in failures[:3]:
        print("   ", str(failure)[:300])

    # Sanity output rather than silence: a footprint count of zero means the
    # spatial retrieval path is dead even though nothing raised.
    geo = client.count(index=config.INDEX_NAME,
                       body={"query": {"exists": {"field": "geometry"}}})["count"]
    print("documents with a geo_shape footprint: %d" % geo)
    if geo == 0:
        print("WARNING: spatial retrieval will always return nothing")

    agg = client.search(index=config.INDEX_NAME, size=0,
                        body={"aggs": {"t": {"terms": {"field": "type"}}}})
    print("type distribution:")
    for bucket in agg["aggregations"]["t"]["buckets"]:
        print("   %-12s %d" % (bucket["key"], bucket["doc_count"]))


# ---- Neo4j ---------------------------------------------------------------
ELEMENT_UNWIND = """
UNWIND $rows AS row
MERGE (e:Element {id: row.id})
SET e.title = row.title, e.type = row.type, e.url = row.url,
    e.organization = row.organization
"""

ORG_UNWIND = """
UNWIND $rows AS row
MATCH (e:Element {id: row.id})
MERGE (o:Organization {name: row.organization})
MERGE (e)-[:CONTRIBUTED_BY]->(o)
"""

TAG_UNWIND = """
UNWIND $rows AS row
MATCH (e:Element {id: row.id})
UNWIND row.tags AS tag
MERGE (t:Tag {name: tag})
MERGE (e)-[:HAS_TAG]->(t)
"""

RELATED_UNWIND = """
UNWIND $rows AS row
MATCH (a:Element {id: row.a})
MATCH (b:Element {id: row.b})
MERGE (a)-[r:RELATED_TO]->(b)
SET r.weight = row.w
"""


# ---- Corpus marker --------------------------------------------------------
# Neo4j community edition holds one graph per server, and clear_graph() below
# deletes every node in it. Before corpora became pluggable that was harmless;
# now `CORPUS=<other> python ingest/build_indexes.py` against the default URI
# would silently wipe the au graph. So the graph records which corpus built
# it, and a build for a different corpus refuses to clear it.
#
# The marker is written only after the build's own statistics are printed,
# and nothing in retrieval matches unlabelled nodes, so for au the graph and
# every query against it are as before.
MARKER_LABEL = "Meta"
# A graph holding Element nodes but no marker was built before markers
# existed, and every such graph is the au graph.
LEGACY_CORPUS = "au"


def graph_owner(session):
    """The corpus a graph belongs to, or None for an empty graph."""
    owners = sorted({r["corpus"] for r in session.run(
        "MATCH (m:%s) RETURN m.corpus AS corpus" % MARKER_LABEL)
        if r["corpus"]})
    if len(owners) > 1:
        return ",".join(owners)
    if owners:
        return owners[0]
    has_elements = session.run(
        "MATCH (e:Element) RETURN count(e) > 0 AS any").single()["any"]
    return LEGACY_CORPUS if has_elements else None


def check_graph_owner(session, force=False):
    owner = graph_owner(session)
    if owner is None or owner == config.CORPUS:
        return
    if force:
        print("WARNING: the graph at %s belongs to corpus %r; --force given, "
              "clearing it for corpus %r" % (config.NEO4J_URI, owner,
                                             config.CORPUS))
        return
    raise SystemExit(
        "refusing to clear the graph at %s: it belongs to corpus %r, and this "
        "build is for corpus %r.\nPoint corpora/%s/corpus.py NEO4J_URI at a "
        "separate Neo4j server, or pass --force to discard the %r graph."
        % (config.NEO4J_URI, owner, config.CORPUS, config.CORPUS, owner))


def mark_graph(session):
    session.run("MERGE (m:%s {corpus: $corpus})" % MARKER_LABEL,
                corpus=config.CORPUS)
    print("graph marked as corpus %r" % config.CORPUS)


def clear_graph(session):
    """Delete in batches. One DETACH DELETE over 300k nodes blows up the heap."""
    print("clearing old graph")
    while True:
        deleted = session.run(
            "MATCH (n) WITH n LIMIT 10000 DETACH DELETE n RETURN count(*) AS c"
        ).single()["c"]
        if deleted == 0:
            break


def reset_schema(session):
    """Drop leftover standalone indexes."""
    try:
        rows = list(session.run("SHOW INDEXES YIELD name, type, owningConstraint"))
    except Exception as exc:                # noqa: BLE001
        print("  could not list indexes (%s), continuing" % str(exc)[:80])
        return
    for row in rows:
        if row.get("owningConstraint") is not None:
            continue
        if (row.get("type") or "").upper() == "LOOKUP":
            continue
        name = row.get("name")
        if not name:
            continue
        print("  dropping leftover index %s" % name)
        session.run("DROP INDEX `%s` IF EXISTS" % name)


def create_constraints(session):
    reset_schema(session)
    for statement in (
        "CREATE CONSTRAINT element_id IF NOT EXISTS "
        "FOR (e:Element) REQUIRE e.id IS UNIQUE",
        "CREATE CONSTRAINT org_name IF NOT EXISTS "
        "FOR (o:Organization) REQUIRE o.name IS UNIQUE",
        "CREATE CONSTRAINT tag_name IF NOT EXISTS "
        "FOR (t:Tag) REQUIRE t.name IS UNIQUE",
    ):
        session.run(statement)


def run_batches(session, statement, rows, label):
    sent = 0
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= config.NEO4J_BATCH:
            session.run(statement, rows=batch)
            sent += len(batch)
            batch = []
            if sent % (config.NEO4J_BATCH * 10) == 0:
                print("  %s: %d" % (label, sent))
    if batch:
        session.run(statement, rows=batch)
        sent += len(batch)
    print("  %s: %d done" % (label, sent))


def derive_related_edges(path):
    """Tag overlap -> RELATED_TO, via an inverted index."""
    ids = []
    tag_to_idx = defaultdict(list)
    for index, element in enumerate(iter_elements(path)):
        ids.append(element["id"])
        for tag in set(element.get("tags") or []):
            tag_to_idx[tag].append(index)
    print("  %d elements, %d distinct tags" % (len(ids), len(tag_to_idx)))

    # Pick the largest df cap that keeps the pair count affordable, so a big
    # corpus cannot blow up memory 20 minutes into the build.
    cap = max(2, config.MAX_TAG_DF)
    projected = 0
    while True:
        projected = sum(len(v) * (len(v) - 1) // 2
                        for v in tag_to_idx.values() if 2 <= len(v) <= cap)
        if projected <= config.MAX_CANDIDATE_PAIRS or cap <= 2:
            break
        cap = max(2, int(cap * 0.7))
    print("  tag df cap %d -> %d candidate pairs" % (cap, projected))

    mode = getattr(config, "EDGE_WEIGHT", "count")
    if mode == "count":
        # Default: weight = number of shared tags.
        pair_counts = Counter()
        for indices in tag_to_idx.values():
            if not 2 <= len(indices) <= cap:
                continue
            for a, b in combinations(sorted(indices), 2):
                pair_counts[(a, b)] += 1

        edges = [(a, b, w) for (a, b), w in pair_counts.items()
                 if w >= config.MIN_SHARED_TAGS]
        del pair_counts
        print("  %d pairs share >= %d tags"
              % (len(edges), config.MIN_SHARED_TAGS))
    elif mode == "idf":
        # EDGE_WEIGHT=idf. Sharing one rare tag says more about two records
        # than sharing three tags every second dataset carries.
        import math
        n_elements = len(ids)
        pair_packed = defaultdict(float)
        for indices in tag_to_idx.values():
            size = len(indices)
            if not 2 <= size <= cap:
                continue
            idf = math.log(n_elements / float(size))
            assert 0.0 <= idf < 1e3
            for a, b in combinations(sorted(indices), 2):
                pair_packed[(a, b)] += 1e4 + idf

        edges = []
        for (a, b), packed in pair_packed.items():
            count = int(packed // 1e4)
            if count >= config.MIN_SHARED_TAGS:
                idf_sum = packed - count * 1e4
                assert idf_sum < 1e4
                edges.append((a, b, round(idf_sum, 4)))
        del pair_packed
        print("  %d pairs share >= %d tags, weighted by summed idf"
              % (len(edges), config.MIN_SHARED_TAGS))
    else:
        raise ValueError("unknown EDGE_WEIGHT %r; use count or idf" % mode)

    # Degree cap: keep the strongest edges so hub elements do not dominate.
    edges.sort(key=lambda e: -e[2])
    degree = Counter()
    kept = []
    for a, b, w in edges:
        if degree[a] >= config.MAX_EDGES_PER_ELEMENT:
            continue
        if degree[b] >= config.MAX_EDGES_PER_ELEMENT:
            continue
        degree[a] += 1
        degree[b] += 1
        kept.append({"a": ids[a], "b": ids[b], "w": w})
    print("  keeping %d edges after the degree cap of %d"
          % (len(kept), config.MAX_EDGES_PER_ELEMENT))
    return kept


def build_neo4j(path, force=False):
    driver = GraphDatabase.driver(
        config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
    )
    with driver.session() as session:
        check_graph_owner(session, force=force)
        clear_graph(session)
        create_constraints(session)

        print("creating Element nodes")
        run_batches(session, ELEMENT_UNWIND, (
            {"id": e["id"], "title": e["title"], "type": e["type"],
             "url": e["url"], "organization": e.get("organization") or ""}
            for e in iter_elements(path)), "elements")

        print("creating CONTRIBUTED_BY edges")
        run_batches(session, ORG_UNWIND, (
            {"id": e["id"], "organization": e["organization"]}
            for e in iter_elements(path) if e.get("organization")), "orgs")

        print("creating HAS_TAG edges")
        run_batches(session, TAG_UNWIND, (
            {"id": e["id"], "tags": sorted(set(e["tags"]))}
            for e in iter_elements(path) if e.get("tags")), "tags")

        print("deriving RELATED_TO edges (shared tags >= %d)"
              % config.MIN_SHARED_TAGS)
        run_batches(session, RELATED_UNWIND,
                    derive_related_edges(path), "related")

        nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        related = session.run(
            "MATCH ()-[r:RELATED_TO]->() RETURN count(r) AS c").single()["c"]
        isolated = session.run(
            "MATCH (e:Element) WHERE NOT (e)-[:RELATED_TO]-() "
            "RETURN count(e) AS c").single()["c"]
        print("graph: %d nodes, %d relationships (%d RELATED_TO)"
              % (nodes, rels, related))
        print("elements with no RELATED_TO edge (graph islands): %d" % isolated)
        mark_graph(session)
    driver.close()


def check_backends(need_opensearch=True, need_neo4j=True):
    """Verify both stores answer before doing any expensive work."""
    problems = []
    if need_opensearch:
        try:
            OpenSearch(hosts=[config.OPENSEARCH_URL], timeout=10).info()
        except Exception as exc:            # noqa: BLE001
            problems.append("OpenSearch at %s: %s"
                            % (config.OPENSEARCH_URL, str(exc)[:120]))
    if need_neo4j:
        try:
            driver = GraphDatabase.driver(
                config.NEO4J_URI,
                auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))
            driver.verify_connectivity()
            driver.close()
        except Exception as exc:            # noqa: BLE001
            problems.append("Neo4j at %s: %s" % (config.NEO4J_URI, str(exc)[:120]))

    if problems:
        print("Backends are not reachable:")
        for problem in problems:
            print("   %s" % problem)
        print("\nStart them with:  docker compose up -d")
        print("OpenSearch needs 20-40 seconds before it answers. Check with:")
        print("   curl -s localhost:9200")
        print("   docker compose logs opensearch | tail -40")
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=config.ELEMENTS_FILE)
    parser.add_argument("--skip-opensearch", action="store_true")
    parser.add_argument("--skip-neo4j", action="store_true")
    parser.add_argument("--index", default="",
                        help="index name to write; defaults to "
                             "config.INDEX_NAME. Alternative indexes are "
                             "built side by side, never in place.")
    parser.add_argument("--english-subfields", action="store_true",
                        help="add an english-analysed .en subfield to "
                             "every text field")
    parser.add_argument("--embed-fields", default="title_desc",
                        choices=["title_desc", "full"],
                        help="what goes into the embedding. title_desc "
                             "is title and description; full adds organisation "
                             "and tags")
    parser.add_argument("--force", action="store_true",
                        help="clear and rebuild the Neo4j graph even if it "
                             "is marked as belonging to another corpus")
    args = parser.parse_args()

    if not os.path.exists(args.corpus):
        raise SystemExit("%s not found -- run ingest/sample_elements.py first"
                         % args.corpus)

    if args.index:
        config.INDEX_NAME = args.index

    print("build configuration")
    print("  corpus           %s" % config.CORPUS)
    print("  index            %s" % config.INDEX_NAME)
    print("  english subfields %s" % args.english_subfields)
    print("  embedding text    %s" % args.embed_fields)
    print("  min shared tags   %d" % config.MIN_SHARED_TAGS)
    print("  edge weight       %s" % getattr(config, "EDGE_WEIGHT", "count"))
    print()

    check_backends(need_opensearch=not args.skip_opensearch,
                   need_neo4j=not args.skip_neo4j)

    if not args.skip_opensearch:
        build_opensearch(args.corpus,
                         english_subfields=args.english_subfields,
                         embed_mode=args.embed_fields)
    if not args.skip_neo4j:
        build_neo4j(args.corpus, force=args.force)
    print("\ndone.")


if __name__ == "__main__":
    main()
