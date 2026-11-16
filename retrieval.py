"""Retrieval paths (keyword, semantic, spatial, graph, and the publisher
path) and reciprocal rank fusion.
"""
import json
import os
import re
from collections import defaultdict

from neo4j import GraphDatabase
from opensearchpy import OpenSearch
from sentence_transformers import SentenceTransformer

import config
import expansion
import rerank

os_client = OpenSearch(hosts=[config.OPENSEARCH_URL], timeout=60)
neo4j = GraphDatabase.driver(
    config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
)

_embedder = None

# ---- Index ownership ------------------------------------------------------
# The corpus whose indexes predate the _meta label and may therefore carry
# none. Mirrors ingest/build_indexes.LEGACY_CORPUS.
LEGACY_INDEX_CORPUS = "au"
_index_corpus_checked = set()


def _ensure_index_corpus():
    """Refuse to search another corpus's index. No-op for au."""
    if getattr(config, "CORPUS", LEGACY_INDEX_CORPUS) == LEGACY_INDEX_CORPUS:
        return
    index = config.INDEX_NAME
    if index in _index_corpus_checked:
        return

    try:
        mapping = os_client.indices.get_mapping(index=index)
    except Exception as exc:                            # noqa: BLE001
        raise SystemExit(
            "cannot read the mapping of index %r to check which corpus built "
            "it: %r" % (index, exc))

    meta = ((mapping.get(index) or {}).get("mappings") or {}).get("_meta") or {}
    owner = meta.get("corpus")
    if owner is None:
        raise SystemExit(
            "index %r carries no _meta.corpus, so it cannot be shown to hold "
            "the %r corpus.\nIt was built before the label existed, which "
            "means it is almost certainly %r's.\nRebuild it with "
            "CORPUS=%s python ingest/build_indexes.py, or point INDEX_NAME "
            "somewhere else."
            % (index, config.CORPUS, LEGACY_INDEX_CORPUS, config.CORPUS))
    if owner != config.CORPUS:
        raise SystemExit(
            "index %r was built by corpus %r, but this process is %r.\n"
            "Searching it would return the wrong catalogue's documents and "
            "say nothing about it.\nUnset INDEX_NAME to use this corpus's "
            "default (%s), or name the right index."
            % (index, owner, config.CORPUS,
               config.CORPUS_SPEC.corpus.INDEX_NAME))
    _index_corpus_checked.add(index)


def encode_query(text):
    """One normalised vector as a plain list. The single entry point other
    modules use, so nothing else has to know about the embedder."""
    return get_embedder().encode(text, normalize_embeddings=True).tolist()


def get_embedder():
    """Load lazily so we don't grab a CUDA context before Ollama loads the LLM."""
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(config.EMBED_MODEL, device=config.DEVICE)
    return _embedder


def to_doc(hit):
    """Standard projection: every retriever returns this same shape."""
    src = hit["_source"]
    return {
        "id": hit["_id"],
        "title": src.get("title", ""),
        "description": src.get("description", ""),
        "type": src.get("type", ""),
        "tags": src.get("tags", [])[:6],
        "organization": src.get("organization", ""),
        "url": src.get("url", ""),
    }


def _text_fields(include_org=True):
    """Fields the BM25 paths search, plus the english-analysed subfields when
    USE_EN_SUBFIELDS is on.
    """
    fields = ["title^3", "description", "tags^2"]
    if include_org:
        fields.append("organization")
    if getattr(config, "USE_EN_SUBFIELDS", False):
        fields += ["title.en^2", "description.en", "tags.en"]
        if include_org:
            fields.append("organization.en")
    return fields


def _main_match(query, include_org=True):
    """The one place the main lexical clause is built, so
    BM25_MIN_SHOULD_MATCH and USE_EN_SUBFIELDS each have a single point of
    application."""
    clause = {"query": query, "fields": _text_fields(include_org)}
    msm = getattr(config, "BM25_MIN_SHOULD_MATCH", "")
    if msm:
        clause["minimum_should_match"] = msm
    return {"multi_match": clause}


def _norm_title(title):
    return " ".join((title or "").lower().split())


def candidate_pool():
    """How many fused documents are carried to the final cut."""
    explicit = getattr(config, "CANDIDATE_POOL", 0)
    if explicit and explicit > 0:
        return explicit
    if rerank.active():
        return max(config.TOP_K, getattr(config, "RERANK_POOL", config.TOP_K))
    return config.TOP_K


def fetch_size(nominal):
    """Single point where candidate depth enters every retrieval path."""
    return max(int(nominal), candidate_pool())


def dedup_by_title(docs):
    """Keep the highest-ranked document of each title group."""
    if not getattr(config, "DEDUP_BY_TITLE", False):
        return docs
    kept, seen = [], set()
    for doc in docs:
        key = _norm_title(doc.get("title"))
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        kept.append(doc)
    return kept


def finalise(question, docs, top_k=None):
    """The one place a ranking becomes the ten documents a user sees:
    optional reranking, then the cut.
    """
    return rerank.rerank(question, docs)[:(top_k or config.TOP_K)]


def _phrase_clauses(phrase):
    """Extra should-clauses that reward the whole topic phrase."""
    if not phrase or len(phrase.split()) < 2:
        return []
    return [
        {"match_phrase": {"title": {"query": phrase, "boost": 6}}},
        {"match_phrase": {"description": {"query": phrase, "boost": 3}}},
    ]


def _expansion_clauses(expansion_terms):
    """Turn a list of vocabulary/gazetteer terms into low-weight shoulds."""
    if not expansion_terms:
        return []
    clauses = []
    for term in expansion_terms:
        if not term:
            continue
        clauses.append({
            "multi_match": {
                "query": term,
                "fields": ["title^2", "tags^3", "organization^2"],
                "boost": config.EXPANSION_KEYWORD_BOOST,
            }
        })
    return clauses


