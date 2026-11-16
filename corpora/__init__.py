"""Corpus registry: everything that ties the system to one catalogue."""
import importlib
import os
import types

DEFAULT_CORPUS = "au"

# Fields each module must define. A missing required field is a load-time
# error naming the field, rather than an AttributeError deep inside a run.
REQUIRED_CORPUS_FIELDS = (
    "NAME", "INDEX_NAME", "NEO4J_URI",
    "DATA_DIR", "EVAL_DIR", "RAW_FILE", "ELEMENTS_FILE", "HARVEST_DONE_FILE",
    "VOCAB_INDEX_FILE", "TOKEN_DF_FILE",
    "CKAN_API", "DATASET_URL_PREFIX", "DATASET_URL_TEMPLATE",
    "CKAN_SPATIAL_KEYS", "CKAN_SPATIAL_EXTRAS_KEYS", "HARVEST_TOPICS",
    "HARVEST_ROWS", "DEEP_PAGE_LIMIT", "HARVEST_MIN_INTERVAL", "HARVEST_SLEEP",
    "HARVEST_TIMEOUT", "HARVEST_RETRIES", "HARVEST_COOLDOWN",
    "HARVEST_FAIL_STREAK", "HARVEST_LONG_COOLDOWN", "LIST_PAGE_SIZE",
    "LIST_MAX_OFFSET",
    "SAMPLE_SEED", "SAMPLE_SIZE", "SAMPLE_GEO_FRACTION", "SAMPLE_FOCUS",
    "SAMPLE_FOCUS_N",
    "MIN_SHARED_TAGS", "EDGE_WEIGHT", "MAX_TAG_DF", "MAX_EDGES_PER_ELEMENT",
    "MAX_CANDIDATE_PAIRS",
    "VOCAB_STOP_EXTRA",
    "PROMPT_ROUTER_DOMAIN", "PROMPT_REASON_DOMAIN", "PROMPT_GEN_DOMAIN",
    "PROMPT_REWRITE_DOMAIN",
)
REQUIRED_LANGUAGE_FIELDS = (
    "EMBED_MODEL", "EMBED_QUERY_INSTRUCTION", "RERANK_MODEL",
    "ROUTE_TRIGGERS", "SENTENCE_OPENERS", "FUNCTION_WORDS",
    "VOCAB_BOUNDARY_STOPWORDS", "QUERY_TOKEN_RE", "PLACE_TOKEN_RE",
    "VOCAB_TOKEN_RE", "EXPANSION_TOKEN_RE", "FOREIGN_SCRIPT_RE",
    "has_foreign_script",
)
REQUIRED_PLACES_FIELDS = (
    "BBOXES", "PLACE_PREFIXES", "PLACE_SUFFIXES", "FEATURE_TAILS",
    "ADMIN_TAILS", "GAZETTEER_FILE",
)


def selected_name():
    """The corpus named by the environment, or the default."""
    return (os.getenv("CORPUS") or DEFAULT_CORPUS).strip().lower()


def _import_optional(module_name):
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        # Only "this module does not exist" means optional-and-absent. An
        # import error raised from inside the module is a real bug.
        if exc.name == module_name:
            return None
        raise


def _require(module, fields, label):
    missing = [f for f in fields if not hasattr(module, f)]
    if missing:
        raise SystemExit("corpus %s is missing required field(s): %s"
                         % (label, ", ".join(missing)))


def load(name=None):
    """Load one corpus. Returns a namespace with .name, .corpus, .language and
    .places (None when the corpus provides no place resolution module)."""
    name = name or selected_name()
    package = "corpora.%s" % name
    if _import_optional(package) is None:
        available = sorted(
            d for d in os.listdir(os.path.dirname(os.path.abspath(__file__)))
            if os.path.isfile(os.path.join(os.path.dirname(
                os.path.abspath(__file__)), d, "corpus.py")))
        raise SystemExit("unknown CORPUS %r; available: %s"
                         % (name, ", ".join(available)))

    corpus = importlib.import_module(package + ".corpus")
    _require(corpus, REQUIRED_CORPUS_FIELDS, "%s/corpus.py" % name)
    language = importlib.import_module(package + ".language")
    _require(language, REQUIRED_LANGUAGE_FIELDS, "%s/language.py" % name)
    places = _import_optional(package + ".places")
    if places is not None:
        _require(places, REQUIRED_PLACES_FIELDS, "%s/places.py" % name)

    if corpus.NAME != name:
        raise SystemExit("corpora/%s/corpus.py declares NAME=%r"
                         % (name, corpus.NAME))
    return types.SimpleNamespace(name=name, corpus=corpus, language=language,
                                 places=places)


__all__ = ["DEFAULT_CORPUS", "selected_name", "load"]
