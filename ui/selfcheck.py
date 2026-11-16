"""Does the process hold the evaluated configuration of its corpus?"""
import os
import re

from ui import settings

ALLOWED_EXTRA = ("ASK_MODE", "ASK_STYLE", "ASK_MAX", "NEO4J_PASSWORD",
                 "CITE_CHECK", "ENTITY_RESOLVE")


def read_env_file(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            key, sep, value = line.partition("=")
            if sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                out[key] = value.strip().strip("\"'")
    return out


def env_names_read_by_config(path):
    """Names config.py reads from the environment (AST, not a hand list, so a
    switch added later is covered)."""
    import ast
    tree = ast.parse(open(path, encoding="utf-8").read())

    def is_environ(node):
        return (isinstance(node, ast.Attribute) and node.attr == "environ"
                and isinstance(node.value, ast.Name) and node.value.id == "os")

    def const(node):
        return node.value if isinstance(node, ast.Constant) \
            and isinstance(node.value, str) else None

    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            f = node.func
            if (f.attr == "getenv" and isinstance(f.value, ast.Name)
                    and f.value.id == "os") or \
                    (f.attr == "get" and is_environ(f.value)):
                if node.args and const(node.args[0]):
                    names.add(const(node.args[0]))
        elif isinstance(node, ast.Subscript) and is_environ(node.value):
            if const(node.slice):
                names.add(const(node.slice))
        elif isinstance(node, ast.Compare):
            if any(is_environ(c) for c in node.comparators) and const(node.left):
                names.add(const(node.left))
    return names


def _coerce(attr_value, text):
    """The file's text, in the type config.py parsed that variable to."""
    if isinstance(attr_value, bool):
        return text.lower() in ("1", "on", "true", "yes")
    if isinstance(attr_value, int):
        return int(text)
    if isinstance(attr_value, float):
        return float(text)
    return text


def check(corpus, root=None):
    """Returns (ok, problems, info). Imports config, so call it after the
    environment is final."""
    root = root or settings.ROOT
    problems = []
    import config

    spec = settings.CORPORA[corpus]
    expected = read_env_file(os.path.join(root, spec["env_file"]))

    # 1. environment
    read = env_names_read_by_config(os.path.join(root, "config.py"))
    allowed = set(expected) | set(ALLOWED_EXTRA) | {"CORPUS"}
    for name in sorted(read):
        if name in os.environ and name not in allowed:
            problems.append("environment variable %s=%r is set but is not part "
                            "of the %s env file" % (name, os.environ[name], corpus))
    for name in sorted(set(expected) | {"CORPUS"}):
        if os.environ.get(name) != expected.get(name, corpus if name == "CORPUS" else None):
            problems.append("environment variable %s is %r, the %s env file says %r"
                            % (name, os.environ.get(name), corpus,
                               expected.get(name)))

    # 2. values as config.py parsed them
    if config.CORPUS != corpus:
        problems.append("config.CORPUS is %r, this worker serves %r"
                        % (config.CORPUS, corpus))
    for name, text in sorted(expected.items()):
        if not hasattr(config, name):
            problems.append("config has no attribute %s" % name)
            continue
        have = getattr(config, name)
        want = _coerce(have, text)
        if have != want:
            problems.append("config.%s is %r, the env file says %r"
                            % (name, have, want))
    if "INDEX_NAME" not in expected:
        want_index = config.CORPUS_SPEC.corpus.INDEX_NAME
        if config.INDEX_NAME != want_index:
            problems.append("config.INDEX_NAME is %r, the corpus's own index is %r"
                            % (config.INDEX_NAME, want_index))
    if config.ASK_MODE == "on" and config.ASK_STYLE not in ("separate", "slot",
                                                            "grounded"):
        problems.append("ASK_MODE=on needs ASK_STYLE=separate, slot or "
                        "grounded, got %r" % config.ASK_STYLE)

    info = {"corpus": corpus, "env_file": spec["env_file"],
            "index": config.INDEX_NAME,
            "ask_mode": config.ASK_MODE, "ask_style": config.ASK_STYLE,
            "ask_max": config.ASK_MAX,
            "rerank": config.RERANK_MODE}
    return (not problems), problems, info
