/* The comparison view: the agent's final ten next to simpler searches.
 * CompareSummary sits above the result list in Standard mode (one line, opens
 * into side-by-side columns); CompareTable is the body of the Comparison card
 * in Advanced mode (each record's rank in every list). Both read
 * Reducer.comparisonView(job.comparison). */
(function () {
  var C = window.UIComponents, R = window.Reducer, P = R.PATHS;

  C.CompareSummary = {
    props: { cmp: Object, memo: Object },
    emits: ["open"],
    data() { return { localOpen: false }; },
    computed: {
      v() { return R.comparisonView(this.cmp); },
      open: {
        get() { return this.memo ? !!this.memo.cards["compare:standard"] : this.localOpen; },
        set(x) { if (this.memo) this.memo.cards["compare:standard"] = x; else this.localOpen = x; }
      }
    },
    template: '<section v-if="v" class="compare fade-in" aria-label="Comparison with simpler search">' +
      '<button type="button" class="compare-head" :aria-expanded="open" @click="open = !open">' +
      '<span class="compare-text"><span class="compare-line">{{ v.headline }}</span>' +
      '<span v-for="(l, i) in v.lines" :key="i" class="small muted compare-sub">{{ l }}</span></span>' +
      '<span class="chev" :class="{open: open}" aria-hidden="true">▸</span></button>' +
      '<div v-if="open" class="compare-cols">' +
      '<div v-for="col in v.columns" :key="col.id" class="compare-col" :class="{agent: col.id === \'agent\'}">' +
      '<div class="compare-col-head"><div class="compare-col-title">{{ col.label }}</div>' +
      '<div class="small muted compare-q" :title="col.question_used">“{{ col.question_used }}”</div></div>' +
      '<p v-if="!col.available" class="small muted">Not available. {{ col.reason }}</p>' +
      '<ol v-else-if="col.id !== \'agent\'" class="compare-list">' +
      '<li v-for="r in col.records" :key="r.id"><button type="button" class="compare-item" @click="$emit(\'open\', r.id)">' +
      '<span class="rank-sm">{{ r.rank }}</span><span class="compare-t">{{ r.title }}<span class="small muted" style="display:block">{{ r.organization }}</span></span></button></li></ol>' +
      '<ol v-else class="compare-list">' +
      '<li v-for="r in v.records" :key="r.id"><button type="button" class="compare-item" :class="{\'is-new\': r.isNew}" @click="$emit(\'open\', r.id)">' +
      '<span class="rank-sm">{{ r.rank }}</span><span class="compare-t">{{ r.title }}<span class="small muted" style="display:block">{{ r.organization }}</span>' +
      '<span v-if="r.badges.length" class="tags"><span v-for="b in r.badges" :key="b" class="pill pill-accent">{{ b }}</span></span></span></button></li></ol>' +
      '</div>' +
      '<p v-if="v.same" class="small muted compare-note">The single search round is not shown because it is the same list as the agent’s.</p>' +
      '</div></section>'
  };

  C.CompareTable = {
    props: { cmp: Object },
    emits: ["open"],
    computed: {
      v() { return R.comparisonView(this.cmp); },
      others() {
        return this.v ? this.v.all.filter(function (c) { return c.id !== "agent"; }) : [];
      }
    },
    methods: {
      rankIn(r, col) {
        if (!col.available) return "n/a";
        var x = r.ranks ? r.ranks[col.id] : null;
        return x === null || x === undefined ? "—" : x;
      },
      found(r) {
        var paths = (r.paths || []).map(function (p) { return (P[p] || { label: p }).label; });
        var rounds = r.rounds && r.rounds.length ? "round " + r.rounds.join(", ") : "";
        return [paths.join(", "), rounds].filter(Boolean).join(" · ");
      }
    },
    template: '<div v-if="v" class="compare-table-wrap">' +
      '<p v-for="(l, i) in v.lines" :key="i" class="small muted">{{ l }}</p>' +
      '<div class="compare-table-scroll"><table class="compare-table">' +
      '<thead><tr><th scope="col">Agent</th><th scope="col">Dataset</th>' +
      '<th v-for="col in others" :key="col.id" scope="col">{{ col.label }}' +
      '<span v-if="col.shown === false" class="small muted" style="display:block">same as the agent</span></th>' +
      '<th scope="col">Found by</th></tr></thead>' +
      '<tbody><tr v-for="r in v.records" :key="r.id" :class="{\'is-new\': r.isNew}">' +
      '<td class="num">{{ r.rank }}</td>' +
      '<td><button type="button" class="linkish" @click="$emit(\'open\', r.id)">{{ r.title }}</button>' +
      '<span v-if="r.badges.length" class="tags"><span v-for="b in r.badges" :key="b" class="pill pill-accent">{{ b }}</span></span></td>' +
      '<td v-for="col in others" :key="col.id" class="num">{{ rankIn(r, col) }}</td>' +
      '<td class="small muted">{{ found(r) }}</td></tr></tbody></table></div>' +
      '<p class="small muted">A dash means the dataset is not in that list’s top 10.</p>' +
      '<p v-for="col in others" v-show="!col.available" :key="\'na\' + col.id" class="small muted">{{ col.label }}: not available. {{ col.reason }}</p>' +
      '</div>'
  };
})();