# ---- 1. Keyword (BM25) ----------------------------------------------------
def keyword_search(query, size=10, phrase="", expansion_terms=None):
    _ensure_index_corpus()
    should = ([_main_match(query)]
              + _phrase_clauses(phrase)
              + _expansion_clauses(expansion_terms))

    body = {
        "size": fetch_size(size),
        "_source": {"excludes": ["embedding"]},
        "query": {"bool": {"should": should, "minimum_should_match": 1}},
    }
    res = os_client.search(index=config.INDEX_NAME, body=body)
    return [to_doc(h) for h in res["hits"]["hits"]]


# ---- 2. Semantic (k-NN) ---------------------------------------------------
def semantic_search(query, size=10, vector=None):
    _ensure_index_corpus()
    # bge-*-en-v1.5 is an asymmetric model: the document side is encoded bare,
    # the query side behind a fixed instruction (EMBED_QUERY_PREFIX).
    text = query
    if getattr(config, "EMBED_QUERY_PREFIX", False):
        text = config.BGE_QUERY_PREFIX + query
    k = fetch_size(size)
    # A caller that already built a query vector (hyde.query_vector) passes
    # it; with vector=None the query is encoded here.
    vec = (list(vector) if vector is not None
           else get_embedder().encode(text, normalize_embeddings=True).tolist())
    body = {
        "size": k,
        "_source": {"excludes": ["embedding"]},
        "query": {"knn": {"embedding": {"vector": vec, "k": k}}},
    }
    res = os_client.search(index=config.INDEX_NAME, body=body)
    return [to_doc(h) for h in res["hits"]["hits"]]


# ---- 3. Spatial (geo_shape) -----------------------------------------------
# Place names, their boxes and the words stripped from a place string belong
# to the corpus (corpora/<name>/places.py). They are bound here once, at
# import, because a process serves exactly one corpus; the names are kept
# because eval/a3_headroom.py reads retrieval.BBOXES and normalise_place().
# A corpus without a places module gets empty tables, and spatial_search()
# then switches itself off (see place_resolution_available()).
_PLACES = config.PLACES
BBOXES = _PLACES.BBOXES if _PLACES is not None else {}  # [min_lon, min_lat, max_lon, max_lat]

_PLACE_PREFIXES = _PLACES.PLACE_PREFIXES if _PLACES is not None else ()
_PLACE_SUFFIXES = _PLACES.PLACE_SUFFIXES if _PLACES is not None else ()


def place_resolution_available():
    """Whether this corpus can turn a place string into a box at all."""
    return bool(BBOXES) or bool(getattr(config, "GAZETTEER_FILE", None))


def normalise_place(place):
    """The router writes 'Victoria, Australia' or 'Greater Melbourne'; the
    dictionary holds 'victoria' and 'melbourne'. Without this the lookup misses
    and the spatial path silently drops out of the plan."""
    if not place:
        return ""
    text = place.lower().strip().strip(".")
    text = text.split(",")[0].strip()
    if text.startswith("the "):
        text = text[4:].strip()
    for prefix in _PLACE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    for suffix in _PLACE_SUFFIXES:
        if text.endswith(suffix):
            text = text[:-len(suffix)].strip()
    return text


# ---- SPATIAL_NAME_FALLBACK: name evidence alongside geometry --------------
_token_df = None
_TOKEN_RE = config.LANGUAGE.PLACE_TOKEN_RE


def _load_token_df():
    """Corpus token document frequencies, built by ingest/build_token_df.py."""
    global _token_df
    if _token_df is not None:
        return _token_df
    path = getattr(config, "TOKEN_DF_FILE", "data/token_df.json")
    if not os.path.exists(path):
        print("[warn] retrieval: %s not found, the SPATIAL_NAME_FALLBACK=df gate cannot be "
              "evaluated and the name fallback stays off" % path)
        _token_df = {"corpus_size": 0, "df": {}}
        return _token_df
    with open(path) as f:
        _token_df = json.load(f)
    return _token_df


def _place_is_selective(text):
    """True when the rarest token of the place phrase is rare enough that
    matching on it is not the same as matching half the catalogue.
    """
    table = _load_token_df()
    total = table.get("corpus_size") or 0
    if not total:
        return False
    tokens = [t.lower() for t in _TOKEN_RE.findall(text or "")]
    if not tokens:
        return False
    df = table.get("df") or {}
    rarest = min(df.get(t, 0) for t in tokens)
    return rarest < getattr(config, "SPATIAL_NAME_DF_MAX", 0.05) * total


def _name_clause(place_text):
    """Selection clause for 'this document says it is about that place'."""
    mode = getattr(config, "SPATIAL_NAME_FALLBACK", "off")
    if mode in ("off", "0", "", None):
        return None
    text = normalise_place(place_text)
    if not text:
        return None
    if mode == "df" and not _place_is_selective(text):
        print("[info] spatial: name fallback skipped for %r (not selective "
              "enough in the corpus)" % text)
        return None
    return {"multi_match": {"query": text, "type": "phrase",
                            "fields": ["title", "description",
                                       "organization"]}}


