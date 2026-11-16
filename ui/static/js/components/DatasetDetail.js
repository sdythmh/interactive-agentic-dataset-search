(function () {
  var C = window.UIComponents, P = window.Reducer.PATHS;
  C.DatasetDetail = {
    props: { corpus: String, id: String, item: Object, planArea: Object, replay: Boolean, cited: Boolean },
    emits: ["back", "open"],
    components: { MapView: C.MapView, ConnectionsGraph: C.ConnectionsGraph },
    data() { return { rec: null, graph: null, loading: true, err: "", graphErr: "", graphLoading: true, token: 0 }; },
    watch: { id() { this.load(); } },
    mounted() { this.load(); },
    methods: {
      day(v) {
        var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v || "");
        if (!m) return v;
        var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
        return parseInt(m[3], 10) + " " + months[parseInt(m[2], 10) - 1] + " " + m[1];
      },
      full(v) { return (v || "").replace("T", " "); },
      cap(t) { return t ? t.charAt(0).toUpperCase() + t.slice(1) : ""; },
      path(id) { return P[id] || { label: "Search", color: "#9AA0B4" }; },
      load() {
        var vm = this, token = ++this.token;
        this.loading = true; this.err = ""; this.rec = null; this.graph = null;
        this.graphErr = ""; this.graphLoading = true;
        if (this.replay) {
          this.loading = false; this.graphLoading = false;
          this.err = "Dataset details are not available in a replay.";
          return;
        }
        GeoApi.record(this.corpus, this.id).then(function (r) {
          if (token !== vm.token) return; vm.rec = r; vm.loading = false;
        }).catch(function (e) {
          if (token !== vm.token) return; vm.loading = false;
          vm.err = e.status === 404 ? "This dataset is not in the catalogue index." : "The dataset could not be loaded. " + e.message;
        });
        GeoApi.neighbours(this.corpus, this.id).then(function (g) {
          if (token !== vm.token) return; vm.graph = g; vm.graphLoading = false;
        }).catch(function (e) {
          if (token !== vm.token) return; vm.graphLoading = false;
          vm.graphErr = e.status === 404 ? "This dataset has no entry in the knowledge graph." : "The connections could not be loaded.";
        });
      }
    },
    computed: {
      hasGraphNodes() { return this.graph && this.graph.nodes && this.graph.nodes.length > 1; },
      pathRanks() {
        var it = this.item; if (!it || !it.path_ranks) return [];
        return Object.keys(it.path_ranks).map(k => ({ id: k, rank: it.path_ranks[k] }));
      },
      rows() {
        if (!this.rec) return [];
        var vm = this;
        return this.rec.fields.filter(f => ["title", "tags"].indexOf(f.key) < 0).map(function (f) {
          if (f.key === "created" || f.key === "modified") {
            return { key: f.key, label: f.label, value: vm.day(f.value), title: vm.full(f.value) };
          }
          if (f.key === "type") return { key: f.key, label: f.label, value: vm.cap(f.value), title: "" };
          return { key: f.key, label: f.label, value: f.value, title: "" };
        });
      }
    },
    template: '<article class="detail fade-in">' +
      '<div v-if="loading"><div class="skeleton" style="height:28px;width:70%"></div><div class="skeleton" style="height:18px;width:40%;margin-top:12px"></div>' +
      '<div class="skeleton" style="height:120px;margin-top:20px"></div></div>' +
      '<div v-else-if="err" class="error-panel" role="alert">{{ err }}</div>' +
      '<template v-else>' +
      '<h1>{{ rec.title }}</h1>' +
      '<div class="meta"><span v-if="rec.organization">{{ rec.organization }}</span>' +
      '<span v-if="rec.type" class="pill">{{ cap(rec.type) }}</span><span v-if="cited" class="pill pill-accent">Cited</span>' +
      '<a v-if="rec.url" class="btn" :href="rec.url" target="_blank" rel="noopener noreferrer">Open on {{ rec.portal }}</a></div>' +
      '<section class="section" v-if="rec.description"><h2>Description</h2><p class="description">{{ rec.description }}</p></section>' +
      '<section class="section" v-if="rows.length"><h2>Details</h2><div class="kv">' +
      '<template v-for="f in rows" :key="f.key"><div class="k">{{ f.label }}</div><div :title="f.title || null">{{ f.value }}</div></template></div></section>' +
      '<section class="section" v-if="rec.tags && rec.tags.length"><h2>Tags</h2><div class="tags"><span v-for="t in rec.tags" :key="t" class="tag">{{ t }}</span></div></section>' +
      '<section class="section" v-if="rec.formats && rec.formats.length"><h2>Formats</h2><div class="tags"><span v-for="t in rec.formats" :key="t" class="pill">{{ t }}</span></div></section>' +
      '<section class="section" v-if="rec.geometry"><h2>Map</h2>' +
      '<MapView :shape="rec.geometry.geojson" :center="rec.geometry.center" :outline="planArea ? planArea.geojson : null" :outline-label="planArea ? \'Area searched: \' + planArea.name : \'\'" />' +
      '<p class="small muted" style="margin-top:6px">West {{ rec.geometry.bbox[0].toFixed(3) }}, south {{ rec.geometry.bbox[1].toFixed(3) }}, east {{ rec.geometry.bbox[2].toFixed(3) }}, north {{ rec.geometry.bbox[3].toFixed(3) }}</p></section>' +
      '<section class="section"><h2>Connections</h2>' +
      '<div v-if="graphLoading" class="skeleton" style="height:200px"></div>' +
      '<p v-else-if="graphErr" class="muted">{{ graphErr }}</p>' +
      '<p v-else-if="!hasGraphNodes" class="muted">This dataset has no recorded connections.</p>' +
      '<ConnectionsGraph v-else :graph="graph" @open="$emit(\'open\', $event)" /></section>' +
      '<section class="section" v-if="item"><h2>Why it was found</h2><div class="why">' +
      '<div class="why-row"><span>Found by</span><span v-for="p in item.paths" :key="p.id" class="pill"><i class="dot" :style="{background: path(p.id).color}"></i>{{ path(p.id).label }}</span></div>' +
      '<div class="why-row" v-if="pathRanks.length"><span v-for="p in pathRanks" :key="p.id" class="muted"><i class="dot" :style="{background: path(p.id).color}" style="margin-right:6px"></i>{{ path(p.id).label }}: position <span class="num">{{ p.rank }}</span></span></div>' +
      '<div class="why-row"><span>Position in the combined ranking: <strong class="num">{{ item.rank_before }}</strong></span><span>After re-ranking: <strong class="num">{{ item.rank_after }}</strong></span></div>' +
      '<p v-if="item.relation" class="muted">{{ item.relation }}</p></div></section>' +
      '</template></article>'
  };
})();
