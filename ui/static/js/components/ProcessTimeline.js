/* Advanced mode: one expandable card per step of the search, live. */
(function () {
  var C = window.UIComponents, R = window.Reducer, P = R.PATHS;
  function secs(ms) { return ms ? (ms / 1000).toFixed(1) + "s" : ""; }
  C.ProcessTimeline = {
    props: { job: Object, memo: Object },
    emits: ["open"],
    components: {
      PathTabs: C.PathTabs, FusionTable: C.FusionTable, SlopeChart: C.SlopeChart,
      ScoreBars: C.ScoreBars, AnswerAttempts: C.AnswerAttempts, MapView: C.MapView,
      Notice: C.Notice, CompareTable: C.CompareTable
    },
    data() { return { localManual: {}, localGroups: {} }; },
    computed: {
      manual() { return this.memo ? this.memo.cards : this.localManual; },
      groupOpen() { return this.memo ? this.memo.groups : this.localGroups; },
      groups() { return R.runGroups(this.job); },
      live() { return R.isLive(this.job); }
    },
    methods: {
      secs,
      path(id) { return P[id] || { label: "Search", color: "#9AA0B4" }; },
      cards(g) {
        var out = [], n = 0, self = this;
        g.cards.forEach(function (c) {
          if (c.type === "other") return;
          var item = Object.assign({}, c);
          n += 1; item.num = n;
          out.push(item);
        });
        return out;
      },
      title(c) {
        return { understanding: "Understanding", plan: "Search plan", round: "Round " + c.round,
                 decision: "Agent decision", clarify: "Clarifying question",
                 rerank: "Re-ranking", answer: "Answer", allrounds: "Combined ranking across rounds",
                 comparison: "Comparison" }[c.type] || "Step";
      },
      summary(c) {
        var job = this.job;
        switch (c.type) {
          case "understanding":
            return c.contextual ? "Understood in context as: " + c.contextual : (c.original ? "Your question stood on its own" : "");
          case "plan":
            return (c.paths || []).map(p => p.label).join(", ") + (c.place ? " · Place: " + c.place : "");
          case "round":
            var pr = (c.paths || []).filter(p => p.done).map(p => p.count);
            if (!c.rewrite) return "";
            if (!c.rewrite.kept) return "Rewritten query: " + c.rewrite.rewritten;
            return c.round >= 2 ? "Kept the follow-up query as written" : "Kept your wording";
          case "decision": return (c.text || "").replace(/^Agent decision:\s*/, "");
          case "clarify":
            if (c.stage === "awaiting" || c.stage === "asking") return "Waiting for your answer";
            if (c.stage === "answered") return "You answered — searching again";
            if (c.stage === "skipped") return "Skipped";
            if (c.stage === "timed_out") return "No answer in time";
            if (c.stage === "not_needed") return "Not asked";
            return "";
          case "rerank":
            return c.data ? c.data.candidates + " candidates · " + (c.data.moved_in || []).length + " moved into the top " + c.data.top_k : "";
          case "answer":
            var n = (job.drafts || []).length;
            return n ? n + " draft" + (n === 1 ? "" : "s") + (job.answer ? "" : " so far") : "";
          case "allrounds": return c.count + " datasets";
          case "comparison":
            var v = R.comparisonView(job.comparison);
            return v ? v.headline : "";
          default: return "";
        }
      },
      isOpen(c) {
        var k = c.key;
        if (this.manual[k] !== undefined) return this.manual[k];
        return !!c.running || c.type === "clarify" && (c.stage === "awaiting");
      },
      toggle(c) { this.manual[c.key] = !this.isOpen(c); },
      groupIsOpen(g) {
        return this.groupOpen[g.run] !== undefined ? this.groupOpen[g.run] : !g.collapsed;
      },
      toggleGroup(g) { this.groupOpen[g.run] = !this.groupIsOpen(g); },
      // From round 2 on the query is the agent's follow-up, not the user's
      // words, so sentences about "your wording" are said about the query.
      said(c, text) {
        if (!text || c.round < 2) return text;
        return text
          .replace("Your question already matches the catalogue's wording.", "The follow-up query already matches the catalogue's wording.")
          .replace("so your wording was kept", "so the follow-up query was kept as written")
          .replace("Your wording was used as written.", "The follow-up query was used as written.");
      },
      noticesFor(g, c) {
        return this.job.notices.filter(n => n.run === g.run && n.round === c.round);
      },
      cap(t) { return t ? t.charAt(0).toUpperCase() + t.slice(1) : ""; },
      entityText(f) {
        if (!f.entity) return "none named";
        var n = f.family_size, t = f.title_hits;
        switch (f.entity_status) {
          case "org_grounded": return "\u201c" + f.entity + "\u201d: " + (n === 1 ? "one publisher" : n + " publishers");
          case "org_ambiguous": return "\u201c" + f.entity + "\u201d: matches " + n + " publishers, too many to be one";
          case "title_grounded": return "\u201c" + f.entity + "\u201d: in the titles of " + t + " dataset" + (t === 1 ? "" : "s");
          case "title_ambiguous": return "\u201c" + f.entity + "\u201d: in the titles of " + t + " datasets, too many to be one";
          default: return "\u201c" + f.entity + "\u201d: not a publisher or a dataset title";
        }
      }
    },
    template: '<div class="timeline">' +
      '<div v-if="!groups.length || !groups[0].cards.length" class="skeleton" style="height:64px"></div>' +
      '<template v-for="g in groups" :key="g.run">' +
      '<button v-if="g.label" type="button" class="group-head" @click="toggleGroup(g)" :aria-expanded="groupIsOpen(g)">' +
      '<span class="chev" :class="{open: groupIsOpen(g)}" aria-hidden="true">▸</span>{{ g.label }}</button>' +
      '<template v-if="groupIsOpen(g)">' +
      '<section v-for="c in cards(g)" :key="c.key" class="tcard fade-in" :class="{running: c.running}">' +
      '<button type="button" class="tcard-head" :aria-expanded="isOpen(c)" @click="toggle(c)">' +
      '<span class="tnum">{{ c.num }}</span>' +
      '<span style="min-width:0"><span class="tcard-title">{{ title(c) }}</span><br><span class="tcard-sum" style="display:block">{{ summary(c) }}</span></span>' +
      '<span class="dur"><span v-if="c.running" class="spinner" aria-hidden="true"></span><template v-else>{{ secs(c.duration_ms) }}</template></span>' +
      '<span class="chev" :class="{open: isOpen(c)}" aria-hidden="true">▸</span></button>' +
      '<div v-if="isOpen(c)" class="tcard-body">' +

      '<template v-if="c.type === \'understanding\'">' +
      '<div><div class="field-label">Your question</div><div class="quote">{{ c.original }}</div></div>' +
      '<div v-if="c.contextual"><div class="field-label">Understood in context as</div><div class="quote">{{ c.contextual }}</div></div>' +
      '<p v-else class="muted">Your question was used as written.</p></template>' +

      '<template v-else-if="c.type === \'plan\'">' +
      '<div><div class="field-label">Searches used</div><div class="tags">' +
      '<span v-for="p in c.paths" :key="p.id" class="pill"><i class="dot" :style="{background: path(p.id).color}"></i>{{ path(p.id).label }}</span></div></div>' +
      '<div v-for="s in c.skipped" :key="s.id" class="small muted">{{ path(s.id).label }} was skipped: {{ s.reason }}</div>' +
      '<div v-if="c.place"><div class="field-label">Place</div><div>{{ c.place }}</div>' +
      '<div v-if="c.area" style="margin-top:8px"><MapView small :outline="c.area.geojson" :outline-label="\'Area searched: \' + c.area.name" shape-label="" /></div>' +
      '<p v-else class="small muted" style="margin-top:4px">No area could be found for this place, so spatial search had nothing to filter on.</p></div>' +
      '<div v-if="c.entity"><div class="field-label">Named dataset or organisation</div><div>{{ c.entity }}</div></div>' +
      '<div v-if="c.graph_relation"><div class="field-label">Relation looked for</div><div>{{ c.graph_relation }}</div></div>' +
      '<div v-if="c.entity_resolution && c.entity_resolution.text"><div class="field-label">Publisher</div><div>{{ c.entity_resolution.text }}</div>' +
      '<p v-if="c.entity_resolution.reason" class="small muted" style="margin-top:4px">{{ c.entity_resolution.reason }}</p>' +
      '<details v-if="c.entity_resolution.candidates && c.entity_resolution.candidates.length" class="small" style="margin-top:6px">' +
      '<summary>Publishers considered ({{ c.entity_resolution.candidates.length }})<template v-if="c.entity_resolution.guess"> \u00b7 first guess: {{ c.entity_resolution.guess }}</template></summary>' +
      '<ul class="cand-list"><li v-for="x in c.entity_resolution.candidates" :key="x.name" :class="{chosen: x.chosen}">' +
      '{{ x.name }} <span class="muted">({{ x.datasets }} dataset{{ x.datasets === 1 ? "" : "s" }})</span><span v-if="x.chosen" class="pill pill-accent" style="margin-left:6px">chosen</span></li></ul></details></div></template>' +

      '<template v-else-if="c.type === \'round\'">' +
      '<div><div class="field-label">{{ c.round >= 2 ? "Follow-up query" : "Query used" }}</div><div class="quote">{{ c.query }}</div></div>' +
      '<div v-if="c.rewrite"><div class="field-label">Query rewrite</div>' +
      '<div v-if="c.rewrite.kept" class="quote">{{ c.round >= 2 ? "Kept the follow-up query as written" : "Kept your wording" }}</div>' +
      '<div v-else class="quote">Rewritten query: {{ c.rewrite.rewritten }}</div>' +
      '<p v-if="c.rewrite.attempted" class="small" style="margin-top:4px">Tried: \u201c{{ c.rewrite.attempted }}\u201d \u2014 not used, because {{ c.rewrite.attempted_reason }}</p>' +
      '<p class="small muted" style="margin-top:4px">{{ said(c, c.rewrite.outcome) }}</p>' +
      '<p v-if="c.rewrite.required && c.rewrite.required.length" class="small">Terms kept: {{ c.rewrite.required.join(", ") }}</p>' +
      '<p v-if="c.rewrite.dropped && c.rewrite.dropped.length" class="small muted">Words not in the catalogue and left out: {{ c.rewrite.dropped.join(", ") }}</p></div>' +
      '<div v-if="c.paths.length"><div class="field-label">Results from each search</div><PathTabs :paths="c.paths" :memo="memo" :memo-key="c.key" @open="$emit(\'open\', $event)" /></div>' +
      '<div v-if="c.fusion"><div class="field-label">Combined ranking</div><FusionTable :rows="c.fusion.rows" :memo="memo" :memo-key="c.key" @open="$emit(\'open\', $event)" />' +
      '<p v-if="c.pool" class="small muted" style="margin-top:6px">{{ c.pool.text }}</p></div>' +
      '<Notice v-for="(n, i) in noticesFor(g, c)" :key="i" :notice="n" :memo="memo" /></template>' +

      '<template v-else-if="c.type === \'decision\'">' +
      '<div class="quote">{{ c.text }}</div>' +
      '<div v-for="(n, i) in c.notes" :key="i" class="small muted">{{ n.text }}<template v-if="n.subquery"> “{{ n.subquery }}”</template></div></template>' +

      '<template v-else-if="c.type === \'clarify\'">' +
      '<div v-if="c.features"><div class="field-label">What was checked</div>' +
      '<p>{{ c.reason }}</p>' +
      '<div class="feat-grid small" style="margin-top:6px">' +
      '<span class="muted">Place</span><span>{{ c.features.place ? c.features.place + (c.features.place_resolved ? " (in the place list)" : " (not in the place list)") : "none named" }}</span>' +
      '<span class="muted">Named publisher or dataset</span><span>{{ entityText(c.features) }}</span>' +
      '<span class="muted">Publishers in the first ten results</span><span>{{ c.features.n_pub }}</span>' +
      '<span class="muted">Largest publisher\u2019s share</span><span>{{ Math.round(100 * c.features.top_share) }}%</span></div></div>' +
      '<div v-if="c.question"><div class="field-label">Question</div><div class="quote">{{ c.question }}</div>' +
      '<div v-if="c.options && c.options.length" class="tags" style="margin-top:6px"><span v-for="o in c.options" :key="o.value" class="pill">{{ o.label }}</span></div></div>' +
      '<div v-if="c.stage === \'answered\' || c.stage === \'skipped\' || c.stage === \'timed_out\'"><div class="field-label">Your reply</div>' +
      '<div class="quote">{{ c.stage === "answered" ? c.reply : (c.stage === "timed_out" ? "No answer in time" : "Skipped") }}</div></div>' +
      '<div v-if="c.restart_text" class="pill pill-accent" style="justify-self:start">{{ c.restart_text }}</div>' +
      '<div v-else-if="c.stage === \'skipped\' || c.stage === \'timed_out\'" class="small muted">The search went on without it.</div></template>' +

      '<template v-else-if="c.type === \'rerank\' && c.data">' +
      '<p>{{ c.data.candidates }} candidates were scored for relevance to your question.</p>' +
      '<div v-if="c.data.signals && c.data.signals.length" class="small muted">Signals used: {{ c.data.signals.map(s => s.label).join(", ") }}</div>' +
      '<p v-if="c.data.signals && c.data.signals.some(s => s.id === \'publisher\')" class="small">The publisher you asked about also counted in the ordering.</p>' +
      '<div><div class="field-label">Position before and after</div><SlopeChart :top="c.data.top" @open="$emit(\'open\', $event)" /></div>' +
      '<div><div class="field-label">Relevance scores</div><ScoreBars :top="c.data.top" /></div></template>' +

      '<template v-else-if="c.type === \'comparison\'"><CompareTable :cmp="job.comparison" @open="$emit(\'open\', $event)" /></template>' +

      '<template v-else-if="c.type === \'allrounds\'"><FusionTable :rows="c.rows" :memo="memo" :memo-key="c.key" @open="$emit(\'open\', $event)" /></template>' +

      '<template v-else-if="c.type === \'answer\'">' +
      '<AnswerAttempts :drafts="job.drafts" :answer="job.answer" :results="job.results" :memo="memo" @open="$emit(\'open\', $event)" /></template>' +
      '</div></section></template></template></div>'
  };
})();
