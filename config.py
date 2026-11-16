"""Configuration."""
import os

import corpora

# ---- Corpus ---------------------------------------------------------------
# One catalogue per process. CORPUS_SPEC.corpus / .language / .places are the
# three modules under corpora/<CORPUS>/; PLACES is None when the corpus
# provides no place resolution, which switches the spatial path off.
CORPUS = corpora.selected_name()
CORPUS_SPEC = corpora.load(CORPUS)
LANGUAGE = CORPUS_SPEC.language
PLACES = CORPUS_SPEC.places
_corpus = CORPUS_SPEC.corpus

# ---- Backends -------------------------------------------------------------
OPENSEARCH_URL = "http://localhost:9200"
# Per corpus: build_indexes.py clears the whole graph before building, so two
# corpora must not share one Neo4j server (see docker-compose.yml).
NEO4J_URI = _corpus.NEO4J_URI
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "change-me")

# LLM (Ollama's OpenAI-compatible endpoint)
LLM_BASE_URL = "http://localhost:11434/v1"
LLM_MODEL = "qwen2.5:7b-instruct"
LLM_API_KEY = "ollama"

# Embeddings
EMBED_MODEL = LANGUAGE.EMBED_MODEL
DEVICE = "cpu"
EMBED_BATCH = 64

# ---- Files / index --------------------------------------------------------
# Which OpenSearch index to search. The env files name the index that was
# built with the English subfields and the full embedded text.
INDEX_NAME = os.getenv("INDEX_NAME", _corpus.INDEX_NAME)
RAW_FILE = _corpus.RAW_FILE
ELEMENTS_FILE = _corpus.ELEMENTS_FILE
HARVEST_DONE_FILE = _corpus.HARVEST_DONE_FILE
# Where this corpus's data and evaluation files live: au uses "data" and
# "eval", the other corpora data/<name>/ and eval/<name>/.
DATA_DIR = _corpus.DATA_DIR
EVAL_DIR = _corpus.EVAL_DIR

# ---- Harvest (ingest/fetch_ckan.py) --------------------------------------
CKAN_API = _corpus.CKAN_API
DATASET_URL_PREFIX = _corpus.DATASET_URL_PREFIX
# None = DATASET_URL_PREFIX + package name. A str.format template over {id}
# and {name} otherwise.
DATASET_URL_TEMPLATE = _corpus.DATASET_URL_TEMPLATE
# Top-level package fields first, then extras keys: the two live in different
# places in a CKAN package.
CKAN_SPATIAL_KEYS = _corpus.CKAN_SPATIAL_KEYS
CKAN_SPATIAL_EXTRAS_KEYS = _corpus.CKAN_SPATIAL_EXTRAS_KEYS
HARVEST_TOPICS = _corpus.HARVEST_TOPICS
HARVEST_ROWS = _corpus.HARVEST_ROWS
DEEP_PAGE_LIMIT = _corpus.DEEP_PAGE_LIMIT
HARVEST_MIN_INTERVAL = _corpus.HARVEST_MIN_INTERVAL
HARVEST_SLEEP = _corpus.HARVEST_SLEEP
HARVEST_TIMEOUT = _corpus.HARVEST_TIMEOUT
HARVEST_RETRIES = _corpus.HARVEST_RETRIES
HARVEST_COOLDOWN = _corpus.HARVEST_COOLDOWN
HARVEST_FAIL_STREAK = _corpus.HARVEST_FAIL_STREAK
HARVEST_LONG_COOLDOWN = _corpus.HARVEST_LONG_COOLDOWN
LIST_PAGE_SIZE = _corpus.LIST_PAGE_SIZE
LIST_MAX_OFFSET = _corpus.LIST_MAX_OFFSET

# ---- Sampling (ingest/sample_elements.py) --------------------------------
SAMPLE_SEED = _corpus.SAMPLE_SEED
SAMPLE_SIZE = _corpus.SAMPLE_SIZE
SAMPLE_GEO_FRACTION = _corpus.SAMPLE_GEO_FRACTION
SAMPLE_FOCUS = _corpus.SAMPLE_FOCUS
SAMPLE_FOCUS_N = _corpus.SAMPLE_FOCUS_N

