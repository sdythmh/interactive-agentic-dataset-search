"""data.gov (United States): files, index, backends, harvest, sampling, graph
build and prompt fragments.
"""

NAME = "us"

# ---- Backends --------------------------------------------------------------
# OpenSearch indexes are namespaced by name, so us shares the node with au and
# uk. COPIED naming pattern (knowledge-elements, knowledge-elements-uk).
INDEX_NAME = "knowledge-elements-us"
# Its own Neo4j, as for uk (build_indexes.py clears the whole graph before
# building): the neo4j-us service in docker-compose.yml, host ports 7476 (HTTP)
# / 7689 (bolt).
NEO4J_URI = "bolt://localhost:7689"

# ---- Files -----------------------------------------------------------------
# uk and us use data/<name>/ and eval/<name>/.
DATA_DIR = "data/us"
EVAL_DIR = "eval/us"
RAW_FILE = "data/us/elements_all.jsonl"      # fetch_catalog_api.py --merge output
# Optional. Where a record's publisher name comes from for the publisher
# lexicon (ingest/build_publisher_lexicon.py, retrieval.resolve_publisher).
PUBLISHER_SOURCE = "dcat.publisher.name"
ELEMENTS_FILE = "data/us/elements.jsonl"     # sample_elements.py output
HARVEST_DONE_FILE = "data/us/.harvest_done"  # fetch_catalog_api.py --resume log
VOCAB_INDEX_FILE = "data/us/vocab_index.npz" # build_vocabulary.py output
TOKEN_DF_FILE = "data/us/token_df.json"      # build_token_df.py output

# ---- Harvest: the Catalog API (read by ingest/fetch_catalog_api.py only) ---
# These are the fields the fetcher reads. corpora/__init__.py checks only
# that REQUIRED_CORPUS_FIELDS exist, so extra names are accepted; the fetcher
# reads them through config.CORPUS_SPEC.corpus, not through config.
#
# MEASURED (probe A1, A3): the documented base; paths /organizations (the
# documented /api/organizations returns 404), /search, /keywords.
CATALOG_API_BASE = "https://api.gsa.gov/technology/datagov/v4"
# MEASURED (probe A3): per_page 1000 returned 1000 records (6.3 MB); 5000 was
# rejected with HTTP 400 "per_page must be between 1 and 1000".
CATALOG_PER_PAGE = 1000
# Documented personal-key limit is 1000 requests per hour (probe, X-RateLimit
# headers read 999 at the start).
CATALOG_HOURLY_LIMIT = 900
# MEASURED (probe B/A): the limit is advertised in X-RateLimit-Remaining. Below
# this the fetcher pauses. COPIED from the task brief.
CATALOG_LOW_QUOTA = 50
CATALOG_PAUSE_SECONDS = 300    # sleep between re-checks while quota is low
# >= 1 s between requests (COPIED from uk's HARVEST_MIN_INTERVAL, which is also
# what the probe ran with).
CATALOG_MIN_INTERVAL = 1.0
CATALOG_TIMEOUT = 120          # COPIED from HARVEST_TIMEOUT
CATALOG_RETRIES = 6            # COPIED from HARVEST_RETRIES; 5xx and timeouts only

# ---- Harvest: CKAN fields the loader requires, NOT USED by us --------------
# us does not use these; the new fetcher does not read them. They exist because
# corpora/__init__.py requires them. The values are the uk ones, except where
# the old endpoint is named.
#
# MEASURED (probe B): catalog.data.gov/api/3/action/* answers 404, so this
# would fail if ingest/fetch_ckan.py were run under CORPUS=us, which is the
# intent: it is the wrong fetcher.
CKAN_API = "https://catalog.data.gov/api/3"
# MEASURED (probe A7): HEAD https://catalog.data.gov/dataset/<slug> answered 200
# with no redirect on 3 slugs, so the plain prefix + slug form is the page.
DATASET_URL_PREFIX = "https://catalog.data.gov/dataset/"
DATASET_URL_TEMPLATE = None
# The Catalog API carries the footprint in `spatial_shape` and `dcat.spatial`,
# not in CKAN package fields; the new fetcher reads those itself.
CKAN_SPATIAL_KEYS = ()
CKAN_SPATIAL_EXTRAS_KEYS = ()
HARVEST_TOPICS = []
HARVEST_ROWS = 200
DEEP_PAGE_LIMIT = 9000
HARVEST_MIN_INTERVAL = 1.0
HARVEST_SLEEP = 0.0
HARVEST_TIMEOUT = 120
HARVEST_RETRIES = 6
HARVEST_COOLDOWN = 30
HARVEST_FAIL_STREAK = 3
HARVEST_LONG_COOLDOWN = 300
LIST_PAGE_SIZE = 200
LIST_MAX_OFFSET = 60000

# ---- Sampling (ingest/sample_elements.py) ----------------------------------
# COPIED from uk, and kept after the whole-harvest statistics were read.
# The sample has footprints
# on 31.4% of its elements against 74.1% of the harvest; the difference comes
# from the round-robin over organizations, not from a footprint quota.
# Same size and seed as au and uk, so corpus scale is not a variable.
SAMPLE_SEED = 42
SAMPLE_SIZE = 8000
# -1 is a uniform round-robin draw with no footprint oversampling, as for uk
# and for the evaluated au corpus (see corpora/uk/corpus.py).
SAMPLE_GEO_FRACTION = -1
# No focus slice, for the reason given in corpora/uk/corpus.py.
SAMPLE_FOCUS = ""
SAMPLE_FOCUS_N = 0

# ---- Graph derivation (ingest/build_indexes.py) ----------------------------
# COPIED from au, not measured for us. For reference only: probe A5
# (clustered, not for decisions) found >=3 keywords on 72% of records, and
# probe A2 found keywords with counts in the hundreds of thousands, so
# MAX_TAG_DF=150 will drop the most common ones. No claim is made here about
# what edge count results; the uk graph values are marked the same way.
MIN_SHARED_TAGS = 3
EDGE_WEIGHT = "count"
MAX_TAG_DF = 150
MAX_EDGES_PER_ELEMENT = 20
MAX_CANDIDATE_PAIRS = 8_000_000

# ---- Vocabulary (ingest/build_vocabulary.py) -------------------------------
# The catalogue's own name for its country, dropped at title n-gram boundaries
# (the au equivalent is ("australia", "australian")). Given by the task, not
# measured.
VOCAB_STOP_EXTRA = ("united", "states", "us", "u.s.", "usa")

# ---- Prompt fragments ------------------------------------------------------
# Spliced into the English templates exactly as in corpora/uk/corpus.py (see
# there for where each goes). "a US": the article follows the pronunciation
# ("you-ess"); the rewrite prompt takes no article.
PROMPT_ROUTER_DOMAIN = "a US"
PROMPT_REASON_DOMAIN = "a US"
PROMPT_GEN_DOMAIN = "a US"
PROMPT_REWRITE_DOMAIN = "US"
