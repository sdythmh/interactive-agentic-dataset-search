/* Every draft, the two checks, the reason for each revision, then the final
 * answer with its citations resolved to datasets. */
(function () {
  var R = window.Reducer;
  window.UIComponents.AnswerAttempts = {
    props: { drafts: Array, answer: Object, results: Array, memo: Object },
    emits: ["open"],
    data() { return { localOpen: {} }; },
    methods: {
      isOpen(d) {
        var m = this.memo ? this.memo.drafts : this.localOpen;
        return m[d.attempt] === undefined ? true : !!m[d.attempt];
      },
      toggle(d) {
        var m = this.memo ? this.memo.drafts : this.localOpen;
        m[d.attempt] = !this.isOpen(d);
      },
      plural(n) { return n + " citation" + (n === 1 ? "" : "s"); },
      closeMatches(check) {
        // "3 citations accepted as close matches: title (1), description (1), publisher name (1)."
        var t = check.lenient_types;
        if (!t) return null;
        var names = { title: "title", description: "description", publisher: "publisher name" };
        var parts = [];
        ["title", "description", "publisher"].forEach(function (k) {
          if (t[k]) parts.push(names[k] + " (" + t[k] + ")");
        });
        var n = 0;
        for (var k in t) n += t[k];
        return n + " citation" + (n === 1 ? "" : "s") + " accepted as close matches: " + parts.join(", ") + ".";
      },
      html(text) {
        var segs = R.segmentsFromText(text, this.results);
        return R.renderAnswer({ segments: segs, text: text }, R.rankMap({ results: this.results }));
      },
      onClick(e) {
        var b = e.target.closest && e.target.closest("button.cite");
        if (b && b.dataset.id) this.$emit("open", b.dataset.id);
      },
      cited() {
        var ranks = R.rankMap({ results: this.results });
        var byId = {};
        (this.results || []).forEach(r => { byId[r.id] = r; });
        return ((this.answer && this.answer.cited) || []).map(id => ({ id: id, rank: ranks[id], title: byId[id] ? byId[id].title : "" }));
      }
    },
    template: '<div style="display:grid;gap:10px" @click="onClick">' +
      '<div v-for="d in drafts" :key="d.attempt" class="attempt">' +
      '<div class="attempt-head"><button type="button" class="steps-toggle" :aria-expanded="isOpen(d)" @click.stop="toggle(d)"><span class="chev" :class="{open: isOpen(d)}" aria-hidden="true">\u25B8</span><strong>Draft {{ d.attempt }}</strong></button>' +
      '<template v-if="d.check"><span class="pill" :class="d.check.grounded ? \'pill-ok\' : \'pill-bad\'">{{ d.check.grounded ? "✓" : "✗" }} Grounded in the sources</span>' +
      '<span class="pill" :class="d.check.relevant ? \'pill-ok\' : \'pill-bad\'">{{ d.check.relevant ? "✓" : "✗" }} Answers the question</span></template>' +
      '<span v-else class="pill"><span class="spinner"></span>Checking</span></div>' +
      '<template v-if="isOpen(d)">' +
      '<div v-if="d.revised_because" class="small muted">Written again because {{ d.revised_because }}</div>' +
      '<div class="answer" v-html="html(d.text)"></div>' +
      '<div v-if="d.check && d.check.lenient_accepted && closeMatches(d.check)" class="small muted">{{ closeMatches(d.check) }}</div>' +
      '<div v-else-if="d.check && d.check.lenient_accepted" class="small muted">{{ plural(d.check.lenient_accepted) }} accepted because the text matches the dataset\'s description.</div>' +
      '<ul v-if="d.check && d.check.problems && d.check.problems.length" class="small" style="margin:0;padding-left:18px;color:#B91C1C">' +
      '<li v-for="(p, i) in d.check.problems" :key="i">{{ p }}</li></ul></template></div>' +
      '<div v-if="answer" class="attempt" style="background:var(--surface-2)">' +
      '<div class="attempt-head"><strong>Final answer</strong>' +
      '<span v-if="answer.refused" class="pill pill-warn">No draft passed the checks</span>' +
      '<span v-else class="pill pill-ok">Passed the checks</span></div>' +
      '<div v-if="cited().length" class="cite-list"><span class="small muted" style="align-self:center">Cites</span>' +
      '<button v-for="c in cited()" :key="c.id" type="button" class="mini-btn" :title="c.title" @click.stop="$emit(\'open\', c.id)"><span class="num">{{ c.rank }}</span> · {{ c.title.length > 40 ? c.title.slice(0, 39) + "…" : c.title }}</button></div>' +
      '<p v-else class="small muted">The answer cites no dataset by reference.</p>' +
      '</div></div>'
  };
})();