def spatial_search(place, query="", size=10, phrase="", expansion_terms=None):
    """Geo filter, then rank topically INSIDE the filtered set."""
    if not place_resolution_available():
        print("[info] spatial: corpus %r provides no place resolution, "
              "spatial path off" % getattr(config, "CORPUS", "?"))
        return []
    # After the kill-switch, not before: a corpus with no place resolution
    # returns [] without ever reaching the index, and must not be made to fetch
    # a mapping to do it.
    _ensure_index_corpus()
    key = normalise_place(place)
    bbox = BBOXES.get(key)
    hit_source = "BBOXES"
    if bbox is None:
        hit = expansion.scheme_b_place(place)
        if hit and hit.get("bbox"):
            bbox = hit["bbox"]
            hit_source = "gazetteer/%s" % hit["kind"]
            print("[info] spatial: gazetteer resolved %r -> %s (%s)"
                  % (place, hit["name"], hit["kind"]))
    if bbox is None:
        print("[warn] spatial: no bbox for place %r (normalised %r)"
              % (place, key))
        return []

    min_lon, min_lat, max_lon, max_lat = bbox
    relation = getattr(config, "SPATIAL_RELATION", "intersects")
    if relation not in ("intersects", "within"):
        raise ValueError("unknown SPATIAL_RELATION %r; use intersects or "
                         "within" % relation)
    geo_clause = {
        "geo_shape": {
            "geometry": {
                "shape": {
                    "type": "envelope",
                    "coordinates": [[min_lon, max_lat], [max_lon, min_lat]],
                },
                "relation": relation,
            }
        }
    }
    # With the fallback off the filter is the geo_shape clause alone.
    name_clause = _name_clause(place)
    if name_clause is None:
        bool_query = {"filter": geo_clause}
    else:
        print("[info] spatial: name fallback active for %r (%s)"
              % (place, hit_source))
        bool_query = {"filter": {"bool": {
            "should": [geo_clause, name_clause],
            "minimum_should_match": 1,
        }}}
    should = []
    if query:
        should.append(_main_match(query, include_org=False))
    should += _phrase_clauses(phrase)
    should += _expansion_clauses(expansion_terms)
    if should:
        bool_query["should"] = should
        # A bool query that already has a filter defaults minimum_should_match
        # to 0, so these should-clauses only score and never select: a document
        # sharing not one word with the query is still returned, and still
        # takes a slot in the RRF pool.
        if getattr(config, "SPATIAL_TOPIC_GATE", False):
            bool_query["minimum_should_match"] = 1

    body = {
        "size": fetch_size(size),
        "_source": {"excludes": ["embedding"]},
        "query": {"bool": bool_query},
    }
    res = os_client.search(index=config.INDEX_NAME, body=body)
    hits = res["hits"]["hits"]
    if not hits:
        # SPATIAL_RELATION=within can empty this path out: it excludes every
        # national and state-wide envelope, and for some places nothing
        # narrower exists in the corpus.
        print("[warn] spatial: %r (%s, relation=%s) matched no documents"
              % (place, hit_source, relation))
    return [to_doc(h) for h in hits]


# ---- 4. Graph (intent-classified Cypher catalog) ---------------------------
CYPHER_CATALOG = {
    "AUTHORSHIP": (
        "MATCH (e:Element)-[:CONTRIBUTED_BY]->(o:Organization) "
        "WHERE toLower(o.name) CONTAINS toLower($entity) "
        "RETURN e, o.name AS w LIMIT $limit"
    ),
    "AGGREGATION": (
        "MATCH (o:Organization)<-[:CONTRIBUTED_BY]-(:Element) "
        "WITH o, count(*) AS n ORDER BY n DESC LIMIT 5 "
        "MATCH (o)<-[:CONTRIBUTED_BY]-(e:Element) "
        "RETURN e, o.name + ' publishes ' + toString(n) + ' elements' AS w "
        "LIMIT $limit"
    ),
}

ANCHORED_CATALOG = {
    "PROVENANCE": (
        "MATCH (a:Element {id: $aid})-[r:RELATED_TO]-(b:Element) "
        "RETURN b AS e, r.weight AS w ORDER BY w DESC LIMIT $limit"
    ),
    "CO_CONTRIBUTION": (
        "MATCH (a:Element {id: $aid})-[:CONTRIBUTED_BY]->(o:Organization)"
        "<-[:CONTRIBUTED_BY]-(b:Element) WHERE b <> a "
        "RETURN b AS e, o.name AS w LIMIT $limit"
    ),
}


def find_anchors(entity, top_n=None):
    """Elements a relational query could be about. Returns [(id, title), ...]."""
    if not entity:
        return []
    top_n = int(top_n if top_n is not None
                else getattr(config, "GRAPH_ANCHOR_TOPN", 1))
    top_n = max(1, top_n)

    with neo4j.session() as session:
        if top_n == 1:
            rows = list(session.run(
                "MATCH (a:Element) WHERE toLower(a.title) CONTAINS toLower($entity) "
                "RETURN a.id AS id, a.title AS title LIMIT 1",
                entity=entity,
            ))
        else:
            rows = list(session.run(
                "MATCH (a:Element) WHERE toLower(a.title) CONTAINS toLower($entity) "
                "RETURN a.id AS id, a.title AS title "
                "ORDER BY size(a.title), a.id LIMIT $limit",
                entity=entity, limit=top_n,
            ))
    anchors = [(r["id"], r["title"]) for r in rows]
    if anchors:
        return anchors

    hits = keyword_search(entity, size=top_n)
    return [(h["id"], h["title"]) for h in hits[:top_n]]


def find_anchor(entity):
    """Back-compatible single-anchor wrapper: (id, title) or None."""
    anchors = find_anchors(entity, top_n=1)
    return anchors[0] if anchors else None


def _hydrate_active():
    return bool(getattr(config, "HYDRATE_CANDIDATES", False))


def node_to_doc(node, description):
    """Standard projection for a graph result."""
    doc = {
        "id": node["id"],
        "title": node.get("title", ""),
        "description": description,
        "type": node.get("type", ""),
        "tags": [],
        "organization": node.get("organization", ""),
        "url": node.get("url", ""),
    }
    if _hydrate_active():
        doc["description"] = ""
        doc["graph_note"] = description
        doc["hydration"] = "pending"
    return doc


# ---- Member intents (AUTHORSHIP, CO_CONTRIBUTION, AGGREGATION) -------------
# The default Cypher for these intents ends in `LIMIT $limit` with no ORDER
# BY: which members come back, and in what order, is the store's order,
# unrelated to the question. AUTHORSHIP also selects organisations by
# substring, so the entity "council" selects 186 of them. Two switches, one
# mechanism each, both off by default.
#
#   GRAPH_ORG_MATCH=family     AUTHORSHIP: organisations whose name tokens
#                              include every token of the entity, rejected
#                              when that family is larger than any real
#                              publisher name's own family (see _org_index()).
#   GRAPH_MEMBER_ORDER=query   members come from OpenSearch, filtered on
#                              organization.raw and ranked by BM25 on the
#                              question, _id breaking ties; AGGREGATION's
#                              top-five organisations get a name tie-break.
#
# organization.raw member counts were checked equal to Neo4j CONTRIBUTED_BY
# counts for all 978 au organisations, so the query order changes order and
# truncation, never membership.
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_ORG_INDEX = None

