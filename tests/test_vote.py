"""RERANK_MODE=vote: merge rule, voices, invariants.

Usage
-----
    python tests/test_vote.py
"""
import io
import os
import sys
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

SINK = io.StringIO()
with redirect_stdout(SINK):
    import config
    import pipeline
    import rerank
    import retrieval

FAILURES = []


def check(label, condition, detail=""):
    print("  [%s] %s%s" % ("ok" if condition else "FAIL", label,
                           ("  (%s)" % (detail,)) if not condition else ""))
    if not condition:
        FAILURES.append(label)


class Stub:
    """Scores are read from the document's title: 'd<score>'."""

    def predict(self, pairs, **kwargs):
        return [float(d.split(".")[0][1:].split()[0]) / 100.0 for _, d in pairs]


def doc(i, score, relation=None, weight=None):
    d = {"id": "id%02d" % i, "title": "d%d" % score, "description": "",
         "type": "dataset", "tags": [], "organization": "", "url": ""}
    if relation:
        d["graph_relation"] = relation
        d["graph_weight"] = weight
    return d


def run(docs, mode="vote", voices="cross", absent="none"):
    config.RERANK_MODE, config.RERANK_VOICES = mode, voices
    config.VOTE_ABSENT_RANK = absent
    rerank.reset_trace()
    with redirect_stdout(SINK):
        out = rerank.rerank("q", [dict(d) for d in docs])
    return out, rerank.take_trace()


def pool():
    # 50 documents, cross-encoder scores 99, 97, ..., distinct. Members of
    # the graph voice sit at cross ranks 5, 20 and 49.
    docs = [doc(i, 99 - 2 * i) for i in range(50)]
    docs[5] = doc(5, 99 - 10, "PROVENANCE", 3)
    docs[20] = doc(20, 99 - 40, "PROVENANCE", 3)
    docs[49] = doc(49, 1, "PROVENANCE", 5)
    docs[30] = doc(30, 99 - 60, "AUTHORSHIP", None)      # never a member
    return docs


