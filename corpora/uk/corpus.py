"""data.gov.uk: files, index, backends, harvest, sampling, graph build and
prompt fragments.
"""

NAME = "uk"

# ---- Backends --------------------------------------------------------------
# OpenSearch indexes are namespaced by name, so uk and au share the node.
INDEX_NAME = "knowledge-elements-uk"
# Its own Neo4j. ingest/build_indexes.py clears the whole graph before building
# and refuses to clear a graph another corpus marked (LEGACY_CORPUS = "au" for
# an unmarked one), but the guard prevents accidents -- a separate server is
# what actually keeps two graphs.
NEO4J_URI = "bolt://localhost:7688"

# ---- Files -----------------------------------------------------------------
# uk and us use data/<name>/ and eval/<name>/; au uses data/ and eval/.
DATA_DIR = "data/uk"
EVAL_DIR = "eval/uk"
RAW_FILE = "data/uk/elements_all.jsonl"      # fetch_ckan.py output
ELEMENTS_FILE = "data/uk/elements.jsonl"     # sample_elements.py output
HARVEST_DONE_FILE = "data/uk/.harvest_done"  # fetch_ckan.py --resume log
VOCAB_INDEX_FILE = "data/uk/vocab_index.npz" # build_vocabulary.py output
TOKEN_DF_FILE = "data/uk/token_df.json"      # build_token_df.py output

# ---- Harvest (ingest/fetch_ckan.py) ----------------------------------------
# MEASURED. The documented front door, https://data.gov.uk/api/action/, is not
# the CKAN: it 301s twice, via www.data.gov.uk, to the host below. requests
# follows redirects, so the documented base would work, but every call would
# pay two extra round trips against HARVEST_MIN_INTERVAL. A client that does
# NOT follow redirects gets an HTML page reading "We're experiencing technical
# difficulties", which is the body of the 301 and not an outage.
CKAN_API = "https://ckan.publishing.service.gov.uk/api/3"
# MEASURED. The user-facing dataset page is on www.data.gov.uk, not on the
# CKAN host that answers the API.
DATASET_URL_PREFIX = "https://www.data.gov.uk/dataset/"
# MEASURED by HEAD request on three sampled packages, 2026-09-21.
DATASET_URL_TEMPLATE = "https://www.data.gov.uk/dataset/{id}/{name}"
# MEASURED. This catalogue carries NO top-level footprint at all: 0/500 of the
# probe sample have `spatial` or `spatial_coverage` on the package.
CKAN_SPATIAL_KEYS = ()
# MEASURED. extras["spatial"] is a GeoJSON string, present on 132/500 of the
# probe sample.
CKAN_SPATIAL_EXTRAS_KEYS = ("spatial",)
# Small-corpus mode (fetch_ckan.py --topics). Not used for uk: the topic list
# is au's old 587-element small-corpus path and has no UK counterpart.
HARVEST_TOPICS = []
# Throttling and paging. Copied from au; see the block comment above.
HARVEST_ROWS = 200             # used for the 2026-09-21 harvest
# MEASURED, and it contradicts the au reasoning: rows=1 at start=9000, 20000
# and 40000 all returned 200 in 0.39-0.45 s, no slowdown with depth, against a
# catalogue of 59,028.
DEEP_PAGE_LIMIT = 9000         # used for the 2026-09-21 harvest
HARVEST_MIN_INTERVAL = 1.0     # used for the 2026-09-21 harvest
HARVEST_SLEEP = 0.0            # used for the 2026-09-21 harvest
HARVEST_TIMEOUT = 120          # used for the 2026-09-21 harvest
HARVEST_RETRIES = 6            # used for the 2026-09-21 harvest
HARVEST_COOLDOWN = 30          # used for the 2026-09-21 harvest
HARVEST_FAIL_STREAK = 3        # used for the 2026-09-21 harvest
HARVEST_LONG_COOLDOWN = 300    # used for the 2026-09-21 harvest
LIST_PAGE_SIZE = 200           # used for the 2026-09-21 harvest
LIST_MAX_OFFSET = 60000        # used for the 2026-09-21 harvest

# ---- Sampling (ingest/sample_elements.py) ----------------------------------
# Same size and seed as au, so corpus scale is not a variable between the
# two; the other three differ, with reasons.
SAMPLE_SEED = 42
# au samples 8,000 of 140,839 (5.7%). The same 8,000 out of 59,028 is 13.6%.
SAMPLE_SIZE = 8000
# -1 is a uniform round-robin draw with NO footprint oversampling.
SAMPLE_GEO_FRACTION = -1
# No focus slice, for two reasons.
SAMPLE_FOCUS = ""
SAMPLE_FOCUS_N = 0

# ---- Graph derivation (ingest/build_indexes.py) ----------------------------
# Copied from au, and this is the block most likely to be wrong. The probe
# found 366/500 sampled records with NO tags, and the 359 tag occurrences in
# the whole sample drawn from only 148 distinct tags -- 190 of those
# occurrences are the same two strings. MIN_SHARED_TAGS=3 on that input yields
# close to no RELATED_TO edges, which silently empties the graph path, the
# PROVENANCE / CO_CONTRIBUTION intents and the `graph` voice. The values
# below are kept equal to au's so the corpora differ only in their data; the
# 2026-09-21 build with them has 340 edges and 7,777 islands.
MIN_SHARED_TAGS = 3            # used for the 2026-09-21 graph build
EDGE_WEIGHT = "count"          # used for the 2026-09-21 graph build
MAX_TAG_DF = 150               # used for the 2026-09-21 graph build
MAX_EDGES_PER_ELEMENT = 20     # used for the 2026-09-21 graph build
MAX_CANDIDATE_PAIRS = 8_000_000  # used for the 2026-09-21 graph build

# ---- Vocabulary (ingest/build_vocabulary.py) -------------------------------
# The catalogue's own name for its country, dropped at title n-gram
# boundaries on top of language.VOCAB_BOUNDARY_STOPWORDS. The au equivalent is
# ("australia", "australian"); "uk" is included because it is how UK
# publishers usually write it in a title.
VOCAB_STOP_EXTRA = ("uk", "united", "kingdom")

# ---- Prompt fragments ------------------------------------------------------
# Literal words spliced into the English prompt templates; the template owns
# the surrounding text and the line breaks. Where each one goes:
#
#   PROMPT_ROUTER_DOMAIN   pipeline.ROUTER_SYSTEM, line 1:
#       "You route a search query to retrieval methods for <FRAGMENT>"
#       then a LINE BREAK, then "open-data catalog. Respond ONLY with JSON:"
#   PROMPT_REASON_DOMAIN   pipeline._REASON_HEAD, line 2:
#       "question about <FRAGMENT> open-data catalog. The catalog only ..."
#   PROMPT_GEN_DOMAIN      pipeline.GEN_SYSTEM, line 1:
#       "You answer questions about <FRAGMENT> open-data catalog using"
#       then a LINE BREAK, then "ONLY the provided evidence. ..."
#   PROMPT_REWRITE_DOMAIN  rewrite.REWRITE_SYSTEM, start of line 2:
#       "<FRAGMENT> open-data catalogue metadata. Output a compact noun phrase"
#
# "a UK" rather than "an UK": the article follows the pronunciation, and the
# rewrite prompt takes no article at all.
PROMPT_ROUTER_DOMAIN = "a UK"
PROMPT_REASON_DOMAIN = "a UK"
PROMPT_GEN_DOMAIN = "a UK"
PROMPT_REWRITE_DOMAIN = "UK"
