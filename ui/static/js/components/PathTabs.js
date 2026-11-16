/* One tab per retrieval path with its ranked list. */
(function () {
  var P = window.Reducer.PATHS;
  window.UIComponents.PathTabs = {
    props: { paths: Array, memo: Object, memoKey: String },
    emits: ["open"],
    data() { return { localSel: 0, localAll: {} }; },
    computed: {
      sel() { return this.memo ? (this.memo.tabs[this.memoKey] || 0) : this.localSel; },
      allMap() { return this.memo ? this.memo.all : this.localAll; },
      current() { return this.paths[Math.min(this.sel, this.paths.length - 1)]; },
      shown() {
        var p = this.current; if (!p) return [];
        return this.allMap[this.memoKey + ':' + p.path] ? p.rows : p.rows.slice(0, 10);
      }
    },
    methods: {
      pick(i) { if (this.memo) this.memo.tabs[this.memoKey] = i; else this.localSel = i; },
      toggleAll(p) { var k = this.memoKey + ':' + p.path; this.allMap[k] = !this.allMap[k]; },
      color(p) { return p.color || (P[p.path] && P[p.path].color) || "#9AA0B4"; },
      label(p) { return p.label || (P[p.path] && P[p.path].label) || "Search"; },
      cap(t) { return t ? t.charAt(0).toUpperCase() + t.slice(1) : ""; }
    },
    template: '<div v-if="paths.length">' +
      '<div class="tabs" role="tablist">' +
      '<button v-for="(p, i) in paths" :key="p.path + i" type="button" role="tab" class="tab" :aria-selected="i === sel" @click="pick(i)">' +
      '<i class="dot" :style="{background: color(p)}"></i>{{ label(p) }}' +
      '<span class="pill num">{{ p.done ? p.count : "…" }}</span></button></div>' +
      '<div v-if="current" style="padding-top:10px">' +
      '<div class="small muted" style="margin-bottom:6px">' +
      '<span v-if="current.query">Query: “{{ current.query }}”</span>' +
      '<span v-if="current.place"> · Place: {{ current.place }}</span>' +
      '<span v-if="current.relation"> · Relation: {{ current.relation }}</span>' +
      '<span v-if="current.duration_ms"> · <span class="num">{{ (current.duration_ms / 1000).toFixed(2) }}s</span></span></div>' +
      '<p v-if="current.done && !current.rows.length" class="muted">No results from this search</p>' +
      '<div v-else-if="!current.done" class="skeleton" style="height:80px"></div>' +
      '<div v-else class="rows">' +
      '<button v-for="r in shown" :key="r.id" type="button" class="row-item" @click="$emit(\'open\', r.id)">' +
      '<span class="rank-sm">{{ r.rank }}</span>' +
      '<span style="min-width:0"><span class="t" style="display:block">{{ r.title }}</span>' +
      '<span class="sub" style="display:block">{{ r.organization }}<template v-if="r.note"> · {{ r.note }}</template></span></span>' +
      '<span v-if="r.type" class="pill">{{ cap(r.type) }}</span></button></div>' +
      '<button v-if="current.done && current.rows.length > 10" type="button" class="link-btn" style="margin-top:8px" @click="toggleAll(current)">' +
      '{{ allMap[memoKey + \':\' + current.path] ? "Show top 10 only" : "Show all " + current.rows.length }}</button>' +
      '</div></div>'
  };
})();