AGGREGATION_ORGS_ORDERED = (
    "MATCH (o:Organization)<-[:CONTRIBUTED_BY]-(:Element) "
    "WITH o, count(*) AS n ORDER BY n DESC, o.name ASC LIMIT 5 "
    "RETURN o.name AS name, n"
)
AUTHORSHIP_CONTAINS_ORGS = (
    "MATCH (o:Organization)<-[:CONTRIBUTED_BY]-(:Element) "
    "WHERE toLower(o.name) CONTAINS toLower($entity) "
    "RETURN DISTINCT o.name AS name"
)
AUTHORSHIP_BY_NAMES = (
    "MATCH (e:Element)-[:CONTRIBUTED_BY]->(o:Organization) "
    "WHERE o.name IN $names RETURN e, o.name AS w LIMIT $limit"
)
ANCHOR_ORGANISATION = (
    "MATCH (a:Element {id: $aid})-[:CONTRIBUTED_BY]->(o:Organization) "
    "RETURN o.name AS name"
)
ORGANISATION_NAMES = (
    "MATCH (o:Organization)<-[:CONTRIBUTED_BY]-(:Element) "
    "RETURN DISTINCT o.name AS name"
)


def _org_match():
    mode = getattr(config, "GRAPH_ORG_MATCH", "contains")
    if mode not in ("contains", "family"):
        raise ValueError("unknown GRAPH_ORG_MATCH %r; use contains or family"
                         % mode)
    return mode


def _member_order():
    mode = getattr(config, "GRAPH_MEMBER_ORDER", "storage")
    if mode not in ("storage", "query"):
        raise ValueError("unknown GRAPH_MEMBER_ORDER %r; use storage or query"
                         % mode)
    return mode


def _name_tokens(text):
    return frozenset(_WORD_RE.findall((text or "").lower()))


def _org_index():
    """Organisation names, their token sets, a token -> names index, and CAP."""
    global _ORG_INDEX
    if _ORG_INDEX is not None:
        return _ORG_INDEX
    with neo4j.session() as session:
        names = sorted({r["name"] for r in session.run(ORGANISATION_NAMES)
                        if r["name"]})
    tokens = {name: _name_tokens(name) for name in names}
    postings = defaultdict(set)
    for name, toks in tokens.items():
        for tok in toks:
            postings[tok].add(name)

    def family(toks):
        if not toks:
            return set()
        sets = sorted((postings.get(t, set()) for t in toks), key=len)
        out = set(sets[0])
        for other in sets[1:]:
            out &= other
        return out

    cap = max((len(family(toks)) for toks in tokens.values()), default=0)
    _ORG_INDEX = {"names": names, "tokens": tokens, "family": family, "cap": cap}
    print("[info] graph: organisation family index over %d names, CAP=%d"
          % (len(names), cap))
    return _ORG_INDEX


def organisation_family(entity):
    """(family, CAP): every organisation whose name tokens include all of the
    entity's, sorted, and the CAP of _org_index(). Read-only, and the family
    rule whatever GRAPH_ORG_MATCH says: authorship_organisations() uses it
    under `family`, and the grounded clarifying question (pipeline.py) uses it
    to tell a named publisher from a generic word such as "council"."""
    index = _org_index()
    return sorted(index["family"](_name_tokens(entity))), index["cap"]


def title_phrase_count(phrase):
    """How many records have `phrase` as a phrase in their title. Read-only
    (a count request); used only by the grounded clarifying question."""
    _ensure_index_corpus()
    res = os_client.count(index=config.INDEX_NAME,
                          body={"query": {"match_phrase": {"title": phrase}}})
    return int(res.get("count", 0))


# ---- Publisher resolution ---------------------------------------------------
# The family rule of _org_index(), applied to the catalogue's publisher list
# (ingest/build_publisher_lexicon.py: name -> record ids; for us the DCAT
# publishers as well as the organisations). Same tokens, same family, and a
# CAP read off the list the same way, once per process. Used only under
# ENTITY_RESOLVE; off, nothing here is called.
_PUBLISHER_INDEX = None


def _publisher_index():
    global _PUBLISHER_INDEX
    if _PUBLISHER_INDEX is not None:
        return _PUBLISHER_INDEX
    path = config.PUBLISHER_LEXICON_FILE
    if not os.path.exists(path):
        raise SystemExit("ENTITY_RESOLVE needs %s; build it with python "
                         "ingest/build_publisher_lexicon.py --corpus %s"
                         % (path, config.CORPUS))
    with open(path, encoding="utf-8") as f:
        blob = json.load(f)
    names = blob["names"]
    tokens = {n: _name_tokens(n) for n in names}
    sequences = {n: _WORD_RE.findall(n.lower()) for n in names}
    postings = defaultdict(set)
    for n, toks in tokens.items():
        for tok in toks:
            postings[tok].add(n)

    def family(toks):
        if not toks:
            return set()
        sets = sorted((postings.get(t, set()) for t in toks), key=len)
        out = set(sets[0])
        for other in sets[1:]:
            out &= other
        return out

    cap = max((len(family(t)) for t in tokens.values()), default=0)
    _PUBLISHER_INDEX = {"names": names, "tokens": tokens, "sequences": sequences,
                        "family": family, "cap": cap, "source": blob.get("source")}
    print("[info] publisher: lexicon of %d names (%s), CAP=%d"
          % (len(names), blob.get("source"), cap))
    return _PUBLISHER_INDEX


