"""data.gov.au: files, index, backends, harvest, sampling, graph build and
prompt fragments.
"""

NAME = "au"

# ---- Backends --------------------------------------------------------------
# The OpenSearch index is namespaced by name, so corpora can share one
# OpenSearch node. Neo4j community edition holds one graph per server and
# ingest/build_indexes.py clears the whole graph before building, so each
# corpus should point at its own Neo4j server; build_indexes.py refuses to
# clear a graph marked as another corpus's.
INDEX_NAME = "knowledge-elements"
NEO4J_URI = "bolt://localhost:7687"

# ---- Files -----------------------------------------------------------------
# au uses data/ and eval/; uk and us use data/<name>/ and eval/<name>/.
DATA_DIR = "data"
EVAL_DIR = "eval"
RAW_FILE = "data/elements_all.jsonl"
ELEMENTS_FILE = "data/elements.jsonl"
HARVEST_DONE_FILE = "data/.harvest_done"
VOCAB_INDEX_FILE = "data/vocab_index.npz"
TOKEN_DF_FILE = "data/token_df.json"

# ---- Harvest (ingest/fetch_ckan.py) ----------------------------------------
CKAN_API = "https://data.gov.au/data/api/3"
# Prefix of the user-facing dataset page; the CKAN package `name` is appended.
DATASET_URL_PREFIX = "https://data.gov.au/data/dataset/"
# None keeps the historical PREFIX + name construction.
DATASET_URL_TEMPLATE = None
# Package fields that carry the spatial footprint.
CKAN_SPATIAL_KEYS = ("spatial", "spatial_coverage")
# Extras keys carrying a footprint.
CKAN_SPATIAL_EXTRAS_KEYS = ()
# Topic mode (fetch_ckan.py --topics): harvest by these search terms instead
# of by organisation. Not used for the evaluated corpus.
HARVEST_TOPICS = [
    "flood risk", "bushfire", "water quality",
    "flood victoria", "land use victoria", "planning victoria",
]
# Throttling and paging, set against how data.gov.au's CKAN behaved.
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
SAMPLE_SEED = 42
SAMPLE_SIZE = 8000
SAMPLE_GEO_FRACTION = 0.4
SAMPLE_FOCUS = "victoria,melbourne,gippsland"
SAMPLE_FOCUS_N = 2000

# ---- Graph derivation (ingest/build_indexes.py) ----------------------------
# Defaults only; config.py still lets MIN_SHARED_TAGS and EDGE_WEIGHT be set
# from the environment.
MIN_SHARED_TAGS = 3
EDGE_WEIGHT = "count"
MAX_TAG_DF = 150
MAX_EDGES_PER_ELEMENT = 20
MAX_CANDIDATE_PAIRS = 8_000_000

# ---- Vocabulary (ingest/build_vocabulary.py) -------------------------------
# Words dropped at the boundary of a title n-gram on top of the language's
# closed-class stopwords: the catalogue's own name for its country appears in
# so many titles that it carries no topic.
VOCAB_STOP_EXTRA = ("australia", "australian")

# ---- Prompt fragments ------------------------------------------------------
# Literal text inserted into the English prompt templates. Each template keeps
# the surrounding words and the line breaks; the fragment is only the words
# that name the catalogue. The rendered prompts must stay byte-identical to
# the pre-refactor ones, so these include the article.
#
#   PROMPT_ROUTER_DOMAIN   pipeline.ROUTER_SYSTEM, line 1:
#       "You route a search query to retrieval methods for <FRAGMENT>"
#       followed by a LINE BREAK, then "open-data catalog. Respond ONLY ..."
#   PROMPT_REASON_DOMAIN   pipeline._REASON_HEAD, line 2:
#       "question about <FRAGMENT> open-data catalog. The catalog only ..."
#   PROMPT_GEN_DOMAIN      pipeline.GEN_SYSTEM, line 1:
#       "You answer questions about <FRAGMENT> open-data catalog using"
#       followed by a LINE BREAK, then "ONLY the provided evidence. ..."
#   PROMPT_REWRITE_DOMAIN  rewrite.REWRITE_SYSTEM, line 2 (starts the line):
#       "<FRAGMENT> open-data catalogue metadata. Output a compact noun phrase"
PROMPT_ROUTER_DOMAIN = "an Australian"
PROMPT_REASON_DOMAIN = "an Australian"
PROMPT_GEN_DOMAIN = "an Australian"
PROMPT_REWRITE_DOMAIN = "Australian"
