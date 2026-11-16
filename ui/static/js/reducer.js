/* Pure state reducer for one search job.
 *
 *   reduce(state, event) -> new state
 *
 * No DOM, no network, no clock. Standard and Advanced mode both read this
 * state; the mode only changes what is shown. The state is plain JSON so the
 * same function can be fed a recorded stream in a test.
 */
(function (root) {
  "use strict";

  // Display names and colours for the four retrieval paths. Keyed by the id
  // the stream uses; the id itself is never shown.
  var PATHS = {
    keyword:  { label: "Keyword search",         color: "#2563EB" },
    semantic: { label: "Semantic search",        color: "#7C3AED" },
    spatial:  { label: "Spatial search",         color: "#059669" },
    graph:    { label: "Knowledge graph search", color: "#D97706" },
    publisher: { label: "Publisher search",      color: "#DB2777" }
  };
  var PATH_ORDER = ["keyword", "semantic", "spatial", "graph", "publisher"];

  // Which timeline card a step belongs to.
  var STEP_CARD = {
    understand: "understanding", plan: "plan", search: "round",
    combine: "round", decide: "decision", clarify_check: "clarify",
    clarify: "clarify", rerank: "rerank", write: "answer", check: "answer"
  };

  function clone(x) { return JSON.parse(JSON.stringify(x)); }

  function initial(question, opts) {
    opts = opts || {};
    return {
      id: null, corpus: opts.corpus || null, question: question || "",
      ask: !!opts.ask,
      status: "starting",      // starting | queued | running | done | error | cancelled | interrupted
      queued: null, progress: "",
      steps: [], timeline: [], notices: [],
      runIndex: 0, round: 0,
      results: null, rerank: null, answer: null, drafts: [], comparison: null,
      clarify: null, restarted: false, planArea: null, scope: null,
      error: null, done: null, lastT: 0
    };
  }

  function findCard(s, type, run, round) {
    for (var i = s.timeline.length - 1; i >= 0; i--) {
      var c = s.timeline[i];
      if (c.type === type && c.run === run &&
          (round === undefined || round === null || c.round === round)) return c;
    }
    return null;
  }

  function ensureCard(s, type, run, round, extra) {
    var c = findCard(s, type, run, type === "round" || type === "plan" || type === "decision" ? round : null);
    if (c) return c;
    c = { key: type + ":" + run + ":" + (round || 0) + ":" + s.timeline.length,
          type: type, run: run, round: round || 0, running: false,
          duration_ms: 0, steps: 0 };
    if (extra) for (var k in extra) c[k] = extra[k];
    s.timeline.push(c);
    return c;
  }

  function roundCard(s, run, round) {
    return ensureCard(s, "round", run, round, {
      query: "", rewrite: null, paths: [], fusion: null, pool: null
    });
  }

  function reduce(prev, ev) {
    var s = clone(prev);
    var run = typeof ev.run === "number" ? ev.run : s.runIndex;
    if (typeof ev.t === "number" && ev.type !== "queued") s.lastT = ev.t;
    if (run > s.runIndex) s.runIndex = run;
    var c, i, st;

    switch (ev.type) {
      case "queued":
        s.status = "queued"; s.id = ev.job_id || s.id; s.question = s.question || ev.question || "";
        s.queued = { position: ev.position, text: ev.text };
        break;

      case "job_started":
        s.status = "running"; s.id = ev.job_id; s.corpus = ev.corpus || s.corpus;
        s.question = s.question || ev.question;
        s.ask = !!ev.ask; s.queued = null;
        break;

      case "progress":
        s.progress = ev.label;
        break;

      case "step_start":
        st = { id: ev.id, kind: ev.kind, label: ev.label, round: ev.round || 0,
               attempt: ev.attempt || 0, status: "running", run: run,
               duration_ms: 0, summary: "", card: null };
        var cardType = STEP_CARD[ev.kind] || "other";
        c = cardType === "round"
          ? roundCard(s, run, ev.round || s.round || 1)
          : ensureCard(s, cardType, run, ev.round || s.round || 1);
        c.running = true; c.steps += 1; st.card = c.key;
        s.steps.push(st);
        s.progress = ev.label;
        break;

      case "step_end":
        for (i = 0; i < s.steps.length; i++) {
          if (s.steps[i].id === ev.id) {
            st = s.steps[i];
            st.status = "done"; st.duration_ms = ev.duration_ms;
            st.summary = ev.summary || "";
            for (var j = 0; j < s.timeline.length; j++) {
              if (s.timeline[j].key === st.card) {
                var cc = s.timeline[j];
                cc.duration_ms += ev.duration_ms;
                cc.steps -= 1;
                if (cc.steps <= 0) cc.running = false;
              }
            }
          }
        }
        break;

      case "understanding":
        c = ensureCard(s, "understanding", run, 0);
        c.original = ev.original; c.contextual = ev.contextual;
        c.messages_used = ev.messages_used;
        break;

      case "search_plan":
        c = ensureCard(s, "plan", run, ev.round);
        c.paths = ev.paths; c.skipped = ev.skipped; c.place = ev.place;
        c.area = ev.area; c.area_used = ev.area_used; c.entity = ev.entity;
        c.graph_relation = ev.graph_relation;
        if (ev.entity_resolution) {
          c.entity_resolution = ev.entity_resolution;
          // The publisher the search is restricted to, for the line above
          // the results; the latest plan of the latest run decides.
          var er = ev.entity_resolution;
          s.scope = (er.status === "resolved" && er.filters && er.names && er.names.length)
            ? er.names[0] : null;
        }
        if (ev.area) s.planArea = ev.area;
        break;

      case "round_started":
        s.round = ev.round;
        c = roundCard(s, run, ev.round);
        c.query = ev.query;
        break;

      case "query_rewrite":
        c = roundCard(s, run, ev.round);
        c.rewrite = {
          input: ev.input, rewritten: ev.rewritten, kept: ev.kept_wording,
          attempted: ev.attempted || null, attempted_reason: ev.attempted_reason || null,
          attempted_dropped: ev.attempted_dropped || [],
          outcome: ev.outcome, required: ev.required_terms,
          protected: ev.protected_terms, dropped: ev.dropped_terms
        };
        break;

      case "path_started":
        c = roundCard(s, run, ev.round);
        c.paths.push({ path: ev.path, label: ev.label, query: ev.query,
                       done: false, rows: [], count: 0 });
        break;

      case "path_results":
        c = roundCard(s, run, ev.round);
        var found = null;
        for (i = 0; i < c.paths.length; i++) {
          if (c.paths[i].path === ev.path && !c.paths[i].done) { found = c.paths[i]; break; }
        }
        if (!found) { found = { path: ev.path }; c.paths.push(found); }
        found.label = ev.label; found.color = ev.color; found.query = ev.query;
        found.place = ev.place; found.relation = ev.intent; found.entity = ev.entity;
        found.count = ev.count; found.duration_ms = ev.duration_ms;
        found.rows = ev.rows; found.done = true;
        break;

      case "fusion":
        if (ev.scope === "all_rounds") {
          c = ensureCard(s, "allrounds", run, 0);
          c.rows = ev.rows; c.count = ev.count; c.inputs = ev.inputs;
        } else {
          c = roundCard(s, run, ev.round);
          c.fusion = { rows: ev.rows, count: ev.count };
        }
        break;

      case "evidence_pool":
        c = roundCard(s, run, ev.round);
        c.pool = { size: ev.size, new_in_top: ev.new_in_top, text: ev.text };
        break;

      case "decision":
        c = ensureCard(s, "decision", run, ev.round);
        c.action = ev.action; c.subquery = ev.subquery; c.text = ev.text;
        c.notes = c.notes || [];
        break;

      case "loop_note":
        c = ensureCard(s, "decision", run, ev.round);
        c.notes = c.notes || [];
        c.notes.push({ code: ev.code, text: ev.text, subquery: ev.subquery || null });
        break;

      case "clarify_check":
        c = ensureCard(s, "clarify", run, 0);
        c.asked = ev.asked; c.question = ev.question; c.stage = ev.asked ? "asking" : "not_needed";
        // The grounded rule reports what it checked; its card stays even
        // when it did not ask, to say why.
        if (ev.features) { c.features = ev.features; c.reason = ev.reason || ""; }
        if (!ev.asked && !ev.features) {
          // Nothing happened worth a card.
          s.timeline = s.timeline.filter(function (x) { return x !== undefined && x.key !== c.key; });
        }
        break;

      case "clarify_question":
        c = ensureCard(s, "clarify", run, 0);
        c.asked = true; c.stage = "awaiting"; c.question = ev.question;
        c.job_id = ev.job_id; c.null_reply = ev.null_reply;
        c.timeout_s = ev.timeout_s;
        if (ev.options) c.options = ev.options;
        s.clarify = { stage: "awaiting", question: ev.question, job_id: ev.job_id,
                      null_reply: ev.null_reply, timeout_s: ev.timeout_s,
                      reply: null, restarts: false };
        if (ev.options) s.clarify.options = ev.options;
        break;

      case "clarify_reply":
        c = ensureCard(s, "clarify", run, 0);
        c.reply = ev.reply; c.skipped = ev.skipped; c.timed_out = ev.timed_out;
        c.restarts = ev.restarts;
        c.stage = ev.timed_out ? "timed_out" : (ev.skipped ? "skipped" : "answered");
        if (s.clarify) {
          s.clarify.stage = c.stage; s.clarify.reply = ev.reply;
          s.clarify.restarts = ev.restarts;
        }
        break;

      case "restart":
        s.restarted = true;
        c = ensureCard(s, "clarify", run, 0);
        c.restart_text = ev.text; c.restart_query = ev.query;
        if (s.clarify) s.clarify.restart_text = ev.text;
        break;

      case "rerank":
        s.rerank = ev;
        c = ensureCard(s, "rerank", run, 0);
        c.data = { candidates: ev.candidates, top_k: ev.top_k, top: ev.top,
                   scores: ev.scores, moved_in: ev.moved_in, signals: ev.signals,
                   duration_ms: ev.duration_ms };
        break;

      case "results":
        s.results = ev.items;
        break;

      case "comparison":
        // Optional, after the answer. Its card goes right after the
        // re-ranking card of the same run, not at the end of the timeline.
        s.comparison = { columns: ev.columns, per_record: ev.per_record,
                         summary: ev.summary };
        if (!findCard(s, "comparison", run, null)) {
          c = { key: "comparison:" + run + ":0:" + s.timeline.length,
                type: "comparison", run: run, round: 0, running: false,
                duration_ms: 0, steps: 0 };
          var at = -1;
          for (i = 0; i < s.timeline.length; i++) {
            if (s.timeline[i].type === "rerank" && s.timeline[i].run === run) at = i;
          }
          if (at >= 0) s.timeline.splice(at + 1, 0, c); else s.timeline.push(c);
        }
        break;

      case "answer_draft":
        s.drafts.push({ attempt: ev.attempt, text: ev.text,
                        revised_because: ev.revised_because, check: null });
        ensureCard(s, "answer", run, 0);
        break;

      case "answer_check":
        for (i = 0; i < s.drafts.length; i++) {
          if (s.drafts[i].attempt === ev.attempt) {
            s.drafts[i].check = { grounded: ev.grounded, relevant: ev.relevant,
                                  passed: ev.passed, problems: ev.problems,
                                  lenient_accepted: ev.lenient_accepted || 0,
                                  lenient_types: ev.lenient_types || null,
                                  detail: ev.detail };
          }
        }
        break;

      case "answer":
        s.answer = ev;
        ensureCard(s, "answer", run, 0);
        break;

      case "notice":
        // The same text shows once per job, with a count; every raw line is kept.
        var seen = null;
        for (i = 0; i < s.notices.length; i++) {
          if (s.notices[i].text === ev.text) { seen = s.notices[i]; break; }
        }
        if (seen) {
          seen.count += 1;
          if (ev.detail) seen.details.push(ev.detail);
        } else {
          s.notices.push({ level: ev.level, text: ev.text, detail: ev.detail,
                          details: ev.detail ? [ev.detail] : [], count: 1,
                          round: s.round, run: run });
        }
        break;

      case "error":
        s.error = { message: ev.message, detail: ev.detail };
        s.status = "error";
        break;

      case "done":
        s.done = { ok: ev.ok, cancelled: ev.cancelled, steps: ev.steps,
                   duration_s: ev.duration_s };
        if (ev.cancelled) s.status = "cancelled";
        else if (s.status !== "error") s.status = ev.ok ? "done" : "error";
        for (i = 0; i < s.timeline.length; i++) s.timeline[i].running = false;
        for (i = 0; i < s.steps.length; i++) {
          if (s.steps[i].status === "running") s.steps[i].status = "done";
        }
        s.progress = "";
        break;

      default:
        break;
    }
    return s;
  }

  // The stream ended without a done event (connection lost).
  function interrupted(prev) {
    var s = clone(prev);
    if (s.status === "running" || s.status === "queued" || s.status === "starting") {
      s.status = "interrupted";
      s.progress = "";
      for (var i = 0; i < s.timeline.length; i++) s.timeline[i].running = false;
    }
    return s;
  }

  // ---- derived views (pure) ----------------------------------------------
  function isLive(s) {
    return s.status === "starting" || s.status === "queued" || s.status === "running";
  }

  function rankMap(s) {
    var m = {};
    (s.results || []).forEach(function (it) { m[it.id] = it.rank; });
    return m;
  }

  function citedSet(s) {
    var m = {};
    if (s.answer) (s.answer.cited || []).forEach(function (id) { m[id] = true; });
    return m;
  }

  // Timeline cards grouped by run. A run that a clarification replaced is
  // labelled "Before your clarification".
  function runGroups(s) {
    var groups = [];
    var maxRun = 0;
    s.timeline.forEach(function (c) { if (c.run > maxRun) maxRun = c.run; });
    for (var r = 0; r <= maxRun; r++) {
      var cards = s.timeline.filter(function (c) { return c.run === r; });
      groups.push({
        run: r,
        label: maxRun > 0 ? (r < maxRun ? "Before your clarification" : "After your clarification") : "",
        collapsed: maxRun > 0 && r < maxRun,
        cards: cards
      });
    }
    return groups;
  }

  // What the comparison view shows, derived from the comparison event.
  // The agent's records that are not in the baseline list are "new": the
  // single search round when its column is shown, else keyword search.
  function comparisonView(cmp) {
    if (!cmp || !cmp.columns) return null;
    var cols = {};
    cmp.columns.forEach(function (c) { cols[c.id] = c; });
    var s = cmp.summary || {};
    var single = cols.single_round, kw = cols.keyword;
    var agent = cols.agent || { records: [] };
    var n = agent.records.length;
    var showSingle = !!(single && single.shown !== false);
    var baseline = showSingle && single.available ? "single_round"
      : (kw && kw.available ? "keyword" : null);
    var headline;
    if (s.same_as_single_round) {
      headline = s.restarted
        ? "After your clarification the agent searched once; its results are the same as a single search round."
        : "The agent searched once; its results are the same as a single search round.";
    } else if (single && single.available) {
      headline = "Compared with a single search round: " + s.changed_vs_single_round +
        " of " + n + " result" + (n === 1 ? "" : "s") + " changed";
    } else {
      headline = "Compared with a single search round: not available";
    }
    var lines = [];
    if (kw && kw.available) {
      lines.push("Compared with keyword search: " + s.changed_vs_keyword + " of " + n + " changed");
    }
    if (s.restarted && cols.before_clarification && cols.before_clarification.available) {
      lines.push("Changed by your clarification: " + s.changed_by_clarification + " of " + n);
    }
    var per = {};
    (cmp.per_record || []).forEach(function (p) { per[p.id] = p; });
    var GRAPH_VIA = { PROVENANCE: "found via related datasets",
                      CO_CONTRIBUTION: "found via the same publisher" };
    function badges(p) {
      var out = [];
      if (!p) return out;
      var first = Math.min.apply(null, p.rounds && p.rounds.length ? p.rounds : [1]);
      if (first > 1) out.push("found in round " + first);
      var paths = p.paths || [];
      if (paths.indexOf("publisher") >= 0 && paths.indexOf("keyword") < 0) {
        out.push("found via the publisher you asked about");
      }
      if (paths.indexOf("graph") >= 0 && paths.indexOf("keyword") < 0 && paths.indexOf("semantic") < 0) {
        out.push(GRAPH_VIA[p.graph_relation] || "found via the knowledge graph");
      }
      if (p.after_clarification) out.push("after your clarification");
      if (kw && kw.available && p.ranks && p.ranks.keyword === null) out.push("not in keyword search top 10");
      return out;
    }
    var records = agent.records.map(function (r) {
      var p = per[r.id];
      var isNew = !!(baseline && p && p.ranks && p.ranks[baseline] === null);
      return { id: r.id, rank: r.rank, title: r.title, organization: r.organization,
               isNew: isNew, badges: isNew ? badges(p) : [], ranks: p ? p.ranks : {},
               paths: p ? p.paths || [] : [], rounds: p ? p.rounds || [] : [] };
    });
    var shown = cmp.columns.filter(function (c) { return c.shown !== false; });
    return { headline: headline, lines: lines, same: !!s.same_as_single_round,
             changed: s.same_as_single_round ? 0 : s.changed_vs_single_round,
             baseline: baseline, columns: shown, all: cmp.columns, records: records };
  }

  // The history the page sends with the next question, exactly as app.py
  // keeps it: the question, then the answer text if there was one, and only
  // the last four messages.
  function historyAfter(history, question, answerText) {
    var h = (history || []).slice();
    h.push({ role: "user", content: question });
    if (answerText) h.push({ role: "assistant", content: answerText });
    return h.slice(-4);
  }

  // Plain-text answer to safe HTML. All text is escaped first; the only
  // markup produced is paragraphs, lists, bold and the citation chips.
  function esc(t) {
    return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function renderAnswer(answer, ranks) {
    if (!answer) return "";
    var segs = answer.segments || [{ type: "text", text: answer.text || "" }];
    var chips = [];
    var raw = segs.map(function (seg) {
      if (seg.type === "text") return seg.text;
      chips.push(seg);
      return "\u0000" + (chips.length - 1) + "\u0000";
    }).join("");
    var blocks = [], list = null;
    function flush() { if (list) { blocks.push(list); list = null; } }
    raw.split(/\r?\n/).forEach(function (line) {
      var m = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
      if (m) {
        if (!list) list = { ul: true, items: [] };
        list.items.push(m[1]);
      } else if (line.trim() === "") {
        flush();
      } else {
        flush();
        blocks.push({ p: line.trim() });
      }
    });
    flush();
    function inline(t) {
      var h = esc(t).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      return h.replace(/\u0000(\d+)\u0000/g, function (_, n) {
        var seg = chips[+n];
        if (!seg) return "";
        if (seg.status === "resolved") {
          var rank = ranks && ranks[seg.id];
          return '<button type="button" class="cite" data-id="' + esc(seg.id) +
            '" title="' + esc(seg.title || "Open dataset") + '">' +
            esc(rank ? rank : "•") + "</button>";
        }
        var why = seg.status === "ambiguous"
          ? "This reference matches more than one dataset"
          : "This reference does not match any result";
        return '<span class="cite cite-muted" title="' + esc(why) + '">?</span>';
      });
    }
    return blocks.map(function (b) {
      if (b.ul) return "<ul>" + b.items.map(function (t) { return "<li>" + inline(t) + "</li>"; }).join("") + "</ul>";
      return "<p>" + inline(b.p) + "</p>";
    }).join("");
  }


  // Segments for a draft that has not been through the server's resolver:
  // the same marker rule (eight hex characters, at least one a-f, not inside
  // a longer word), resolved against the result list.
  function segmentsFromText(text, results) {
    var by = {};
    (results || []).forEach(function (it) {
      var k = String(it.id).slice(0, 8).toLowerCase();
      (by[k] = by[k] || []).push(it);
    });
    var re = /(?<![0-9a-zA-Z])([0-9a-f]{8})(?![0-9a-zA-Z])/g;
    var segs = [], pos = 0, m;
    text = text || "";
    while ((m = re.exec(text))) {
      if (!/[a-f]/.test(m[1])) continue;
      var start = m.index, end = m.index + 8;
      if (start > 0 && text[start - 1] === "[" && text[end] === "]") { start -= 1; end += 1; }
      if (start > pos) segs.push({ type: "text", text: text.slice(pos, start) });
      var hit = by[m[1].toLowerCase()] || [];
      if (hit.length === 1) segs.push({ type: "cite", status: "resolved", id: hit[0].id, title: hit[0].title });
      else if (hit.length > 1) segs.push({ type: "cite", status: "ambiguous" });
      else segs.push({ type: "cite", status: "unresolved" });
      pos = end;
      re.lastIndex = end;
    }
    if (pos < text.length) segs.push({ type: "text", text: text.slice(pos) });
    return segs;
  }

  root.Reducer = {
    PATHS: PATHS, PATH_ORDER: PATH_ORDER,
    initial: initial, reduce: reduce, interrupted: interrupted,
    isLive: isLive, rankMap: rankMap, citedSet: citedSet,
    segmentsFromText: segmentsFromText,
    runGroups: runGroups, historyAfter: historyAfter, renderAnswer: renderAnswer,
    comparisonView: comparisonView,
    escapeHtml: esc
  };
})(typeof window !== "undefined" ? window : globalThis);