def _contiguous(needle, hay):
    n = len(needle)
    return any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def resolve_publisher(entity, question):
    """{status, source, names, ids, family_size, cap, entity}: the publisher
    the question names, if the catalogue's publisher list can tell.
    """
    index = _publisher_index()
    entity = (entity or "").strip()
    out = {"status": "none", "source": None, "names": [], "ids": [],
           "family_size": None, "cap": index["cap"], "entity": entity}
    if entity:
        fam = sorted(index["family"](_name_tokens(entity)))
        out["source"] = "entity"
        out["family_size"] = len(fam)
        if 1 <= len(fam) <= index["cap"]:
            ids = sorted({i for n in fam for i in index["names"][n]})
            out.update(status="resolved", names=fam, ids=ids)
            return out
        if len(fam) > index["cap"]:
            out.update(status="ambiguous", names=fam[:10])
            return out
    words = _WORD_RE.findall((question or "").lower())
    found = [n for n, seq in index["sequences"].items()
             if len(seq) >= 2 and _contiguous(seq, words)]
    if found:
        longest = max(len(index["sequences"][n]) for n in found)
        top = sorted(n for n in found if len(index["sequences"][n]) == longest)
        out["source"] = "scan"
        if len(top) == 1:
            out.update(status="resolved", names=top, ids=sorted(index["names"][top[0]]))
        else:
            out.update(names=top)
    return out


# ---- Publisher linking: the model picks the publisher -----------------------
# ENTITY_RESOLVE=link_path. Up to 20 candidates from the publisher list by
# fixed rules (resolve_publisher's family, initials, character trigrams, and
# trigrams of the model's guess at the full name), then one model call picks
# one of them or says it cannot. Results are cached per (entity, question)
# for the life of the process.
LINK_MAX_CANDIDATES = 20
LINK_TRIGRAM_TOP = 10
LINK_GUESS_TOP = 5
LINK_INITIAL_STOPWORDS = frozenset(("of", "the", "and", "for", "&"))
LINK_GUESS_SYSTEM = (
    "You help match a user's question about an open-data catalogue to the "
    "organisation that published the data. You are given the question and a "
    "phrase from it that may name or describe an organisation. Reply with "
    "that organisation's full official name as a government data catalogue "
    "would list it, on one line, with no explanation. If the phrase does not "
    "identify one specific organisation, reply NONE.")
LINK_SELECT_SYSTEM = (
    "You link a phrase in a user's question to one publisher of an open-data "
    "catalogue. You get the question, the phrase, and a numbered list of "
    "candidate publishers taken from the catalogue, each with its number of "
    "datasets and one example title. Choose a candidate only if the question "
    "clearly refers to that one organisation: by its name, an abbreviation, a "
    "misspelling or a description that fits only it. If the phrase is generic "
    "and could mean many publishers (for example \"the council\", \"a federal "
    "agency\", \"the national park\"), answer ambiguous. If no candidate is the "
    "organisation meant, answer none. Never name an organisation that is not "
    "in the list. Respond ONLY with JSON: {\"choice\": <candidate number or "
    "null>, \"status\": \"resolved\" | \"ambiguous\" | \"none\", \"reason\": "
    "\"<one sentence>\"}")
LINK_TRACE = {"llm_calls": 0, "parse_failures": 0, "cache_hits": 0}
_LINK_CACHE = {}
_PUBLISHER_TITLES = None
_TRIGRAM_RE = re.compile(r"[^a-z0-9 ]+")


def _trigrams(text):
    t = " ".join(_TRIGRAM_RE.sub("", (text or "").lower()).split())
    return {t[i:i + 3] for i in range(len(t) - 2)} if len(t) >= 3 else ({t} if t else set())


def _trigram_top(text, names, k):
    want = _trigrams(text)
    if not want:
        return []
    scored = []
    for n in names:
        have = _trigrams(n)
        union = len(want | have)
        s = len(want & have) / float(union) if union else 0.0
        if s > 0:
            scored.append((-s, n))
    return [n for _, n in sorted(scored)[:k]]


def _initials(name):
    words = [w for w in _WORD_RE.findall(name.lower()) if w not in LINK_INITIAL_STOPWORDS]
    return "".join(w[0] for w in words)


