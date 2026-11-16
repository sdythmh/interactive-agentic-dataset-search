"""Static checks on the frontend files (ui/static, vendor excluded)."""
import os
import re
import sys
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "ui", "static")
FAILURES = []

CJK = re.compile("[\u3000-\u30ff\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]")
BANNED = [
    (r"\bv4\b", "v4"), (r"\bRRF\b", "RRF"), (r"\bc1g\b", "c1g"),
    (r"hydrat", "hydrate"), (r"\bASK_", "ASK_"), (r"\bEVAL_", "EVAL_"),
    (r"RERANK", "RERANK"), (r"INDEX_NAME", "INDEX_NAME"), (r"\bgates?\b", "gate"),
    (r"\bvotes?\b|\bvoting\b", "vote"), (r"\.env\b", ".env"),
    (r"\.json\b", ".json file name"), (r"\.jsonl\b", ".jsonl file name"),
    (r"\.py\b", ".py file name"), (r"\.sh\b", ".sh file name"),
    (r"\bAUTHORSHIP\b|\bPROVENANCE\b|\bCO_CONTRIBUTION\b|\bAGGREGATION\b", "internal intent name"),
    (r"\bfrom_methods\b|\bpath_ranks\b|\brrf_score\b|\bvote_score\b", "internal field name"),
]
# Names that are capitalised in the code they come from; the same letters in
# lower case are ordinary words (an event called "rerank" is not shown).
CASE_SENSITIVE = ("RRF", "RERANK", "ASK_", "EVAL_", "INDEX_NAME")
COMPONENTS = ["TopBar", "CorpusSwitch", "ModeToggle", "Settings", "Chat", "Message",
              "StepList", "ClarifyCard", "ResultList", "ResultCard", "DatasetDetail",
              "MapView", "ConnectionsGraph", "ProcessTimeline", "PathTabs",
              "FusionTable", "SlopeChart", "ScoreBars", "AnswerAttempts", "Notice",
              "StatusDot"]
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def check(label, ok, detail=""):
    print("  [%s] %s%s" % ("ok" if ok else "FAIL", label,
                           (" -- " + str(detail)[:600]) if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)


def files(exts):
    out = []
    for base, dirs, names in os.walk(STATIC):
        dirs[:] = [d for d in dirs if d != "vendor"]
        for n in names:
            if n.endswith(exts):
                out.append(os.path.join(base, n))
    return sorted(out)


def js_strings(src):
    """The string literals of a JavaScript file (comments and regex literals
    skipped). Good enough for the files in this directory."""
    out, i, n = [], 0, len(src)
    prev = ""
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "'\"`":
            j, buf = i + 1, []
            while j < n and src[j] != c:
                if src[j] == "\\" and j + 1 < n:
                    nxt = src[j + 1]
                    if nxt == "u" and re.match(r"[0-9a-fA-F]{4}", src[j + 2:j + 6]):
                        buf.append(chr(int(src[j + 2:j + 6], 16)))
                        j += 6
                        continue
                    buf.append(nxt)
                    j += 2
                    continue
                buf.append(src[j])
                j += 1
            out.append("".join(buf))
            i = j + 1
            prev = '"'
            continue
        if c == "/" and prev in ("", "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "\n"):
            j, in_class = i + 1, False
            while j < n and (src[j] != "/" or in_class):
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                j += 1
            i = j + 1
            prev = ")"
            continue
        if not c.isspace():
            prev = c
        i += 1
    return out


class TextNodes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        for k, v in attrs:
            if k in ("title", "alt", "aria-label", "placeholder") and v:
                self.text.append(v)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.text.append(data)


def main():
    print("CJK")
    bad = []
    for path in files((".html", ".css", ".js", ".md", ".json")):
        if CJK.search(open(path, encoding="utf-8").read()):
            bad.append(os.path.relpath(path, ROOT))
    check("no CJK character under ui/static (vendor excluded)", not bad, bad)

    print("\nuser-visible text")
    visible = []        # (file, text)
    for path in files((".js",)):
        src = open(path, encoding="utf-8").read()
        for s in js_strings(src):
            visible.append((os.path.relpath(path, ROOT), s))
    for path in files((".html",)):
        parser = TextNodes()
        parser.feed(open(path, encoding="utf-8").read())
        for t in parser.text:
            visible.append((os.path.relpath(path, ROOT), t))
    print("  %d string literals and text nodes examined" % len(visible))
    check("strings were found (the scanner works)", len(visible) > 200)
    hits = []
    for rel, text in visible:
        # Markup in a template string is not text; strip tags and v-bindings
        # but keep the words between them.
        plain = re.sub(r"<[^>]*>", " ", text)
        for pattern, name in BANNED:
            if re.search(pattern, plain, 0 if name in CASE_SENSITIVE else re.I):
                hits.append("%s: %s in %r" % (rel, name, plain.strip()[:80]))
    check("no internal word in any visible string", not hits, hits[:6])

    print("\nreferences")
    index = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
    refs = [r for r in re.findall(r'(?:href|src)="([^"]+)"', index)
            if r != "/static/"]          # the <base> element
    missing = [r for r in refs if not os.path.exists(os.path.join(STATIC, r.split("?")[0]))]
    check("every file index.html refers to exists (%d refs)" % len(refs), not missing, missing)
    css_missing = []
    for path in files((".css",)):
        css = open(path, encoding="utf-8").read()
        for u in re.findall(r"url\(['\"]?([^'\")]+)", css):
            if u.startswith("data:"):
                continue
            if not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(path), u))):
                css_missing.append("%s -> %s" % (os.path.basename(path), u))
    check("every url() in the stylesheets exists", not css_missing, css_missing)
    leaflet_css = open(os.path.join(STATIC, "vendor", "leaflet", "leaflet.css")).read()
    lf_missing = [u for u in re.findall(r"url\(['\"]?(images/[^'\")]+)", leaflet_css)
                  if not os.path.exists(os.path.join(STATIC, "vendor", "leaflet", u))]
    check("every image leaflet.css uses is vendored", not lf_missing, lf_missing)
    external = []
    for path in files((".html", ".css", ".js")):
        for m in re.finditer(r"https?://[^\s'\"<>)]+", open(path, encoding="utf-8").read()):
            if m.group(0) != TILE_URL:
                external.append("%s: %s" % (os.path.relpath(path, ROOT), m.group(0)))
    check("no http(s) address except the map tile template", not external, external)
    check("index.html has no <script> or <link> pointing off the page",
          not [r for r in refs if re.match(r"(https?:)?//", r)])

    print("\nstructure")
    absent = [c for c in COMPONENTS
              if not os.path.exists(os.path.join(STATIC, "js", "components", c + ".js"))]
    check("a file per component", not absent, absent)
    for name in ("css/tokens.css", "css/app.css", "js/main.js", "js/api.js", "js/reducer.js"):
        check("ui/static/%s exists" % name, os.path.exists(os.path.join(STATIC, name)))
    tokens = open(os.path.join(STATIC, "css", "tokens.css")).read()
    for value in ("#F5F6FA", "#FFFFFF", "#E6E8EF", "#1F2330", "#6B7080", "#4F46E5",
                  "#2563EB", "#7C3AED", "#059669", "#D97706", "font-display: swap"):
        check("tokens.css has %s" % value, value.lower() in tokens.lower())
    readme = open(os.path.join(STATIC, "vendor", "README.md"), encoding="utf-8").read()
    for lib in ("Vue", "Leaflet", "Cytoscape", "Inter"):
        check("vendor/README.md lists %s with a version and a licence" % lib,
              re.search(r"\|\s*%s[^|]*\|\s*\d+\.\d+\.\d+\s*\|[^\n]*(MIT|BSD|OFL|Open Font)" % lib, readme) is not None)
    vendored = ["vue.global.prod.js", "cytoscape.min.js", "leaflet/leaflet.js",
                "leaflet/leaflet.css", "leaflet/images/marker-icon.png",
                "fonts/inter-latin-400-normal.woff2"]
    check("vendored files present",
          all(os.path.exists(os.path.join(STATIC, "vendor", v)) for v in vendored))

    print()
    if FAILURES:
        print("FAILED: %d check(s)" % len(FAILURES))
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
