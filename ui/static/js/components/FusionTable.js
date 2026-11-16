/* The combined ranking, with a coloured dot and rank for each path that
 * returned the dataset. */
(function () {
  var P = window.Reducer.PATHS;
  window.UIComponents.FusionTable = {
    props: { rows: Array, memo: Object, memoKey: String },
    emits: ["open"],
    data() { return { localAll: false }; },
    computed: {
      all: {
        get() { return this.memo ? !!this.memo.all[this.memoKey + ':fusion'] : this.localAll; },
        set(v) { if (this.memo) this.memo.all[this.memoKey + ':fusion'] = v; else this.localAll = v; }
      },
      shown() { return this.all ? this.rows : this.rows.slice(0, 10); }
    },
    methods: {
      path(id) { return P[id] || { label: "Search", color: "#9AA0B4" }; },
      ranks(r) {
        var out = [];
        Object.keys(P).forEach(function (k) {
          if (r.path_ranks && r.path_ranks[k] !== undefined) out.push({ id: k, rank: r.path_ranks[k] });
          else if (r.paths && r.paths.indexOf(k) >= 0) out.push({ id: k, rank: null });
        });
        return out;
      }
    },
    template: '<div><div class="rows">' +
      '<button v-for="r in shown" :key="r.id" type="button" class="row-item" @click="$emit(\'open\', r.id)">' +
      '<span class="rank-sm">{{ r.rank }}</span>' +
      '<span style="min-width:0"><span class="t" style="display:block">{{ r.title }}</span>' +
      '<span class="sub" style="display:block">{{ r.organization }}</span></span>' +
      '<span class="pr"><span v-for="p in ranks(r)" :key="p.id" :title="path(p.id).label + (p.rank ? \': position \' + p.rank : \'\')">' +
      '<i class="dot" :style="{background: path(p.id).color}"></i>{{ p.rank || "" }}</span></span></button></div>' +
      '<button v-if="rows.length > 10" type="button" class="link-btn" style="margin-top:8px" @click="all = !all">{{ all ? "Show top 10 only" : "Show all " + rows.length }}</button>' +
      '<div class="legend"><span v-for="(p, k) in { keyword: 1, semantic: 1, spatial: 1, graph: 1 }" :key="k"><i class="dot" :style="{background: path(k).color}" style="margin-right:6px"></i>{{ path(k).label }}</span>' +
      '<span>Numbers show the position in that search</span></div></div>'
  };
})();
