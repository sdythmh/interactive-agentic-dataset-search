"""English, as written in data.gov.au metadata and by its users."""
import re

# ---- Models ----------------------------------------------------------------
# English models. config.py still lets RERANK_MODEL be set from the
# environment.
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
# bge-*-en-v1.5 is trained asymmetrically; this is its model-card query
# instruction. Used only when EMBED_QUERY_PREFIX is on.
EMBED_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
RERANK_MODEL = "BAAI/bge-reranker-base"

# ---- Routing triggers (pipeline.route) --------------------------------------
# Hand-curated patterns that augment the LLM router. English
# prepositions and relation phrases.
ROUTE_TRIGGERS = {
    "spatial": re.compile(r"\b(in|near|around|within|for)\s+[A-Z][a-z]+"),
    "provenance": re.compile(r"related to|similar to|derived from|connected to", re.I),
    "authorship": re.compile(r"(published|contributed|provided) by|publisher", re.I),
}

# ---- Rewrite gate (rewrite.gate_verdict) -----------------------------------
# Closed-class sentence openers: wh-words, auxiliaries, request verbs.
SENTENCE_OPENERS = {
    "what", "where", "which", "who", "whom", "whose", "when", "why", "how",
    "do", "does", "did", "is", "are", "was", "were", "can", "could",
    "should", "would", "will", "has", "have", "am",
    "find", "show", "list", "give", "tell", "name", "search", "identify",
}

# Closed-class function words, plus the three words every catalogue query uses
# for "dataset" and the four spatial prepositions.
FUNCTION_WORDS = {
    "the", "of", "in", "for", "and", "to", "a", "an", "on", "at", "by",
    "from", "with", "or", "as", "is", "are", "be", "this", "that",
    "data", "dataset", "datasets", "near", "around", "within", "about",
}

# Boundary stopwords for title n-grams (ingest/build_vocabulary.py). The
# catalogue-specific additions live in corpus.VOCAB_STOP_EXTRA.
VOCAB_BOUNDARY_STOPWORDS = {
    "the", "of", "in", "for", "and", "to", "a", "an", "on", "at", "by",
    "from", "with", "or", "as", "is", "are", "be", "this", "that",
    "data", "dataset", "datasets",
}

# ---- Token patterns --------------------------------------------------------
# Query tokens for the rewrite guard, the rewrite gate and the full-text df table
# (rewrite.TOKEN_RE and ingest/build_token_df.py must agree, since the table
# is queried with the tokens it was built from).
QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")
# Place-phrase tokens for the SPATIAL_NAME_FALLBACK=df gate (retrieval._place_is_selective).
PLACE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-']+")
# Title tokens for the vocabulary n-grams (ingest/build_vocabulary.py).
VOCAB_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z\-]+")
# Lower-cased word tokens for Scheme A's redundancy check (expansion.py).
EXPANSION_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Runs of characters outside the catalogue's script, stripped from a place
# string by PLACE_NORMALISE (expansion._place_keys).
FOREIGN_SCRIPT_RE = re.compile(r"[^\x00-\x7f]+")


def has_foreign_script(text):
    """Rewrite gate: a question written in another script cannot be matched lexically."""
    return any(ord(ch) > 127 and ch.isalpha() for ch in text or "")
