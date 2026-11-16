(function () {
  var P = window.Reducer.PATHS;
  window.UIComponents.ResultCard = {
    props: { item: Object, cited: Boolean },
    emits: ["open"],
    methods: {
      cap(t) { return t ? t.charAt(0).toUpperCase() + t.slice(1) : ""; },
      path(id) { return P[id] || { label: "Search", color: "#9AA0B4" }; }
    },
    template: '<button type="button" class="result-card fade-in" @click="$emit(\'open\', item.id)">' +
      '<div class="rank num">{{ item.rank }}</div>' +
      '<div style="min-width:0">' +
      '<h3>{{ item.title }}</h3>' +
      '<div class="result-meta"><span v-if="item.organization">{{ item.organization }}</span>' +
      '<span v-if="item.type" class="pill">{{ cap(item.type) }}</span></div>' +
      '<p v-if="item.description" class="clamp2">{{ item.description }}</p>' +
      '<div v-if="item.tags && item.tags.length" class="tags"><span v-for="t in item.tags.slice(0, 5)" :key="t" class="tag">{{ t }}</span></div>' +
      '<div v-if="item.paths && item.paths.length" class="foundby"><span>Found by</span>' +
      '<span v-for="p in item.paths" :key="p.id" class="item"><i class="dot" :style="{background: path(p.id).color}"></i>{{ path(p.id).label }}</span></div>' +
      '</div>' +
      '<span v-if="cited" class="pill pill-accent cited-badge">Cited</span>' +
      '</button>'
  };
})();
