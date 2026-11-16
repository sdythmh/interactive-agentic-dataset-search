(function () {
  var C = window.UIComponents;
  C.ResultList = {
    props: { items: Array, cited: Object, live: Boolean, count: Number },
    emits: ["open"],
    components: { ResultCard: C.ResultCard },
    template: '<div>' +
      '<div class="list-head"><span v-if="items && items.length" class="muted">{{ items.length }} datasets, best match first</span><span v-else-if="live" class="muted">Searching\u2026</span></div>' +
      '<div v-if="items && items.length" class="result-list">' +
      '<ResultCard v-for="it in items" :key="it.id" :item="it" :cited="!!(cited && cited[it.id])" @open="$emit(\'open\', $event)" /></div>' +
      '<div v-else-if="live" class="result-list" aria-hidden="true"><div v-for="n in 4" :key="n" class="skeleton skeleton-card"></div></div>' +
      '<p v-else class="muted">No results were returned for this question.</p>' +
      '</div>'
  };
})();