# ---- Vocabulary (ingest/build_vocabulary.py) -----------------------------
VOCAB_STOP_EXTRA = _corpus.VOCAB_STOP_EXTRA

# ---- Prompt fragments (pipeline.py, rewrite.py) --------------------------
# The words that name the catalogue inside the English prompt templates. See
# corpora/au/corpus.py for where each one is inserted.
PROMPT_ROUTER_DOMAIN = _corpus.PROMPT_ROUTER_DOMAIN
PROMPT_REASON_DOMAIN = _corpus.PROMPT_REASON_DOMAIN
PROMPT_GEN_DOMAIN = _corpus.PROMPT_GEN_DOMAIN
PROMPT_REWRITE_DOMAIN = _corpus.PROMPT_REWRITE_DOMAIN

# ---- Graph derivation (ingest/build_indexes.py) --------------------------
# Two elements are linked RELATED_TO when they share at least this many tags.
MIN_SHARED_TAGS = int(os.getenv("MIN_SHARED_TAGS", str(_corpus.MIN_SHARED_TAGS)))
# How a RELATED_TO edge is weighted, which decides both the order of the degree
# cap at build time and the order of the PROVENANCE query at search time.
EDGE_WEIGHT = os.getenv("EDGE_WEIGHT", _corpus.EDGE_WEIGHT).lower()
MAX_TAG_DF = _corpus.MAX_TAG_DF
MAX_EDGES_PER_ELEMENT = _corpus.MAX_EDGES_PER_ELEMENT
MAX_CANDIDATE_PAIRS = _corpus.MAX_CANDIDATE_PAIRS
NEO4J_BATCH = 1000
OS_BULK_CHUNK = 500

# ---- Pipeline ------------------------------------------------------------
# At most four search rounds per question.
MAX_ITERATIONS = 4
MAX_REGENERATIONS = 3
# Reciprocal rank fusion constant.
RRF_K = int(os.getenv("RRF_K", "60"))
TOP_K = 10

# ---- Determinism ---------------------------------------------------------
LLM_TEMPERATURE = 0.0
LLM_REGEN_TEMPERATURE = 0.0

# Seed sent with every LLM request. Set LLM_SEED= (empty) to send no seed.
LLM_SEED = int(os.environ["LLM_SEED"]) if os.getenv("LLM_SEED") else (
    None if "LLM_SEED" in os.environ else 42)

# ---- Prompt size ---------------------------------------------------------
GEN_DOCS = 12
JUDGE_DOCS = 10
DESC_CHARS = 200

# ---- Validation policy ---------------------------------------------------
GROUNDING_LLM_BLOCKS = False

# How strict the citation check is about a cited id sitting next to the wrong
# name (pipeline.citation_problems, the message "the id ... belongs to '...',
# not to the element named beside it").
CITE_CHECK = os.getenv("CITE_CHECK", "strict").lower()
if CITE_CHECK not in ("strict", "lenient"):
    raise ValueError("CITE_CHECK must be strict or lenient, got %r" % CITE_CHECK)

# ---- Query rewriting mode ------------------------------------------------
#   c1   -- the question is rewritten once per retrieval round into metadata
#           phrasing and sent to both the keyword and the semantic path
#           (default; rewrite.py).
#   c2   -- c1, plus the decision loop may restate the question.
#   off, a, b, ab -- no rewriting; a and b add corpus-vocabulary terms and
#           gazetteer place terms (expansion.py). Not used by the evaluated
#           configuration.
EXPANSION_MODE = os.getenv("EXPANSION_MODE", "c1").lower()

# Older switch name: EXPANSION_ENABLE=1 maps to mode 'ab'.
if os.getenv("EXPANSION_ENABLE") == "1" and EXPANSION_MODE == "off":
    EXPANSION_MODE = "ab"

