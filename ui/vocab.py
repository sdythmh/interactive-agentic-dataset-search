"""Plain-language wording for everything the engine reports."""

PATHS = {
    "keyword": "Keyword search",
    "semantic": "Semantic search",
    "spatial": "Spatial search",
    "graph": "Knowledge graph search",
    # Only present when publisher linking is on.
    "publisher": "Publisher search",
}
PATH_ORDER = ("keyword", "semantic", "spatial", "graph", "publisher")

# Colours are listed so the frontend and any chart agree; they are the spec's.
PATH_COLORS = {
    "keyword": "#2563EB",
    "semantic": "#7C3AED",
    "spatial": "#059669",
    "graph": "#D97706",
    "publisher": "#DB2777",
}

GRAPH_INTENTS = {
    "PROVENANCE": "datasets related to the named dataset (they share tags with it)",
    "AUTHORSHIP": "datasets published by the named organisation",
    "CO_CONTRIBUTION": "datasets published by the same organisation as the named dataset",
    "AGGREGATION": "organisations that publish the most datasets",
}

PLACE_SOURCES = {
    "BBOXES": "built-in place list",
}

RELATION_WORDS = {
    "CONTRIBUTED_BY": "published by",
    "HAS_TAG": "tagged",
    "RELATED_TO": "related dataset",
}


def path_label(path):
    return PATHS.get(path, path.replace("_", " ").capitalize())


def intent_label(intent):
    return GRAPH_INTENTS.get(intent or "", "")


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def grounded_reason(f):
    """One plain sentence on why the grounded rule asked or did not ask,
    from the features pipeline.grounded_clarify() reports."""
    place, entity = f.get("place") or "", f.get("entity") or ""
    status, vetoes = f.get("entity_status"), f.get("vetoes") or []
    share = int(round(100 * (f.get("top_share") or 0)))
    if not vetoes:
        parts = []
        parts.append("the place \u2018%s\u2019 is not in the catalogue\u2019s place list" % place
                     if place else "no place was named")
        if status == "org_ambiguous":
            parts.append("\u2018%s\u2019 matches %s in this catalogue"
                         % (entity, _plural(f.get("family_size") or 0, "publisher")))
        elif status == "title_ambiguous":
            parts.append("\u2018%s\u2019 appears in the titles of %s"
                         % (entity, _plural(f.get("title_hits") or 0, "dataset")))
        elif entity:
            parts.append("\u2018%s\u2019 does not name a publisher or a dataset title" % entity)
        else:
            parts.append("no publisher or dataset was named")
        parts.append("the first ten results come from %s, none with half of them"
                     % _plural(f.get("n_pub") or 0, "publisher"))
        return "Asked because " + "; ".join(parts) + "."
    parts = []
    if "place_resolved" in vetoes:
        parts.append("the place \u2018%s\u2019 is in the catalogue\u2019s place list" % place)
    if "entity_grounded" in vetoes:
        if status == "org_grounded":
            n = f.get("family_size") or 0
            parts.append("\u2018%s\u2019 names %s in this catalogue"
                         % (entity, "a publisher" if n == 1 else "%d publishers" % n))
        else:
            parts.append("\u2018%s\u2019 appears in the titles of %s"
                         % (entity, _plural(f.get("title_hits") or 0, "dataset")))
    if "few_publishers" in vetoes:
        parts.append("the first ten results come from only %s"
                     % _plural(f.get("n_pub") or 0, "publisher"))
    if "dominant_publisher" in vetoes:
        parts.append("one publisher has %d%% of the first ten results" % share)
    return "Not asked: " + "; ".join(parts) + "."


def publisher_resolution_text(r):
    """The Search plan card's line on the publisher."""
    if not r:
        return ""
    if r.get("source") == "link":
        entity = r.get("entity") or ""
        if r.get("status") == "resolved":
            return "Interpreted \u2018%s\u2019 as %s (%s)" % (
                entity, (r.get("names") or ["?"])[0], _plural(r.get("datasets") or 0, "dataset"))
        if r.get("status") == "ambiguous":
            return "\u2018%s\u2019 could mean many publishers; not narrowed" % entity
        if entity:
            return "\u2018%s\u2019 did not match a publisher in this catalogue" % entity
        return "No publisher named"
    if r.get("status") == "resolved":
        names = r.get("names") or []
        label = names[0] if len(names) == 1 else "%s and %d related names" % (names[0], len(names) - 1)
        return "Publisher recognised: %s (%s in this catalogue)" % (
            label, _plural(r.get("datasets") or 0, "dataset"))
    if r.get("status") == "ambiguous":
        return "\u2018%s\u2019 matches %s" % (r.get("entity") or "",
                                               _plural(r.get("family_size") or 0, "publisher"))
    return "No publisher named"