def _publisher_titles():
    """{id: title} of the indexed records, for the example title."""
    global _PUBLISHER_TITLES
    if _PUBLISHER_TITLES is None:
        _PUBLISHER_TITLES = {}
        with open(config.ELEMENTS_FILE, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    e = json.loads(line)
                    _PUBLISHER_TITLES[e["id"]] = e.get("title") or ""
    return _PUBLISHER_TITLES


def link_candidates(entity, question, guess):
    """The candidate list, in the order d (resolve_publisher), b (initials),
    a (trigrams of the entity), c (trigrams of the guess), at most
    LINK_MAX_CANDIDATES, each name once."""
    index = _publisher_index()
    names = index["names"]
    res = resolve_publisher(entity, question)
    if res["status"] == "ambiguous":
        d = sorted(index["family"](_name_tokens(entity)))
    else:
        d = list(res["names"]) if res["status"] == "resolved" else []
    words = _WORD_RE.findall(_TRIGRAM_RE.sub(" ", entity.lower()))
    b = []
    if len(words) == 1 and words[0].isalpha() and 2 <= len(words[0]) <= 6:
        b = sorted(n for n in names if _initials(n) == words[0])
    a = _trigram_top(entity, names, LINK_TRIGRAM_TOP)
    c = _trigram_top(guess, names, LINK_GUESS_TOP) if guess else []
    out = []
    for n in d + b + a + c:
        if n not in out:
            out.append(n)
    titles = _publisher_titles()
    return [{"name": n, "datasets": len(names[n]),
             "example": titles.get(min(names[n]), "") if names[n] else ""}
            for n in out[:LINK_MAX_CANDIDATES]]


def link_publisher(entity, question, plan=None):
    """{status, source, names, ids, candidates, guess, reason, llm_raw,
    family_size, cap, entity}. Called only under ENTITY_RESOLVE=link_*.
    """
    import pipeline                          # call_llm; only reached under link_*
    entity = (entity or "").strip()
    intent = str((plan or {}).get("graph_intent") or "")
    if not entity and intent == "AUTHORSHIP":
        entity = (question or "").strip()
    out = {"status": "none", "source": "link", "names": [], "ids": [],
           "candidates": [], "guess": None, "reason": None, "llm_raw": None,
           "family_size": None, "cap": _publisher_index()["cap"], "entity": entity,
           "triggered": bool(entity)}
    if not entity:
        return out
    key = (entity, question or "")
    if key in _LINK_CACHE:
        LINK_TRACE["cache_hits"] += 1
        return json.loads(json.dumps(_LINK_CACHE[key]))
    raw_g = pipeline.call_llm(LINK_GUESS_SYSTEM,
                              "Question: %s\nPhrase: %s" % (question, entity))
    LINK_TRACE["llm_calls"] += 1
    guess = (raw_g.splitlines()[0] if raw_g else "").strip().strip('"').strip()
    guess = None if not guess or guess.upper().rstrip(".") == "NONE" else guess
    cands = link_candidates(entity, question, guess)
    out.update(guess=guess, candidates=cands)
    if cands:
        listing = "\n".join('%d. %s (%d datasets; e.g. "%s")'
                            % (i + 1, c["name"], c["datasets"], c["example"][:100])
                            for i, c in enumerate(cands))
        raw = pipeline.call_llm(LINK_SELECT_SYSTEM,
                                "Question: %s\nPhrase: %s\nCandidates:\n%s"
                                % (question, entity, listing))
        LINK_TRACE["llm_calls"] += 1
        out["llm_raw"] = raw
        verdict = pipeline.parse_json(raw, None)
        status = verdict.get("status") if isinstance(verdict, dict) else None
        choice = verdict.get("choice") if isinstance(verdict, dict) else None
        ok = status in ("resolved", "ambiguous", "none")
        if ok and status == "resolved":
            ok = isinstance(choice, int) and not isinstance(choice, bool) \
                and 1 <= choice <= len(cands)
        if not ok:
            LINK_TRACE["parse_failures"] += 1
            out["reason"] = "unreadable or out-of-range answer"
        else:
            out["status"] = status
            out["reason"] = str(verdict.get("reason") or "")[:300]
            if status == "resolved":
                name = cands[choice - 1]["name"]
                out.update(names=[name], ids=sorted(_publisher_index()["names"][name]))
                for c in cands:
                    c["chosen"] = c["name"] == name
    _LINK_CACHE[key] = json.loads(json.dumps(out))
    return out


def publisher_search(ids, query, size=10, phrase="", expansion_terms=None):
    """The keyword path's query, restricted to `ids` (the `publisher` path).
    Same clauses as keyword_search(), plus an ids filter."""
    _ensure_index_corpus()
    should = ([_main_match(query)]
              + _phrase_clauses(phrase)
              + _expansion_clauses(expansion_terms))
    body = {
        "size": fetch_size(size),
        "_source": {"excludes": ["embedding"]},
        "query": {"bool": {"should": should, "minimum_should_match": 1,
                           "filter": [{"ids": {"values": list(ids)}}]}},
    }
    res = os_client.search(index=config.INDEX_NAME, body=body)
    return [to_doc(h) for h in res["hits"]["hits"]]


def authorship_organisations(entity):
    """(organisation names, reason) the AUTHORSHIP entity resolves to."""
    if _org_match() == "contains":
        with neo4j.session() as session:
            names = sorted({r["name"] for r in session.run(
                AUTHORSHIP_CONTAINS_ORGS, entity=entity)})
        return names, "contains"
    family, cap = organisation_family(entity)
    if not family:
        return [], "no organisation name contains every token"
    if len(family) > cap:
        return [], ("%d organisations match, more than the %d any "
                    "publisher name matches" % (len(family), cap))
    return family, "family"


def member_search(organisations, query, size, exclude_ids=()):
    """Members of `organisations`, ranked by BM25 on `query`, ties by _id."""
    _ensure_index_corpus()
    bool_query = {"filter": [{"terms": {"organization.raw": sorted(organisations)}}]}
    if query:
        bool_query["should"] = [_main_match(query, include_org=False)]
    if exclude_ids:
        bool_query["must_not"] = [{"ids": {"values": list(exclude_ids)}}]
    body = {
        "size": size,
        "_source": {"excludes": ["embedding"]},
        "sort": ["_score", {"_id": "asc"}],
        "query": {"bool": bool_query},
    }
    res = os_client.search(index=config.INDEX_NAME, body=body)
    return [to_doc(h) for h in res["hits"]["hits"]]


def _member_doc(doc, note):
    """A member found in OpenSearch, projected the way a graph result is."""
    out = node_to_doc(doc, note)
    if _hydrate_active():
        for field in _PROJECTED_FIELDS:
            out[field] = doc[field]
        out["hydration"] = "done"
    return out


def _vote_mode():
    return getattr(config, "RERANK_MODE", "off") == "vote"


def graph_search(intent, entity="", size=10, query=""):
    """`query` is the question the member intents rank by under
    GRAPH_MEMBER_ORDER=query; with the default settings it is not used. When
    it is empty the entity stands in for it.
    """
    docs = _graph_search(intent, entity, size, query)
    if _vote_mode():
        for doc in docs:
            doc["graph_relation"] = intent
            doc.setdefault("graph_weight", None)
    return docs


def _graph_search(intent, entity, size, query):
    docs = []
    member_order = _member_order()
    rank_text = query or entity

    if intent in ANCHORED_CATALOG:
        anchors = find_anchors(entity)
        if not anchors:
            print("[warn] graph: no anchor found for %r" % entity)
            return []
        if len(anchors) > 1:
            print("[info] graph: %d anchors for %r: %s"
                  % (len(anchors), entity,
                     "; ".join(t[:40] for _, t in anchors)))

        # One list per anchor, merged round-robin below: at top_n == 1 this
        # is the single list in its original order, unchanged.
        per_anchor = []
        limit = fetch_size(size)
        with neo4j.session() as session:
            for anchor_id, anchor_title in anchors:
                found = []
                if intent == "CO_CONTRIBUTION" and member_order == "query":
                    # The anchor fixes the organisation; OpenSearch
                    # orders its other members by the question.
                    orgs = sorted({r["name"] for r in session.run(
                        ANCHOR_ORGANISATION, aid=anchor_id) if r["name"]})
                    if not orgs:
                        print("[warn] graph: anchor '%s' has no organisation"
                              % anchor_title[:60])
                    for member in (member_search(orgs, rank_text, limit,
                                                 exclude_ids=[anchor_id])
                                   if orgs else []):
                        doc = _member_doc(member, (
                            "graph evidence: same contributor '%s' as '%s'"
                            % (member["organization"], anchor_title[:80])))
                        if getattr(config, "GRAPH_QUERY_REWRITE", False):
                            doc["graph_anchor"] = anchor_title
                        found.append(doc)
                    per_anchor.append((anchor_title, found))
                    continue
                for record in session.run(ANCHORED_CATALOG[intent],
                                          aid=anchor_id, limit=limit):
                    node = record["e"]
                    w = record["w"]
                    if intent == "PROVENANCE":
                        # An int weight is a shared-tag count; a float weight
                        # (EDGE_WEIGHT=idf) is an idf sum and says so, because
                        # the cross-encoder reads this text.
                        if isinstance(w, float):
                            desc = ("graph evidence: shares tags worth %.2f "
                                    "idf with '%s' via a RELATED_TO edge"
                                    % (w, anchor_title[:80]))
                        else:
                            desc = ("graph evidence: shares %s tags with '%s' "
                                    "via a RELATED_TO edge"
                                    % (w, anchor_title[:80]))
                    else:
                        desc = ("graph evidence: same contributor '%s' "
                                "as '%s'" % (w, anchor_title[:80]))
                    doc = node_to_doc(node, desc)
                    # The edge weight is what the 'graph' voice
                    # ranks by; written only in vote mode.
                    if _vote_mode():
                        doc["graph_weight"] = w if intent == "PROVENANCE" else None
                    # GRAPH_QUERY_REWRITE. Only written when the switch is on, so no other
                    # configuration's output gains a field.
                    if getattr(config, "GRAPH_QUERY_REWRITE", False):
                        doc["graph_anchor"] = anchor_title
                    found.append(doc)
                per_anchor.append((anchor_title, found))

        seen = set()
        for column in range(max((len(f) for _, f in per_anchor), default=0)):
            for _, found in per_anchor:
                if column >= len(found):
                    continue
                doc = found[column]
                if doc["id"] in seen:
                    continue
                seen.add(doc["id"])
                docs.append(doc)
                if len(docs) >= limit:
                    break
            if len(docs) >= limit:
                break

        if not docs:
            print("[warn] graph: anchor '%s' has no %s edges"
                  % (anchors[0][1], intent))
        return docs

    if intent == "AUTHORSHIP" and (member_order == "query"
                                   or _org_match() != "contains"):
        # Either switch set: resolve the organisations first, then fetch their
        # members in storage order (Cypher) or question order (OpenSearch).
        limit = fetch_size(size)
        orgs, reason = authorship_organisations(entity)
        if not orgs:
            print("[warn] graph: AUTHORSHIP entity %r not resolved: %s"
                  % (entity, reason))
            return []
        if member_order == "query":
            for member in member_search(orgs, rank_text, limit):
                docs.append(_member_doc(member, "graph evidence: intent=%s, %s"
                                        % (intent, member["organization"])))
        else:
            with neo4j.session() as session:
                for record in session.run(AUTHORSHIP_BY_NAMES, names=orgs,
                                          limit=limit):
                    desc = "graph evidence: intent=%s, %s" % (intent, record["w"])
                    docs.append(node_to_doc(record["e"], desc))
        if not docs:
            print("[warn] graph: %s returned nothing for %r" % (intent, entity))
        return docs

    if intent == "AGGREGATION" and member_order == "query":
        # The same five organisations as the default query -- ties at the fifth
        # place broken by name instead of by the store -- and their members
        # ordered by the question.
        limit = fetch_size(size)
        with neo4j.session() as session:
            counts = {r["name"]: r["n"] for r in session.run(AGGREGATION_ORGS_ORDERED)}
        for member in (member_search(list(counts), rank_text, limit) if counts else []):
            org = member["organization"]
            docs.append(_member_doc(member, "graph evidence: intent=%s, %s"
                                    % (intent, "%s publishes %s elements"
                                       % (org, counts.get(org)))))
        if not docs:
            print("[warn] graph: %s returned nothing for %r" % (intent, entity))
        return docs

    template = CYPHER_CATALOG.get(intent)
    if template is None:
        return []
    with neo4j.session() as session:
        for record in session.run(template, entity=entity,
                                 limit=fetch_size(size)):
            node = record["e"]
            desc = "graph evidence: intent=%s, %s" % (intent, record["w"])
            docs.append(node_to_doc(node, desc))
    if not docs:
        print("[warn] graph: %s returned nothing for %r" % (intent, entity))
    return docs


# ---- Candidate hydration (HYDRATE_CANDIDATES) ------------------------------
# Filled in by hydrate(); eval/run_eval.py records it per row when
# HYDRATE_CANDIDATES is on, so a failed or partial hydration is counted in the
# results file rather than inferred from a score.
HYDRATION_TRACE = {"calls": 0, "requested": 0, "hydrated": 0, "missing": 0,
                   "errors": 0}

# The fields to_doc() projects from an OpenSearch hit.
_PROJECTED_FIELDS = ("title", "description", "type", "tags", "organization",
                     "url")


def reset_hydration_trace():
    for key in HYDRATION_TRACE:
        HYDRATION_TRACE[key] = 0


def take_hydration_trace():
    snapshot = dict(HYDRATION_TRACE)
    reset_hydration_trace()
    return snapshot


def hydrate(docs):
    """Replace graph-only projections with the document's real metadata."""
    _ensure_index_corpus()
    pending = [doc for doc in docs if doc.get("hydration") == "pending"]
    if not pending:
        return docs
    ids = []
    for doc in pending:
        if doc["id"] not in ids:
            ids.append(doc["id"])
    HYDRATION_TRACE["calls"] += 1
    HYDRATION_TRACE["requested"] += len(ids)
    try:
        res = os_client.mget(index=config.INDEX_NAME, body={"ids": ids},
                             _source_excludes="embedding")
        found = {hit["_id"]: to_doc(hit) for hit in res.get("docs", [])
                 if hit.get("found")}
    except Exception as exc:                                  # noqa: BLE001
        print("[warn] hydrate: mget failed (%r); %d documents keep their "
              "graph-only projection" % (exc, len(ids)))
        for doc in pending:
            doc["hydration"] = "error"
        HYDRATION_TRACE["errors"] += len(pending)
        return docs
    missing = 0
    for doc in pending:
        projection = found.get(doc["id"])
        if projection is None:
            doc["hydration"] = "missing"
            missing += 1
            continue
        for field in _PROJECTED_FIELDS:
            doc[field] = projection[field]
        doc["hydration"] = "done"
        HYDRATION_TRACE["hydrated"] += 1
    if missing:
        HYDRATION_TRACE["missing"] += missing
        print("[warn] hydrate: %d of %d graph results are not in index %s; "
              "they keep their graph-only projection"
              % (missing, len(pending), config.INDEX_NAME))
    return docs


def _merge_graph_weight(kept, other):
    """A document linked to several anchors (d_agent rounds, or
    GRAPH_ANCHOR_TOPN > 1) keeps its strongest edge. Only vote mode writes
    the field, so in every other mode this does nothing."""
    if "graph_weight" not in other:
        return
    a, b = kept.get("graph_weight"), other.get("graph_weight")
    if "graph_weight" not in kept or a is None or (b is not None and b > a):
        kept["graph_weight"] = b


def _keep_best(ranks, key, rank):
    if key not in ranks or rank < ranks[key]:
        ranks[key] = rank


def _merge_graph_fields(kept, other):
    """Carry what only a later projection of the same document knows."""
    for field in ("graph_note", "graph_anchor", "graph_relation"):
        if other.get(field) and not kept.get(field):
            kept[field] = other[field]
    _merge_graph_weight(kept, other)
    if kept.get("hydration") in ("pending", "missing", "error") \
            and other.get("hydration") in (None, "done"):
        for field in _PROJECTED_FIELDS:
            kept[field] = other[field]
        kept["hydration"] = "done"


# ---- RRF fusion ------------------------------------------------------------
def rrf_fuse(rank_lists, limit=None):
    """rank_lists = {"keyword": [doc, ...], "spatial": [doc, ...], ...}"""
    scores = {}
    registry = {}
    from_methods = defaultdict(set)
    hydrate_on = _hydrate_active()
    path_ranks = defaultdict(dict)
    round_ranks = defaultdict(dict)

    for method, docs in rank_lists.items():
        for rank, doc in enumerate(docs):
            doc_id = doc["id"]
            previous = registry.get(doc_id)
            if not hydrate_on:
                # The same document can arrive on several paths and the last
                # projection wins the registry slot.
                if previous is not None and previous.get("graph_anchor") \
                        and not doc.get("graph_anchor"):
                    doc["graph_anchor"] = previous["graph_anchor"]
                # Same reason: the vote fields must survive a later
                # projection that lacks them. Only vote mode writes them.
                if previous is not None and previous is not doc:
                    if previous.get("graph_relation") and not doc.get("graph_relation"):
                        doc["graph_relation"] = previous["graph_relation"]
                    _merge_graph_weight(doc, previous)
                registry[doc_id] = doc
            else:
                if previous is None:
                    registry[doc_id] = doc
                elif previous is not doc:
                    _merge_graph_fields(previous, doc)
                inner = doc.get("path_ranks")
                if inner is None:
                    _keep_best(path_ranks[doc_id], method, rank)
                else:
                    for path, inner_rank in inner.items():
                        _keep_best(path_ranks[doc_id], path, inner_rank)
                    for name, inner_rank in (doc.get("round_ranks") or {}).items():
                        _keep_best(round_ranks[doc_id], name, inner_rank)
                    _keep_best(round_ranks[doc_id], method, rank)
            from_methods[doc_id].add(method)
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (config.RRF_K + rank + 1)

    limit = limit or candidate_pool()
    ranked_ids = sorted(scores, key=scores.get, reverse=True)
    result = []
    seen_titles = set()
    dedup = getattr(config, "DEDUP_BY_TITLE", False)
    for doc_id in ranked_ids:
        doc = registry[doc_id]
        if dedup:
            key = _norm_title(doc.get("title"))
            if key and key in seen_titles:
                continue
            if key:
                seen_titles.add(key)
        doc["rrf_score"] = round(scores[doc_id], 5)
        doc["from_methods"] = sorted(from_methods[doc_id])
        if hydrate_on:
            doc["path_ranks"] = dict(path_ranks[doc_id])
            if round_ranks[doc_id]:
                doc["round_ranks"] = dict(round_ranks[doc_id])
        result.append(doc)
        if len(result) >= limit:
            break
    if hydrate_on:
        hydrate(result)
    return result


def corpus_size():
    _ensure_index_corpus()
    try:
        return os_client.count(index=config.INDEX_NAME)["count"]
    except Exception:
        return -1


__all__ = [
    "keyword_search", "semantic_search", "spatial_search", "graph_search",
    "rrf_fuse", "finalise", "dedup_by_title", "candidate_pool", "fetch_size",
    "normalise_place", "corpus_size", "BBOXES", "place_resolution_available",
    "find_anchor", "find_anchors", "node_to_doc",
    "authorship_organisations", "organisation_family", "title_phrase_count",
    "resolve_publisher", "publisher_search", "link_publisher", "link_candidates",
    "member_search",
    "hydrate", "HYDRATION_TRACE", "reset_hydration_trace",
    "take_hydration_trace",
    "CYPHER_CATALOG", "ANCHORED_CATALOG",
]