# Corpus vocabulary (expansion.py, and the rewrite guards in rewrite.py)
VOCAB_INDEX_FILE = _corpus.VOCAB_INDEX_FILE
EXPANSION_TOP_K = 8
EXPANSION_MIN_SIM = 0.55
EXPANSION_KEYWORD_BOOST = 0.5

# Optional place gazetteer (off in the paper configuration); None when the corpus
# provides no place resolution.
GAZETTEER_FILE = os.getenv(
    "GAZETTEER_FILE", PLACES.GAZETTEER_FILE if PLACES is not None else None)

# ---- Spatial topic gate --------------------------------------------------
# The spatial path builds a bool query with a geo_shape filter and a set of
# topical should clauses. OpenSearch defaults minimum_should_match to 0
# whenever a filter is present, so the should clauses would only score.
# On (default): "inside this box" AND "shares at least one query word".
SPATIAL_TOPIC_GATE = os.getenv("SPATIAL_TOPIC_GATE", "1").lower() \
    in ("1", "on", "true", "yes")

# ---- Rewriting (c1 / c2) -------------------------------------------------
# Rewriting happens after routing, so place and entity extraction always see
# the original question. A rewrite that loses a place or entity name is
# discarded by a deterministic string check.
REWRITE_MAX_WORDS = 24

# ---- Candidate depth before fusion ---------------------------------------
# How many candidates each path inside pipeline.retrieve() returns before RRF.
# Default TOP_K. The fused output is still cut to TOP_K.
FUSION_DEPTH = int(os.getenv("FUSION_DEPTH", str(TOP_K)))

# ---- Rewrite guard -------------------------------------------------------
#   strict -- a rewrite is kept only if every must_keep string from the
#             router (place, entity) appears in it verbatim (default).
#   token  -- only the identity-bearing tokens are required: capitalised in
#             the router's output, or absent from the corpus vocabulary.
REWRITE_GUARD = os.getenv("REWRITE_GUARD", "strict").lower()

# ---- Rewrite gate --------------------------------------------------------
# The question is rewritten only when it shows a property rewriting fixes:
#   (a) distorted -- a token the corpus has never seen (misspelling) or a
#       non-Latin script;
#   (b) interrogative -- a sentence rather than a noun phrase: it ends in a
#       question mark or opens with a wh-word, auxiliary or request verb.
# A noun-phrase query is sent unchanged.
#   question_type -- the gate above (default).
#   off           -- every question is rewritten.
REWRITE_GATE = os.getenv("REWRITE_GATE", "question_type").lower()

# Token document frequencies over the corpus full text, built by
# ingest/build_token_df.py. Used only by the gate's distortion clause.
TOKEN_DF_FILE = _corpus.TOKEN_DF_FILE


# ==========================================================================
# Retrieval switches. Each defaults to its plain behaviour; the env files
# set the ones the evaluated configuration uses.
# ==========================================================================

# ---- BM25 candidate selectivity ------------------------------------------
# minimum_should_match for the main keyword query, e.g. "2<70%".
# Empty string = off (default).
BM25_MIN_SHOULD_MATCH = os.getenv("BM25_MIN_SHOULD_MATCH", "")

# ---- bge query instruction -----------------------------------------------
# BAAI/bge-*-en-v1.5 encodes documents bare and queries behind a fixed
# instruction. On: the query side gets the instruction (no reindex needed).
BGE_QUERY_PREFIX = LANGUAGE.EMBED_QUERY_INSTRUCTION
EMBED_QUERY_PREFIX = os.getenv("EMBED_QUERY_PREFIX", "0").lower() \
    in ("1", "on", "true", "yes")

# ---- Candidate depth -----------------------------------------------------
# How many candidates each retrieval path returns, and how many fused
# documents survive to the final cut. 0 = auto: TOP_K when no re-ranker is
# active, RERANK_POOL when one is.
CANDIDATE_POOL = int(os.getenv("CANDIDATE_POOL", "0"))