def main():
    rerank._get_model = lambda: Stub()
    config.HYDRATE_CANDIDATES = True
    config.RRF_K = 60
    k = 60

    print("1. RERANK_VOICES=cross under vote equals RERANK_MODE=cross")
    docs = pool()
    a, ta = run(docs, mode="cross")
    b, tb = run(docs, mode="vote", voices="cross")
    check("same order", [d["id"] for d in a] == [d["id"] for d in b])
    check("same rerank_score", [d["rerank_score"] for d in a] == [d["rerank_score"] for d in b])
    check("same counters", {x: tb[x] for x in rerank._COUNTERS} == ta)
    check("cross mode trace has only the historical keys", set(ta) == set(rerank._COUNTERS))
    check("vote trace names the voices used", tb.get("voices") == ["cross"])

    print("2. graph voice ranks")
    g = rerank.graph_voice_ranks(pool())
    check("members: PROVENANCE only (AUTHORSHIP excluded)", sorted(g) == [5, 20, 49], g)
    check("competition ranking: weight 5 -> 0, the two weight-3 ties -> 1, 1",
          g == {49: 0, 5: 1, 20: 1}, g)
    mixed = [doc(0, 50, "CO_CONTRIBUTION", None), doc(1, 40, "PROVENANCE", 3),
             doc(2, 30, "PROVENANCE", 3), doc(3, 20, "AGGREGATION", None)]
    check("missing weight sorts last; AGGREGATION excluded",
          rerank.graph_voice_ranks(mixed) == {1: 0, 2: 0, 0: 2},
          rerank.graph_voice_ranks(mixed))

    print("3. VOTE_ABSENT_RANK=none partitions")
    out, trace = run(pool(), voices="cross,graph", absent="none")
    ids = [d["id"] for d in out]
    check("the three members occupy the first three places",
          set(ids[:3]) == {"id05", "id20", "id49"}, ids[:4])
    s49 = 1 / (k + 0 + 1) + 1 / (k + 49 + 1)          # graph 0, cross 49
    s0 = 1 / (k + 0 + 1)                              # non-member, cross 0
    check("member at cross 49 (%.5f) > non-member at cross 0 (%.5f)" % (s49, s0),
          s49 > s0 and out[ids.index("id49")]["vote_score"] == round(s49, 8))
    check("non-members keep cross order after them", ids[3:6] == ["id00", "id01", "id02"])
    check("trace: voices and member count", trace["voices"] == ["cross", "graph"]
          and trace["graph_members"] == 3, trace)

    print("4. VOTE_ABSENT_RANK=tail blends")
    out, _ = run(pool(), voices="cross,graph", absent="tail")
    ids = [d["id"] for d in out]
    L = 3                                              # list length of the graph voice
    t_top = 1 / (k + 0 + 1) + 1 / (k + L + 1)          # non-member, cross 0
    t_49 = 1 / (k + 49 + 1) + 1 / (k + 0 + 1)          # member, cross 49, graph 0
    t_5 = 1 / (k + 5 + 1) + 1 / (k + 1 + 1)            # member, cross 5, graph 1
    check("non-member at cross 0: 1/61 + 1/%d = %.5f" % (k + L + 1, t_top),
          out[ids.index("id00")]["vote_score"] == round(t_top, 8))
    check("member at cross 49: 1/110 + 1/61 = %.5f, below it" % t_49,
          out[ids.index("id49")]["vote_score"] == round(t_49, 8) and t_49 < t_top)
    check("member at cross 5 moves up (%.5f): rank %d" % (t_5, ids.index("id05")),
          ids.index("id05") < 5)
    check("member at cross 49 no longer first", ids[0] != "id49")
    a, _ = run(pool(), voices="cross,fusion", absent="none")
    b, _ = run(pool(), voices="cross,fusion", absent="tail")
    check("cross,fusion identical under none and tail (dense voices)",
          [d["id"] for d in a] == [d["id"] for d in b])

    print("5. ties and skipping")
    tied = [doc(i, 50) for i in range(6)]
    out, trace = run(tied, voices="cross,fusion")
    check("equal scores keep the incoming order", [d["id"] for d in out]
          == [d["id"] for d in tied])
    plain = [doc(i, 90 - i) for i in range(10)]
    a, _ = run(plain, mode="cross")
    b, trace = run(plain, voices="cross,graph", absent="tail")
    check("no member -> graph skipped, order equals cross",
          [d["id"] for d in a] == [d["id"] for d in b] and trace["voices"] == ["cross"]
          and "graph" in trace.get("skipped", {}), trace)

    print("6. validation")
    for voices, label in (("cross,bogus", "unknown voice"), ("graph", "cross missing"),
                          ("cross,llm", "llm not implemented")):
        config.RERANK_VOICES = voices
        try:
            rerank.vote_voices()
            ok = False
        except ValueError:
            ok = True
        check(label + " raises", ok)
    config.RERANK_VOICES = "cross"

    print("7. rrf_fuse keeps the vote fields")
    config.RERANK_MODE = "vote"
    for hydrate in (True, False):
        config.HYDRATE_CANDIDATES = hydrate
        text = doc(1, 50)
        graph = doc(1, 50, "PROVENANCE", 4)
        with redirect_stdout(SINK):
            fused = retrieval.rrf_fuse({"keyword": [dict(text)],
                                        "graph": [dict(graph)]}, limit=5)
        check("HYDRATE=%d: relation and weight kept" % hydrate,
              fused[0].get("graph_relation") == "PROVENANCE"
              and fused[0].get("graph_weight") == 4, fused[0])
        r1 = [dict(doc(1, 50, "PROVENANCE", 3), path_ranks={"graph": 0})]
        r2 = [dict(doc(1, 50, "PROVENANCE", 6), path_ranks={"graph": 2})]
        r3 = [dict(doc(1, 50), path_ranks={"keyword": 0})]
        with redirect_stdout(SINK):
            merged = retrieval.rrf_fuse({"round1": r1, "round2": r2, "round3": r3},
                                        limit=5)
        check("HYDRATE=%d: rounds keep the strongest edge (6)" % hydrate,
              merged[0].get("graph_weight") == 6
              and merged[0].get("graph_relation") == "PROVENANCE", merged[0])
    config.HYDRATE_CANDIDATES = True
    config.RERANK_MODE = "cross"
    with redirect_stdout(SINK):
        fused = retrieval.rrf_fuse({"keyword": [doc(1, 50)], "graph": [doc(2, 50)]},
                                   limit=5)
    check("outside vote mode no vote field appears",
          not any("graph_weight" in d or "vote_score" in d for d in fused))

    print("8. fingerprint and warn")
    config.RERANK_MODE = "cross"
    fp_cross = rerank.fingerprint()
    config.RERANK_MODE, config.RERANK_VOICES = "vote", "cross,graph"
    config.VOTE_ABSENT_RANK = "tail"
    fp_vote = rerank.fingerprint()
    check("cross fingerprint has no vote keys",
          not {"voices", "absent_rank", "graph_relations"} & set(fp_cross))
    check("vote fingerprint records voices, absent rank, relations, k",
          fp_vote.get("voices") == ["cross", "graph"] and fp_vote.get("absent_rank") == "tail"
          and fp_vote.get("graph_relations") == ["PROVENANCE", "CO_CONTRIBUTION"]
          and fp_vote.get("rrf_k") == 60, fp_vote)
    config.HYDRATE_CANDIDATES = False
    rerank._warned_unhydrated[0] = False
    out_buf = io.StringIO()
    with redirect_stdout(out_buf):
        rerank.rerank("q", pool())
        rerank.rerank("q", pool())
    check("graph voice with HYDRATE=0 warns once",
          out_buf.getvalue().count("graph voice with HYDRATE_CANDIDATES=0") == 1)

    print("9. GRAPH_VOICE_ROUNDS")
    config.RERANK_MODE, config.RERANK_VOICES = "vote", "cross,graph"
    config.VOTE_ABSENT_RANK = "none"
    config.HYDRATE_CANDIDATES = True

    def graph_doc(i, score, weight):
        d = doc(i, score, "PROVENANCE", weight)
        d["graph_note"] = "graph evidence: shares %d tags with 'A'" % weight
        return d

    r1 = [graph_doc(1, 50, 3), doc(2, 60)]
    r2 = [graph_doc(1, 50, 6), graph_doc(3, 40, 4), doc(4, 70)]
    config.GRAPH_VOICE_ROUNDS = "all"
    check("all: the list itself, every round",
          pipeline.voice_round_filter(r2, 1) is r2 and pipeline.voice_round_filter(r1, 0) is r1)
    config.GRAPH_VOICE_ROUNDS = "first"
    check("first: round 1 untouched", pipeline.voice_round_filter(r1, 0) is r1)
    later = pipeline.voice_round_filter(r2, 1)
    check("first: later rounds lose graph_relation / graph_weight only",
          not any("graph_relation" in d or "graph_weight" in d for d in later)
          and [d["id"] for d in later] == [d["id"] for d in r2]
          and later[0]["graph_note"] == r2[0]["graph_note"], later)
    check("first: nothing shared is mutated",
          r2[0]["graph_weight"] == 6 and r2[1]["graph_relation"] == "PROVENANCE")
    rounds_all = {"round1": [dict(d) for d in r1], "round2": [dict(d) for d in r2]}
    rounds_first = {"round1": [dict(d) for d in r1], "round2": later}
    with redirect_stdout(SINK):
        pool_all = retrieval.rrf_fuse(rounds_all, limit=10)
        pool_first = retrieval.rrf_fuse(rounds_first, limit=10)
    check("the pool is the same under both", [d["id"] for d in pool_all]
          == [d["id"] for d in pool_first])
    m_all = {pool_all[i]["id"] for i in rerank.graph_voice_ranks(pool_all)}
    m_first = {pool_first[i]["id"] for i in rerank.graph_voice_ranks(pool_first)}
    check("all: members from both rounds", m_all == {"id01", "id03"}, m_all)
    check("first: only round-1 members; a round-2-only graph doc is not one",
          m_first == {"id01"}, m_first)
    w_first = [d for d in pool_first if d["id"] == "id01"][0]["graph_weight"]
    w_all = [d for d in pool_all if d["id"] == "id01"][0]["graph_weight"]
    check("first: round-1 weight (3), all: strongest across rounds (6)",
          (w_first, w_all) == (3, 6), (w_first, w_all))
    config.GRAPH_VOICE_ROUNDS = "all"
    fp_all = rerank.fingerprint()
    config.GRAPH_VOICE_ROUNDS = "first"
    fp_first = rerank.fingerprint()
    check("fingerprint: absent under all, recorded under first",
          "graph_voice_rounds" not in fp_all and fp_first.get("graph_voice_rounds") == "first")
    config.GRAPH_VOICE_ROUNDS = "bogus"
    try:
        pipeline.voice_round_filter(r2, 1)
        ok = False
    except ValueError:
        ok = True
    check("unknown value raises", ok)
    config.GRAPH_VOICE_ROUNDS = "all"

    config.RERANK_MODE, config.RERANK_VOICES = "off", "cross"
    config.VOTE_ABSENT_RANK = "none"
    config.HYDRATE_CANDIDATES = False
    if FAILURES:
        print("\n%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