# ---- Collapse identical titles in the ranking ----------------------------
# Ten slots spent on four copies of one report is four wasted slots. With a
# candidate pool deeper than TOP_K the freed slots are filled from the pool.
DEDUP_BY_TITLE = os.getenv("DEDUP_BY_TITLE", "0").lower() \
    in ("1", "on", "true", "yes")

# ---- English-analysed subfields ------------------------------------------
# The index built with --english-subfields has a `.en` subfield analysed with
# the english analyzer ("roads" matches "road"); this switch makes the query
# use it.
USE_EN_SUBFIELDS = os.getenv("USE_EN_SUBFIELDS", "0").lower() \
    in ("1", "on", "true", "yes")

# ---- Re-ranking ----------------------------------------------------------
# 'off' cuts the fused list. 'cross' reorders the fused pool by a
# cross-encoder (BAAI/bge-reranker-base) and keeps the fused order as the
# tie-break. 'vote' merges the cross-encoder's ranking with the voices in
# RERANK_VOICES by RRF; with RERANK_VOICES=cross it is 'cross' exactly.
RERANK_MODE = os.getenv("RERANK_MODE", "off").lower()
RERANK_MODEL = os.getenv("RERANK_MODEL", LANGUAGE.RERANK_MODEL)
# 'cpu', so the re-ranker does not take a CUDA context before Ollama has the
# generator loaded.
RERANK_DEVICE = os.getenv("RERANK_DEVICE", "cpu")
# Re-ranking pool: the top 50 of the fused list.
RERANK_POOL = int(os.getenv("RERANK_POOL", "50"))
RERANK_BATCH = int(os.getenv("RERANK_BATCH", "32"))
RERANK_DESC_CHARS = int(os.getenv("RERANK_DESC_CHARS", "400"))
RERANK_MAX_LEN = int(os.getenv("RERANK_MAX_LEN", "512"))

# ---- Extra text on the cross-encoder's document side ---------------------
# 'append' ends rerank.doc_text() with "Users might search: q1; q2; ...", the
# queries read by document id from DOC_EXTRA_FILE. 'off' (default): nothing
# is appended. Not used by the evaluated configuration.
DOC_EXTRA_MODE = os.getenv("DOC_EXTRA_MODE", "off").lower()
DOC_EXTRA_FILE = os.getenv("DOC_EXTRA_FILE", "")


# ==========================================================================
# Place and graph switches. Same rule: every default is the plain behaviour.
# ==========================================================================

# ---- Place resolution ----------------------------------------------------
#   bboxes    -- the corpus's hand-checked BBOXES only (default).
#   gazetteer -- the gazetteer is consulted as well.
PLACE_RESOLVER = os.getenv("PLACE_RESOLVER", "bboxes").lower()

# ---- The spatial path's candidate set ------------------------------------
#   off -- geo_shape filter only (default).
#   df  -- geometry OR a match on the place name, but only when the name is
#          rare enough in the corpus to be selective (SPATIAL_NAME_DF_MAX).
#   all -- geometry OR name match, ungated.
SPATIAL_NAME_FALLBACK = os.getenv("SPATIAL_NAME_FALLBACK", "off").lower()
# A place phrase is "selective" when its rarest token appears in fewer than
# this fraction of corpus documents.
SPATIAL_NAME_DF_MAX = float(os.getenv("SPATIAL_NAME_DF_MAX", "0.05"))

# ---- Graph anchors -------------------------------------------------------
# How many anchors a relational query gets. At 1 the anchor query is issued
# unordered; above 1 the anchors are taken shortest title first (id as
# tie-break) and their neighbourhoods are merged round-robin.
GRAPH_ANCHOR_TOPN = int(os.getenv("GRAPH_ANCHOR_TOPN", "1"))

# ---- What the cross-encoder is asked about a graph result ----------------
# On: documents the graph path returned for an anchored intent (PROVENANCE,
# CO_CONTRIBUTION) are scored against the question plus a statement of the
# relation they were retrieved for. Off (default): the question alone.
GRAPH_QUERY_REWRITE = os.getenv("GRAPH_QUERY_REWRITE", "0").lower() \
    in ("1", "on", "true", "yes")

# ---- Administrative tail words in the router's place string --------------
# On: extra lookup keys without words such as "Shire" are tried after the
# normalised one, so a resolution can be added but never moved.
PLACE_NORMALISE = os.getenv("PLACE_NORMALISE", "off").lower()

# ---- What the spatial path means by "in this place" ----------------------
#   intersects -- the footprint touches the place box (default).
#   within     -- the footprint lies inside the place box.
SPATIAL_RELATION = os.getenv("SPATIAL_RELATION", "intersects").lower()


# ==========================================================================
# Candidate metadata and graph members.
# ==========================================================================

# ---- Graph results and real metadata -------------------------------------
# Neo4j nodes hold only title, type, url and organization, so a graph result
# carries the relation text in its description slot.
#   0 -- the fused list keeps the last projection of a document.
#   1 -- rrf_fuse() keeps the first projection, records each document's rank
#        in every path (path_ranks), fetches real metadata for graph-only
#        documents with one mget, and the relation text moves to graph_note.
HYDRATE_CANDIDATES = os.getenv("HYDRATE_CANDIDATES", "0").lower() \
    in ("1", "on", "true", "yes")

# ---- Which text the cross-encoder scores ---------------------------------
#   original -- always the original question (default).
#   auto     -- the accepted rewrite, but only when the rewrite gate opened
#               because of a foreign script and the rewrite passed every
#               guard.
RERANK_QUERY = os.getenv("RERANK_QUERY", "original").lower()

# ---- Member graph intents ------------------------------------------------
#   GRAPH_ORG_MATCH     contains -- toLower(name) CONTAINS entity (default).
#                       family   -- AUTHORSHIP only: organisations whose name
#                                   tokens include every entity token; none
#                                   when that family exceeds the largest
#                                   family of any real publisher name.
#   GRAPH_MEMBER_ORDER  storage  -- Cypher, store order (default).
#                       query    -- members from OpenSearch filtered on
#                                   organization.raw, BM25 on the question,
#                                   _id tie-break; AGGREGATION's top five
#                                   organisations get a name tie-break.
GRAPH_ORG_MATCH = os.getenv("GRAPH_ORG_MATCH", "contains").lower()
GRAPH_MEMBER_ORDER = os.getenv("GRAPH_MEMBER_ORDER", "storage").lower()


# ==========================================================================
# RERANK_MODE=vote. Read only when RERANK_MODE is 'vote'.
# ==========================================================================

# ---- Which rankings vote -------------------------------------------------
# Comma-separated. 'cross' (the cross-encoder) is required and covers the
# whole pool. 'graph' ranks the results of the anchored graph intents
# (PROVENANCE, CO_CONTRIBUTION) by edge weight: the one relation the
# cross-encoder cannot read, because it is not in the candidate's own text.
# 'fusion' is the incoming RRF order. A voice with nothing to rank on a row is
# skipped on that row, and a row left with 'cross' alone is ranked exactly as
# RERANK_MODE=cross.
RERANK_VOICES = os.getenv("RERANK_VOICES", "cross")

# ---- How a document absent from a voice is scored ------------------------
#   none -- it gets nothing from that voice (default).
#   tail -- it is scored as if tied at the end of that voice.
# Dense voices ('cross', 'fusion') have no absent documents, so this only
# affects 'graph'.
VOTE_ABSENT_RANK = os.getenv("VOTE_ABSENT_RANK", "none").lower()

# ---- Which search rounds the graph voice listens to ----------------------
#   all   -- every round's graph results are voice members (default).
#   first -- only round 1's graph results are members.
GRAPH_VOICE_ROUNDS = os.getenv("GRAPH_VOICE_ROUNDS", "all").lower()

# ---- Hypothetical-document query vector ----------------------------------
# On: an LLM writes the record that would answer the question and its vector
# is averaged with the question's, on the semantic path only. Off (default).
# Not used by the evaluated configuration.
HYDE = os.getenv("HYDE", "0").lower() in ("1", "on", "true", "yes")
#   gated  -- only questions whose rewrite gate opened.
#   always -- every question.
HYDE_WHEN = os.getenv("HYDE_WHEN", "gated").lower()
HYDE_CACHE_FILE = os.getenv("HYDE_CACHE_FILE", os.path.join("cache", "hyde_cache.jsonl"))

# ---- Clarifying questions ------------------------------------------------
# ASK_MODE=on lets the system ask the user one clarifying question in round 1,
# and only when the caller of pipeline.run() passes an ask_fn to answer it.
# Off (default): no question is asked.
ASK_MODE = os.getenv("ASK_MODE", "off").lower()
if ASK_MODE not in ("off", "on"):
    raise ValueError("ASK_MODE must be off or on, got %r" % ASK_MODE)
# Clarifying questions per question. An ask never uses up a search round.
ASK_MAX = int(os.getenv("ASK_MAX", "1"))
# How the ask decision is made when ASK_MODE=on.
ASK_STYLE = os.getenv("ASK_STYLE", "inline").lower()
if ASK_STYLE not in ("inline", "separate", "slot", "grounded"):
    raise ValueError("ASK_STYLE must be inline, separate, slot or grounded, "
                     "got %r" % ASK_STYLE)
# ---- Publisher linking ---------------------------------------------------
#   link_path -- a rule collects up to 20 candidate publishers from the
#                catalogue's publisher list (the lexicon of
#                ingest/build_publisher_lexicon.py), the model picks one or
#                none (retrieval.link_publisher), and a fifth retrieval path
#                searches that publisher's records (pipeline.retrieve).
#   path, voice, both, link_both -- other variants (word-set rule instead of
#                the model; a publisher voice in the re-ranker). Not used by
#                the evaluated configuration.
# off (default): nothing is resolved, read or changed.
ENTITY_RESOLVE = os.getenv("ENTITY_RESOLVE", "off").lower()
if ENTITY_RESOLVE not in ("off", "path", "voice", "both", "link_path", "link_both"):
    raise ValueError("ENTITY_RESOLVE must be off, path, voice, both, link_path "
                     "or link_both, got %r" % ENTITY_RESOLVE)
PUBLISHER_SOURCE = getattr(_corpus, "PUBLISHER_SOURCE", "organization")
PUBLISHER_LEXICON_FILE = os.path.join(DATA_DIR, "publisher_lexicon.json")

# Who answers a clarifying question in evaluation (user_sim.py).
USER_SIM = os.getenv("USER_SIM", "template").lower()
# What a user (or the simulator) says when there is nothing to add.
NULL_REPLY = "No, nothing more specific."
if USER_SIM not in ("template", "choice"):
    raise ValueError("USER_SIM=%r is not implemented: only 'template' and "
                     "'choice' exist" % USER_SIM)

# ---- Evaluation records (eval/run_eval.py only) ---------------------------
# EVAL_GENERATE=1: keep the generated answer and the ids it cites, and run the
# same generation code (pipeline.answer_stage) after c_routed, which otherwise
# stops at the ranking. EVAL_RECORD_POOL=1: keep the list handed to
# finalise() and each round's routed methods. Both default off.
EVAL_GENERATE = os.getenv("EVAL_GENERATE", "0").lower() in ("1", "on", "true", "yes")
EVAL_RECORD_POOL = os.getenv("EVAL_RECORD_POOL", "0").lower() in ("1", "on", "true", "yes")
# EVAL_RECORD_POOL_METHODS=1: on c_routed rows, keep which retrieval paths
# each document of the merged pool came from. Recording only. Default off.
EVAL_RECORD_POOL_METHODS = os.getenv("EVAL_RECORD_POOL_METHODS", "0").lower() in ("1", "on", "true", "yes")
